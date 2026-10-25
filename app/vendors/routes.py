from app.vendors.schemas import CouponUpdateRequest
from app.vendors.services import _get_vendor_coupon
from sqlalchemy.exc import IntegrityError
from app.vendors.services import _normalize_expiry
from app.vendors.services import _validate_discount
from app.vendors.schemas import CouponCreateRequest
from app.vendors.schemas import CouponResponse
from app.vendors.schemas import WalletResponse
from app.models import Wallet, WalletTransaction
from app.vendors.wallet_service import credit_wallets
from app.models.payment import VendorBanks
from app.vendors.schemas import BankResponse, BankCreationRequest, BankUpdateRequest
from app.vendors.schemas import VendorAnalyticResponse
from app.models.order import OrderItem
from app.models import OrderStatus
from app.vendors.schemas import VendorOrderStatusUpdate
from app.customers.schemas import OrderResponse
from app.core.security import CurrentUser
from app.models import Resturants, Menu, MenuOptionGroup, MenuOptionItem, Order, DeliveryAddress
from app.core.database import get_db
from app.core.response import api_response, ApiResponse
from fastapi import File, UploadFile, Query, APIRouter, Depends, status, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.limiter import limiter
from app.core.s3 import upload_image_to_s3
from app.vendors.services import verify_menu_ownership, verify_restaurant_ownership
from app.vendors.schemas import (
    PaginatedResturantsResponse,
    ResturantUpdateRequest,
    ResturantCreateRequest,
    ResturantResponse,
    MenuCreationRequest,
    MenuResponse,
    MenuUpdateRequest,
    PaginatedMenusResponse,
    MenuOptionGroupCreate,
    MenuOptionGroupUpdate,
    MenuOptionGroupResponse,
    MenuOptionItemCreate,
    MenuOptionItemUpdate,
    MenuOptionItemResponse,
)
from typing import Annotated
from app.core.security import RoleChecker
from app.models.user import UserRole, User
from app.models.coupon import Coupon
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from datetime import datetime, UTC

router = APIRouter()
require_vendor = RoleChecker(allowed_roles=[UserRole.VENDOR])


# get all restaurants
@router.get("", response_model=ApiResponse[PaginatedResturantsResponse])
async def get_restaurants(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10)] = 10
):
    count_result = await db.execute(
        select(func.count()).select_from(Resturants)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Resturants)
        .options(selectinload(Resturants.user))
        .order_by(Resturants.name)
        .offset(skip)
        .limit(limit)
    )

    restaurants = result.scalars().all()
    has_more = skip + len(restaurants) < total

    data = PaginatedResturantsResponse(
        restaurants=[ResturantResponse.model_validate(
            restaurant) for restaurant in restaurants],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more
    )
    return api_response(data=data, message="Restaurants fetched successfully")


# get my restaurants
@router.get('/me', response_model=ApiResponse[list[ResturantResponse]])
async def get_my_restaurants(
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Resturants)
        .options(selectinload(Resturants.user))
        .where(Resturants.user_id == current_user.id)
        .order_by(Resturants.name)
    )

    restaurants = result.scalars().all()
    data = [ResturantResponse.model_validate(r) for r in restaurants]
    return api_response(data=data, message="Vendor restaurants fetched successfully")


# get single restaurant
@router.get('/{restaurant_id}', response_model=ApiResponse[ResturantResponse])
async def get_single_restaurant(
    restaurant_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    results = await db.execute(
        select(Resturants)
        .options(selectinload(Resturants.user))
        .where(Resturants.id == restaurant_id)
    )

    single_restaurant = results.scalars().first()

    if single_restaurant:
        return api_response(
            data=ResturantResponse.model_validate(single_restaurant),
            message="Restaurant details retrieved successfully"
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Restaurant not found"
    )


# update restaurant
@router.patch('/{restaurant_id}', response_model=ApiResponse[ResturantResponse])
async def update_restaurant(
    restaurant_id: int,
    restaurant_data: ResturantUpdateRequest,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    results = await db.execute(select(Resturants).where(Resturants.id == restaurant_id))
    restaurant = results.scalars().first()

    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )

    if restaurant.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not authorized to update this restaurant'
        )

    update_data = restaurant_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(restaurant, field, value)

    await db.commit()
    await db.refresh(restaurant, attribute_names=['user'])
    return api_response(
        data=ResturantResponse.model_validate(restaurant),
        message="Restaurant updated successfully"
    )


# create a new restaurant
@router.post("", response_model=ApiResponse[ResturantResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def create_restaurant(
    request: Request,
    restaurant_data: ResturantCreateRequest,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    new_restaurant = Resturants(
        name=restaurant_data.name,
        description=restaurant_data.description,
        address=restaurant_data.address,
        phone=restaurant_data.phone,
        email=restaurant_data.email,
        website=restaurant_data.website,
        logo=restaurant_data.logo,
        cover_image=restaurant_data.cover_image,
        user_id=current_user.id
    )

    db.add(new_restaurant)
    await db.commit()
    await db.refresh(new_restaurant, attribute_names=['user'])

    return api_response(
        data=ResturantResponse.model_validate(new_restaurant),
        message="Restaurant created successfully"
    )


# delete restaurant
@router.delete('/{restaurant_id}', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def delete_restaurant(
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Resturants).where(Resturants.id == restaurant_id))
    restaurant = result.scalars().first()

    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )

    if restaurant.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not Authorized to delete this restaurant"
        )

    await db.delete(restaurant)
    await db.commit()

    return api_response(message="Restaurant deleted successfully")


# upload restaurant logo
@router.post('/{restaurant_id}/upload-logo', response_model=ApiResponse[ResturantResponse])
@limiter.limit("10/minute")
async def upload_restaurant_logo(
    request: Request,
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...)
):
    restaurant = await verify_restaurant_ownership(restaurant_id, current_user.id, db)
    image_url = await upload_image_to_s3(file, folder="restaurants/logos")
    restaurant.logo = image_url
    await db.commit()
    await db.refresh(restaurant, attribute_names=['user'])
    return api_response(
        data=ResturantResponse.model_validate(restaurant),
        message="Restaurant logo uploaded successfully"
    )


# upload restaurant cover image
@router.post('/{restaurant_id}/upload-cover', response_model=ApiResponse[ResturantResponse])
@limiter.limit("10/minute")
async def upload_restaurant_cover(
    request: Request,
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...)
):
    restaurant = await verify_restaurant_ownership(restaurant_id, current_user.id, db)
    image_url = await upload_image_to_s3(file, folder="restaurants/covers")
    restaurant.cover_image = image_url
    await db.commit()
    await db.refresh(restaurant, attribute_names=['user'])
    return api_response(
        data=ResturantResponse.model_validate(restaurant),
        message="Restaurant cover image uploaded successfully"
    )


# create menu
@router.post('/{restaurant_id}/menus', response_model=ApiResponse[MenuResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def create_menu(
    request: Request,
    restaurant_id: int,
    menu_data: MenuCreationRequest,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Resturants).where(Resturants.id == restaurant_id))
    restaurant = result.scalars().first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )
    if restaurant.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not authorized to create menu for this restaurant'
        )
    new_menu = Menu(
        name=menu_data.name,
        description=menu_data.description,
        price=menu_data.price,
        image_url=menu_data.image_url,
        category=menu_data.category,
        is_available=menu_data.is_available,
        is_vegetarian=menu_data.is_vegetarian,
        is_spicy=menu_data.is_spicy,
        prep_time_minutes=menu_data.prep_time_minutes,
        restaurant_id=restaurant_id
    )
    db.add(new_menu)
    await db.flush()

    for group_data in menu_data.option_groups:
        new_group = MenuOptionGroup(
            menu_id=new_menu.id,
            name=group_data.name,
            is_required=group_data.is_required,
            min_selectable=group_data.min_selectable,
            max_selectable=group_data.max_selectable,
        )
        db.add(new_group)
        await db.flush()

        for item_data in group_data.options:
            new_item = MenuOptionItem(
                menu_group_id=new_group.id,
                name=item_data.name,
                additional_price=item_data.additional_price,
                is_available=item_data.is_available,
            )
            db.add(new_item)

    await db.commit()

    created_menu_result = await db.execute(
        select(Menu)
        .options(selectinload(Menu.menus_options).selectinload(MenuOptionGroup.options))
        .where(Menu.id == new_menu.id)
    )
    created_menu = created_menu_result.scalars().first()
    return api_response(
        data=MenuResponse.model_validate(created_menu),
        message="Menu created successfully"
    )


# update menu
@router.patch('/{restaurant_id}/menus/{menu_id}', response_model=ApiResponse[MenuResponse])
async def update_menu(
    restaurant_id: int,
    menu_id: int,
    menu_data: MenuUpdateRequest,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Resturants).where(Resturants.id == restaurant_id))
    restaurant = result.scalars().first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )
    if restaurant.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not authorized to update menu for this restaurant'
        )
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    menu = result.scalars().first()
    if not menu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Menu not found'
        )
    if menu.restaurant_id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not authorized to update this menu'
        )
    update_data = menu_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(menu, field, value)
    await db.commit()

    refreshed_result = await db.execute(
        select(Menu)
        .options(selectinload(Menu.menus_options).selectinload(MenuOptionGroup.options))
        .where(Menu.id == menu_id)
    )
    updated_menu = refreshed_result.scalars().first()
    return api_response(
        data=MenuResponse.model_validate(updated_menu),
        message="Menu updated successfully"
    )


# upload menu item image
@router.post('/{restaurant_id}/menus/{menu_id}/upload-image', response_model=ApiResponse[MenuResponse])
@limiter.limit("10/minute")
async def upload_menu_image(
    request: Request,
    restaurant_id: int,
    menu_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...)
):
    menu = await verify_menu_ownership(restaurant_id, menu_id, current_user.id, db)
    image_url = await upload_image_to_s3(file, folder="menus")
    menu.image_url = image_url
    await db.commit()

    refreshed_result = await db.execute(
        select(Menu)
        .options(selectinload(Menu.menus_options).selectinload(MenuOptionGroup.options))
        .where(Menu.id == menu_id)
    )
    updated_menu = refreshed_result.scalars().first()
    return api_response(
        data=MenuResponse.model_validate(updated_menu),
        message="Menu image uploaded successfully"
    )


# get all menus for a restaurant
@router.get('/{restaurant_id}/menus', response_model=ApiResponse[PaginatedMenusResponse])
async def get_all_menus(
    restaurant_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10)] = 10
):
    count_result = await db.execute(
        select(func.count()).select_from(Menu).where(
            Menu.restaurant_id == restaurant_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Menu)
        .options(selectinload(Menu.menus_options).selectinload(MenuOptionGroup.options))
        .where(Menu.restaurant_id == restaurant_id)
        .order_by(Menu.name)
        .offset(skip)
        .limit(limit)
    )
    menus = result.scalars().all()
    has_more = skip + len(menus) < total

    data = PaginatedMenusResponse(
        menus=[MenuResponse.model_validate(menu) for menu in menus],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more
    )
    return api_response(data=data, message="Menus fetched successfully")


# get single menu
@router.get('/{restaurant_id}/menus/{menu_id}', response_model=ApiResponse[MenuResponse])
async def get_single_menu(
    restaurant_id: int,
    menu_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    results = await db.execute(
        select(Menu)
        .options(selectinload(Menu.menus_options).selectinload(MenuOptionGroup.options))
        .where(Menu.id == menu_id)
        .where(Menu.restaurant_id == restaurant_id)
    )

    single_menu = results.scalars().first()

    if single_menu:
        return api_response(
            data=MenuResponse.model_validate(single_menu),
            message="Menu details retrieved successfully"
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Menu not found"
    )


# delete menu
@router.delete('/{restaurant_id}/menus/{menu_id}', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def delete_menu(
    restaurant_id: int,
    menu_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    menu = result.scalars().first()

    if not menu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Menu not found'
        )

    if menu.restaurant_id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not Authorized to delete this menu"
        )

    await db.delete(menu)
    await db.commit()

    return api_response(message="Menu deleted successfully")


# --- Menu Option Group Endpoints ---

@router.post('/{restaurant_id}/menus/{menu_id}/groups', response_model=ApiResponse[MenuOptionGroupResponse], status_code=status.HTTP_201_CREATED)
async def create_option_group(
    restaurant_id: int,
    menu_id: int,
    group_data: MenuOptionGroupCreate,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_menu_ownership(restaurant_id, menu_id, current_user.id, db)

    new_group = MenuOptionGroup(
        menu_id=menu_id,
        name=group_data.name,
        is_required=group_data.is_required,
        min_selectable=group_data.min_selectable,
        max_selectable=group_data.max_selectable,
    )
    db.add(new_group)
    await db.flush()

    for item_data in group_data.options:
        new_item = MenuOptionItem(
            menu_group_id=new_group.id,
            name=item_data.name,
            additional_price=item_data.additional_price,
            is_available=item_data.is_available,
        )
        db.add(new_item)

    await db.commit()

    result = await db.execute(
        select(MenuOptionGroup)
        .options(selectinload(MenuOptionGroup.options))
        .where(MenuOptionGroup.id == new_group.id)
    )
    created_group = result.scalars().first()
    return api_response(
        data=MenuOptionGroupResponse.model_validate(created_group),
        message="Option group created successfully"
    )


@router.patch('/{restaurant_id}/menus/{menu_id}/groups/{group_id}', response_model=ApiResponse[MenuOptionGroupResponse])
async def update_option_group(
    restaurant_id: int,
    menu_id: int,
    group_id: int,
    group_data: MenuOptionGroupUpdate,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_menu_ownership(restaurant_id, menu_id, current_user.id, db)

    result = await db.execute(select(MenuOptionGroup).where(MenuOptionGroup.id == group_id))
    group = result.scalars().first()
    if not group or group.menu_id != menu_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Option group not found for this menu'
        )

    update_dict = group_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(group, field, value)

    await db.commit()

    refreshed = await db.execute(
        select(MenuOptionGroup)
        .options(selectinload(MenuOptionGroup.options))
        .where(MenuOptionGroup.id == group_id)
    )
    updated_group = refreshed.scalars().first()
    return api_response(
        data=MenuOptionGroupResponse.model_validate(updated_group),
        message="Option group updated successfully"
    )


@router.delete('/{restaurant_id}/menus/{menu_id}/groups/{group_id}', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def delete_option_group(
    restaurant_id: int,
    menu_id: int,
    group_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_menu_ownership(restaurant_id, menu_id, current_user.id, db)

    result = await db.execute(select(MenuOptionGroup).where(MenuOptionGroup.id == group_id))
    group = result.scalars().first()
    if not group or group.menu_id != menu_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Option group not found for this menu'
        )

    await db.delete(group)
    await db.commit()
    return api_response(message="Option group deleted successfully")


# --- Menu Option Item Endpoints ---

@router.post('/{restaurant_id}/menus/{menu_id}/groups/{group_id}/items', response_model=ApiResponse[MenuOptionItemResponse], status_code=status.HTTP_201_CREATED)
async def create_option_item(
    restaurant_id: int,
    menu_id: int,
    group_id: int,
    item_data: MenuOptionItemCreate,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_menu_ownership(restaurant_id, menu_id, current_user.id, db)

    res_group = await db.execute(select(MenuOptionGroup).where(MenuOptionGroup.id == group_id))
    group = res_group.scalars().first()
    if not group or group.menu_id != menu_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Option group not found'
        )

    new_item = MenuOptionItem(
        menu_group_id=group_id,
        name=item_data.name,
        additional_price=item_data.additional_price,
        is_available=item_data.is_available,
    )
    db.add(new_item)
    await db.commit()
    await db.refresh(new_item)
    return api_response(
        data=MenuOptionItemResponse.model_validate(new_item),
        message="Option item created successfully"
    )


@router.patch('/{restaurant_id}/menus/{menu_id}/groups/{group_id}/items/{item_id}', response_model=ApiResponse[MenuOptionItemResponse])
async def update_option_item(
    restaurant_id: int,
    menu_id: int,
    group_id: int,
    item_id: int,
    item_data: MenuOptionItemUpdate,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_menu_ownership(restaurant_id, menu_id, current_user.id, db)

    res_group = await db.execute(select(MenuOptionGroup).where(MenuOptionGroup.id == group_id))
    group = res_group.scalars().first()
    if not group or group.menu_id != menu_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Option group not found'
        )

    res_item = await db.execute(select(MenuOptionItem).where(MenuOptionItem.id == item_id))
    item = res_item.scalars().first()
    if not item or item.menu_group_id != group_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Option item not found for this group'
        )

    update_dict = item_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item)
    return api_response(
        data=MenuOptionItemResponse.model_validate(item),
        message="Option item updated successfully"
    )


@router.delete('/{restaurant_id}/menus/{menu_id}/groups/{group_id}/items/{item_id}', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def delete_option_item(
    restaurant_id: int,
    menu_id: int,
    group_id: int,
    item_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_menu_ownership(restaurant_id, menu_id, current_user.id, db)

    res_group = await db.execute(select(MenuOptionGroup).where(MenuOptionGroup.id == group_id))
    group = res_group.scalars().first()
    if not group or group.menu_id != menu_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Option group not found'
        )

    res_item = await db.execute(select(MenuOptionItem).where(MenuOptionItem.id == item_id))
    item = res_item.scalars().first()
    if not item or item.menu_group_id != group_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Option item not found for this group'
        )

    await db.delete(item)
    await db.commit()
    return api_response(message="Option item deleted successfully")


@router.patch("/orders/{order_id}/status", response_model=ApiResponse[OrderResponse])
async def vendor_update_status_order(
    order_id: int,
    status_data: VendorOrderStatusUpdate,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.items)
            .selectinload(OrderItem.menu)
            .selectinload(Menu.menus_options)
            .selectinload(MenuOptionGroup.options),
            selectinload(Order.delivery_address)
            .selectinload(DeliveryAddress.user)
        )
        .join(Resturants, Resturants.id == Order.restaurant_id)
        .where(Order.id == order_id, Resturants.user_id == current_user.id)
    )

    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Order not found for your restaurant'
        )

    if status_data.status == OrderStatus.PREPARING and order.status != OrderStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only start preparing orders that are PAID"
        )

    if status_data.status == OrderStatus.READY_FOR_PICKUP and order.status != OrderStatus.PREPARING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order must be in PREPARING status before marking READY FOR PICKUP"
        )

    if status_data.status == OrderStatus.DELIVERED:
        await credit_wallets(order, db)

    order.status = status_data.status
    await db.commit()

    # Re-query with full eager loads so Pydantic can serialize the menu chain
    refreshed = await db.execute(
        select(Order)
        .options(
            selectinload(Order.items)
            .selectinload(OrderItem.menu)
            .selectinload(Menu.menus_options)
            .selectinload(MenuOptionGroup.options),
            selectinload(Order.delivery_address)
            .selectinload(DeliveryAddress.user)
        )
        .where(Order.id == order_id)
    )
    order = refreshed.scalars().first()

    return api_response(
        data=OrderResponse.model_validate(order),
        message="Order status updated successfully"
    )


# Get vendor analytics
@router.get('/{restaurant_id}/analytics', response_model=ApiResponse[VendorAnalyticResponse])
async def get_vendor_analytics(
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    total = await db.execute(
        select(func.count(Order.id))
        .where(Order.restaurant_id == restaurant_id)
    )
    total_revenue = await db.execute(
        select(func.sum(Order.total_amount))
        .where(Order.restaurant_id == restaurant_id)
    )
    pending_orders = await db.execute(
        select(func.count(Order.id))
        .where(Order.restaurant_id == restaurant_id, Order.status == OrderStatus.PENDING_PAYMENT)
    )
    active_orders = await db.execute(
        select(func.count(Order.id))
        .where(Order.restaurant_id == restaurant_id, Order.status.in_([OrderStatus.PAID, OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP]))
    )
    completed_orders = await db.execute(
        select(func.count(Order.id))
        .where(Order.restaurant_id == restaurant_id, Order.status == OrderStatus.DELIVERED)
    )
    cancelled_orders = await db.execute(
        select(func.count(Order.id))
        .where(Order.restaurant_id == restaurant_id, Order.status == OrderStatus.CANCELLED)
    )
    top_selling_items = await db.execute(
        select(
            Menu.name,
            func.sum(OrderItem.quantity).label('total_quantity_sold')
        )
        .join(OrderItem, OrderItem.menu_id == Menu.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.restaurant_id == restaurant_id)
        .group_by(Menu.name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(5)
    )

    total_val = total.scalar() or 0
    total_rev_val = float(total_revenue.scalar() or 0.0)
    pending_val = pending_orders.scalar() or 0
    active_val = active_orders.scalar() or 0
    completed_val = completed_orders.scalar() or 0
    cancelled_val = cancelled_orders.scalar() or 0
    top_selling_val = [row.name for row in top_selling_items.all()]

    analytics_data = VendorAnalyticResponse(
        total_orders=total_val,
        total_revenue=total_rev_val,
        pending_orders=pending_val,
        active_orders=active_val,
        completed_orders=completed_val,
        cancelled_orders=cancelled_val,
        top_selling_items=top_selling_val
    )

    return api_response(
        data=analytics_data,
        message="Vendor analytics retrieved successfully"
    )


@router.get('/{restaurant_id}/wallet', response_model=ApiResponse[WalletResponse])
async def get_wallet(
    current_user: Annotated[User, Depends(require_vendor)],
    restaurant_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    result = await db.execute(
        select(Wallet)
        .options(selectinload(Wallet.wallet_transactions))
        .where(Wallet.user_id == current_user.id)
    )
    wallet = result.scalars().first()

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Wallet not found'
        )

    return api_response(
        data=WalletResponse.model_validate(wallet),
        message='Wallet fetched successfully'
    )


@router.get('/{restaurant_id}/bank', response_model=ApiResponse[BankResponse])
async def get_bank(
    current_user: Annotated[User, Depends(require_vendor)],
    restaurant_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    result = await db.execute(
        select(VendorBanks)
        .where(VendorBanks.restaurant_id == restaurant_id)
    )
    bank = result.scalars().first()

    if not bank:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bank details not found"
        )

    return api_response(
        data=BankResponse.model_validate(bank),
        message='Bank details fetched successfully'
    )


@router.post('/{restaurant_id}/bank', response_model=ApiResponse[BankResponse])
async def add_bank(
    current_user: Annotated[User, Depends(require_vendor)],
    restaurant_id: int,
    bank_request: BankCreationRequest,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    new_bank = VendorBanks(
        user_id=current_user.id,
        restaurant_id=restaurant_id,
        bank_name=bank_request.bank_name,
        account_number=bank_request.account_number,
        account_name=bank_request.account_name
    )

    db.add(new_bank)
    await db.commit()
    await db.refresh(new_bank)

    return api_response(
        data=BankResponse.model_validate(new_bank),
        message='Bank Details Added Successfully'
    )


@router.patch('/{restaurant_id}/bank', response_model=ApiResponse[BankResponse])
async def edit_bank(
    current_user: Annotated[User, Depends(require_vendor)],
    restaurant_id: int,
    bank_request: BankUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    result = await db.execute(
        select(VendorBanks)
        .where(VendorBanks.restaurant_id == restaurant_id)
    )
    bank = result.scalars().first()

    if not bank:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bank details not found"
        )

    # Only update fields that were actually sent in the request
    update_data = bank_request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(bank, field, value)

    await db.commit()
    await db.refresh(bank)

    return api_response(
        data=BankResponse.model_validate(bank),
        message='Bank details updated successfully'
    )


@router.delete('/{restaurant_id}/bank', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def delete_bank(
    current_user: Annotated[User, Depends(require_vendor)],
    restaurant_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    result = await db.execute(
        select(VendorBanks)
        .where(VendorBanks.restaurant_id == restaurant_id)
    )
    bank = result.scalars().first()

    if not bank:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bank details not found"
        )

    await db.delete(bank)
    await db.commit()

    return api_response(message='Bank details deleted successfully')

# create coupon


@router.post(
    '/{restaurant_id}/coupons',
    response_model=ApiResponse[CouponResponse],
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def create_coupon(
    request: Request,
    restaurant_id: int,
    coupon_data: CouponCreateRequest,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    dtype = _validate_discount(
        coupon_data.discount_type, coupon_data.discount_value)
    expires_at = _normalize_expiry(coupon_data.expires_at)
    if expires_at and expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="expires_at must be in the future",
        )

    code = coupon_data.code.strip().upper()

    existing = await db.execute(
        select(Coupon.id).where(
            Coupon.restaurant_id == restaurant_id, Coupon.code == code
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A coupon with this code already exists for this restaurant",
        )

    new_coupon = Coupon(
        restaurant_id=restaurant_id,
        code=code,
        discount_type=dtype,
        discount_value=coupon_data.discount_value,
        min_order_amount=coupon_data.min_order_amount,
        max_uses=coupon_data.max_uses,
        expires_at=expires_at,
    )
    db.add(new_coupon)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A coupon with this code already exists for this restaurant",
        )
    await db.refresh(new_coupon)

    return api_response(
        data=CouponResponse.model_validate(new_coupon),
        message="Coupon created successfully",
    )

# list coupons for a restaurant


@router.get('/{restaurant_id}/coupons', response_model=ApiResponse[list[CouponResponse]])
async def get_coupons(
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    is_active: Annotated[bool | None, Query()] = None,
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)

    query = select(Coupon).where(Coupon.restaurant_id == restaurant_id)
    if is_active is not None:
        query = query.where(Coupon.is_active.is_(is_active))
    result = await db.execute(query.order_by(Coupon.created_at.desc()))

    data = [CouponResponse.model_validate(c) for c in result.scalars().all()]
    return api_response(data=data, message="Coupons fetched successfully")


# get single coupon
@router.get('/{restaurant_id}/coupons/{coupon_id}', response_model=ApiResponse[CouponResponse])
async def get_single_coupon(
    restaurant_id: int,
    coupon_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)
    coupon = await _get_vendor_coupon(restaurant_id, coupon_id, db)
    return api_response(
        data=CouponResponse.model_validate(coupon),
        message="Coupon fetched successfully",
    )


# update coupon (code itself is immutable; deactivate + create a new one instead)
@router.patch('/{restaurant_id}/coupons/{coupon_id}', response_model=ApiResponse[CouponResponse])
async def update_coupon(
    restaurant_id: int,
    coupon_id: int,
    coupon_data: CouponUpdateRequest,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)
    coupon = await _get_vendor_coupon(restaurant_id, coupon_id, db)

    update_data = coupon_data.model_dump(exclude_unset=True)

    # Validate the resulting type/value combination, not just the incoming fields
    new_type = update_data.get("discount_type", coupon.discount_type.value)
    new_value = update_data.get("discount_value", coupon.discount_value)
    dtype = _validate_discount(new_type, new_value)
    if "discount_type" in update_data:
        update_data["discount_type"] = dtype

    if "expires_at" in update_data:
        update_data["expires_at"] = _normalize_expiry(
            update_data["expires_at"])

    new_max_uses = update_data.get("max_uses", coupon.max_uses)
    if new_max_uses is not None and new_max_uses < coupon.used_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"max_uses cannot be lower than the current used_count ({coupon.used_count})",
        )

    for field, value in update_data.items():
        setattr(coupon, field, value)

    await db.commit()
    await db.refresh(coupon)

    return api_response(
        data=CouponResponse.model_validate(coupon),
        message="Coupon updated successfully",
    )


# delete coupon
@router.delete(
    '/{restaurant_id}/coupons/{coupon_id}',
    response_model=ApiResponse[None],
    status_code=status.HTTP_200_OK,
)
async def delete_coupon(
    restaurant_id: int,
    coupon_id: int,
    current_user: Annotated[User, Depends(require_vendor)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await verify_restaurant_ownership(restaurant_id, current_user.id, db)
    coupon = await _get_vendor_coupon(restaurant_id, coupon_id, db)

    await db.delete(coupon)
    await db.commit()
    return api_response(message="Coupon deleted successfully")
