import logging
from app.models.cart import CartItem, Cart
from app.customers.schemas import CartResponse, CartItemAddRequest
from app.models.resturant import Reviews
from app.customers.schemas import ReviewRequest, ReviewResponse, ReviewCreatedResponse
from app.core.limiter import limiter
from sqlalchemy import func
from app.core.security import CurrentUser
from app.core.config import settings
import hashlib
import hmac
from app.customers.services import initialize_paystack_payment, verify_paystack_payment
from uuid import uuid4
from app.customers.schemas import DeliveryAddressResponse, DeliveryAddressRequest, DeliveryAddressCreatedResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.response import api_response, ApiResponse
from sqlalchemy.util.typing import Annotated
from app.vendors.schemas import PaginatedResturantsResponse, ResturantResponse
from fastapi import APIRouter, Depends, Request, Query, status, HTTPException
from app.core.security import RoleChecker
from app.models import Resturants, Menu, MenuOptionGroup, MenuOptionItem, Bookmark, DeliveryAddress, Order, OrderItem, PaymentTransaction, PaymentStatus, OrderStatus
from app.models.user import UserRole, User
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from app.customers.schemas import CategoryItemResponse, OrderResponse, OrderCreateRequest, OrderCreatedCheckoutResponse
from app.core.redis import get_redis
import redis.asyncio as redis


router = APIRouter()
logger = logging.getLogger(__name__)
require_customer = RoleChecker(allowed_roles=[UserRole.CUSTOMER])

CUSTOMER_CACHE_TTL = 120


# get all restaurants with search
@router.get("/restaurants", response_model=ApiResponse[PaginatedResturantsResponse])
@limiter.limit("30/minute")
async def get_all_restaurants(
    request: Request,
    current_user: Annotated[User, Depends(require_customer)],
    r: Annotated[redis.Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
    search: Annotated[str | None, Query(max_length=100)] = None,
):
    filters = []
    search_term = (search or "").strip()

    # Key must include every param that changes the result.
    # Search is lowercased (icontains is case-insensitive) and hashed to keep keys clean.
    search_hash = hashlib.sha256(search_term.lower().encode()).hexdigest()[:16]
    cache_key = f"restaurants:list:{search_hash}:{skip}:{limit}"

    try:
        cached = await r.get(cache_key)
        if cached:
            data = PaginatedResturantsResponse.model_validate_json(cached)
            return api_response(data=data, message="Restaurants fetched successfully")
    except Exception:
        logger.warning("Redis read failed for key %s",
                       cache_key, exc_info=True)

    if search_term:
        filters.append(
            Resturants.name.icontains(search_term, autoescape=True)
        )

    count_result = await db.execute(
        select(func.count())
        .select_from(Resturants)
        .where(*filters)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Resturants)
        .where(*filters)
        .options(selectinload(Resturants.user))
        .order_by(Resturants.name)
        .offset(skip)
        .limit(limit)
    )

    restaurants = result.scalars().all()
    has_more = skip + len(restaurants) < total

    data = PaginatedResturantsResponse(
        restaurants=[
            ResturantResponse.model_validate(restaurant)
            for restaurant in restaurants
        ],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more,
    )

    try:
        await r.set(cache_key, data.model_dump_json(), ex=CUSTOMER_CACHE_TTL)
    except Exception:
        logger.warning("Redis write failed for key %s",
                       cache_key, exc_info=True)

    return api_response(data=data, message="Restaurants fetched successfully")


# get restaurant profile
@router.get('/resturants/{restaurant_id}', response_model=ApiResponse[ResturantResponse])
async def get_single_restaurant(
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    r: Annotated[redis.Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    cache_key = f"restaurant:{restaurant_id}"

    # 1. Try the cache first
    try:
        cached = await r.get(cache_key)
        if cached:
            data = ResturantResponse.model_validate_json(cached)
            return api_response(
                data=data,
                message="Restaurant details retrieved successfully"
            )
    except Exception:
        logger.warning("Redis read failed for key %s", cache_key, exc_info=True)

    # 2. Cache miss: query the DB
    results = await db.execute(
        select(Resturants)
        .options(selectinload(Resturants.user))
        .where(Resturants.id == restaurant_id)
    )
    single_restaurant = results.scalars().first()

    if not single_restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found"
        )

    data = ResturantResponse.model_validate(single_restaurant)

    # 3. Store in the cache, then return
    try:
        await r.set(cache_key, data.model_dump_json(), ex=CUSTOMER_CACHE_TTL)
    except Exception:
        logger.warning("Redis write failed for key %s", cache_key, exc_info=True)

    return api_response(
        data=data,
        message="Restaurant details retrieved successfully"
    )

# top food categories
@router.get("/top-categories", response_model=ApiResponse[list[CategoryItemResponse]])
async def get_top_categories(
    current_user: Annotated[User, Depends(require_customer)],
    r: Annotated[redis.Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):

    cache_key = f"top-categories:{limit}"

    try:
        cached = await r.get(cache_key)
        if cached:
            categories = CategoryItemResponse.model_validate_json(cached)
            return api_response(data=categories, message="Top categories fetched successfully")
    except Exception:
        logger.warning("Redis read failed for key %s",
                       cache_key, exc_info=True)

    result = await db.execute(
        select(
            Menu.category,
            func.count(Menu.id).label("total_items")
        )
        .where(
            Menu.category.isnot(None),
            Menu.category != "",
            Menu.is_available.is_(True)
        )
        .group_by(Menu.category)
        .order_by(func.count(Menu.id).desc())
        .limit(limit)
    )

    categories = [
        CategoryItemResponse(category=row.category,
                             total_items=row.total_items)
        for row in result.all()
    ]

    try:
        await r.set(cache_key, categories.model_dump_json(), ex=CUSTOMER_CACHE_TTL)
    except Exception:
        logger.warning("Redis write failed for key %s",
                       cache_key, exc_info=True)

    return api_response(data=categories, message="Top categories fetched successfully")


# get all delivery addresses
@router.get("/delivery-addresses", response_model=ApiResponse[list[DeliveryAddressResponse]])
async def get_delivery_addresses(
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(DeliveryAddress)
        .where(DeliveryAddress.user_id == current_user.id)
        .order_by(
            DeliveryAddress.is_primary.desc(),
            DeliveryAddress.id,
        )
    )

    delivery_addresses = result.scalars().all()
    data = [DeliveryAddressResponse.model_validate(
        a) for a in delivery_addresses]
    return api_response(data=data, message="Delivery addresses fetched successfully")


# save a new delivery address
@router.post("/delivery-address", response_model=ApiResponse[DeliveryAddressResponse], status_code=status.HTTP_201_CREATED)
async def save_delivery_address(
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)],
    address: DeliveryAddressRequest
):
    if address.is_primary:
        await db.execute(
            update(DeliveryAddress)
            .where(
                DeliveryAddress.user_id == current_user.id,
                DeliveryAddress.is_primary.is_(True)
            )
            .values(is_primary=False)
        )

    new_address = DeliveryAddress(
        user_id=current_user.id,
        address=address.address,
        is_primary=address.is_primary
    )

    db.add(new_address)
    await db.commit()
    await db.refresh(new_address)

    return api_response(
        data=DeliveryAddressResponse.model_validate(new_address),
        message="Delivery Address added successfully"
    )


# update a saved address
@router.put("/delivery-address/{address_id}", response_model=ApiResponse[DeliveryAddressResponse])
async def update_delivery_address(
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)],
    address_id: int,
    address_data: DeliveryAddressRequest
):
    result = await db.execute(select(DeliveryAddress).where(DeliveryAddress.id == address_id))
    address = result.scalars().first()

    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )

    if address.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not authorized to update this address'
        )

    if address_data.is_primary and not address.is_primary:
        await db.execute(
            update(DeliveryAddress)
            .where(
                DeliveryAddress.user_id == current_user.id,
                DeliveryAddress.is_primary.is_(True),
                DeliveryAddress.id != address_id
            )
            .values(is_primary=False)
        )

    update_data = address_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(address, field, value)

    await db.commit()
    await db.refresh(address, attribute_names=['user'])
    return api_response(
        data=DeliveryAddressResponse.model_validate(address),
        message="Delivery Address updated successfully"
    )


@router.delete('/delivery-address/{address_id}', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def delete_delivery_address(
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)],
    address_id: int,
):
    result = await db.execute(select(DeliveryAddress).where(DeliveryAddress.id == address_id))
    address = result.scalars().first()

    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Address not found'
        )

    if address.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not Authorized to delete this address"
        )

    await db.delete(address)
    await db.commit()

    return api_response(message="Address deleted successfully")


# set primary default address
@router.patch("/delivery-address/{address_id}/primary", response_model=ApiResponse[DeliveryAddressResponse])
async def set_primary_delivery_address(
    address_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(DeliveryAddress).where(DeliveryAddress.id == address_id)
    )
    address = result.scalars().first()

    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )

    if address.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this delivery address"
        )

    await db.execute(
        update(DeliveryAddress)
        .where(
            DeliveryAddress.user_id == current_user.id,
            DeliveryAddress.is_primary.is_(True),
            DeliveryAddress.id != address_id
        )
        .values(is_primary=False)
    )

    address.is_primary = True
    await db.commit()
    await db.refresh(address, attribute_names=['user'])

    return api_response(
        data=DeliveryAddressResponse.model_validate(address),
        message="Primary delivery address set successfully"
    )


# list all bookmarked restaurants
@router.get('/bookmarks', response_model=ApiResponse[list[ResturantResponse]])
async def get_bookmarked_restaurants(
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Resturants)
        .join(Bookmark, Bookmark.restaurant_id == Resturants.id)
        .options(selectinload(Resturants.user))
        .where(Bookmark.user_id == current_user.id)
        .order_by(Bookmark.created_at.desc())
    )

    bookmarked_restaurants = result.scalars().all()
    data = [ResturantResponse.model_validate(
        r) for r in bookmarked_restaurants]

    return api_response(data=data, message="Bookmarked restaurants fetched successfully")


# bookmark restaurant
@router.post('/bookmarks/{restaurant_id}', response_model=ApiResponse[None], status_code=status.HTTP_201_CREATED)
async def bookmark_restaurant(
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    rest_result = await db.execute(select(Resturants).where(Resturants.id == restaurant_id))
    if not rest_result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found"
        )

    bm_result = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.restaurant_id == restaurant_id
        )
    )
    if bm_result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Restaurant is already bookmarked"
        )

    new_bookmark = Bookmark(user_id=current_user.id,
                            restaurant_id=restaurant_id)
    db.add(new_bookmark)
    await db.commit()
    return api_response(message="Restaurant bookmarked successfully")


# remove from bookmarks
@router.delete('/bookmarks/{restaurant_id}', response_model=ApiResponse[None])
async def remove_bookmark(
    restaurant_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    bm_result = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.restaurant_id == restaurant_id
        )
    )
    bookmark = bm_result.scalars().first()

    if not bookmark:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bookmark not found"
        )

    await db.delete(bookmark)
    await db.commit()

    return api_response(message="Restaurant removed from bookmarks")


# create order
@router.post('/orders', response_model=ApiResponse[OrderCreatedCheckoutResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_order(
    request: Request,
    order_data: OrderCreateRequest,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(DeliveryAddress)
        .where(DeliveryAddress.id == order_data.delivery_address_id, DeliveryAddress.user_id == current_user.id)
    )
    delivery_address = result.scalars().first()

    if not delivery_address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Delivery Address not found'
        )

    result = await db.execute(
        select(Resturants)
        .where(Resturants.id == order_data.restaurant_id)
    )
    restaurant = result.scalars().first()

    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )

    menu_ids = [item.menu_id for item in order_data.items]
    menus_result = await db.execute(
        select(Menu).where(
            Menu.id.in_(menu_ids),
            Menu.restaurant_id == order_data.restaurant_id,
            Menu.is_available.is_(True)
        )
    )
    db_menus = {menu.id: menu for menu in menus_result.scalars().all()}

    if len(db_menus) != len(set(menu_ids)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more selected menu items are unavailable"
        )

    all_option_ids = list({
        opt_id
        for item in order_data.items
        if item.selected_options
        for opt_id in item.selected_options
    })

    db_options_by_id = {}
    if all_option_ids:
        options_result = await db.execute(
            select(MenuOptionItem).where(
                MenuOptionItem.id.in_(all_option_ids),
                MenuOptionItem.is_available.is_(True)
            )
        )
        db_options_by_id = {
            opt.id: opt for opt in options_result.scalars().all()}

    subtotal = 0
    order_items_to_create = []

    for item in order_data.items:
        menu_item = db_menus[item.menu_id]

        options_total = sum(
            db_options_by_id[opt_id].additional_price
            for opt_id in (item.selected_options or [])
            if opt_id in db_options_by_id
        )

        unit_price = menu_item.price + options_total
        item_total = unit_price * item.quantity
        subtotal += item_total

        order_items_to_create.append({
            "menu_id": menu_item.id,
            "unit_price": unit_price,
            "quantity": item.quantity,
            "total_price": item_total,
            "selected_options": item.selected_options
        })

    delivery_fee = 1500
    service_fee = 500
    total_amount = subtotal + delivery_fee + service_fee
    order_number = f"ORD-{uuid4().hex[:8].upper()}"

    new_order = Order(
        order_number=order_number,
        customer_id=current_user.id,
        restaurant_id=restaurant.id,
        delivery_address_id=delivery_address.id,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        service_fee=service_fee,
        total_amount=total_amount,
        status=OrderStatus.PENDING_PAYMENT,
        payment_status=PaymentStatus.PENDING
    )

    db.add(new_order)
    await db.flush()

    for item_dict in order_items_to_create:
        db.add(OrderItem(order_id=new_order.id, **item_dict))

    await db.commit()
    await db.refresh(new_order)
    await db.refresh(new_order, attribute_names=["items", "delivery_address"])
    # Re-query to get items with full menu data
    refreshed_result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.items)
            .selectinload(OrderItem.menu)
            .selectinload(Menu.menus_options)
            .selectinload(MenuOptionGroup.options),
            selectinload(Order.delivery_address)
            .selectinload(DeliveryAddress.user)
        )
        .where(Order.id == new_order.id)
    )
    new_order = refreshed_result.scalars().first()

    payment_url = await initialize_paystack_payment(
        email=current_user.email,
        amount=total_amount,
        reference=order_number,
    )

    checkout_data = OrderCreatedCheckoutResponse(
        message="Order created successfully. Please complete payment.",
        order=OrderResponse.model_validate(new_order),
        payment_url=payment_url
    )

    return api_response(
        data=checkout_data,
        message="Order created successfully. Please complete payment."
    )


# paystack webhook
@router.post("/webhook/paystack", response_model=ApiResponse[None])
async def paystack_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    paystack_signature = request.headers.get("x-paystack-signature")
    body = await request.body()

    expected_signature = hmac.new(
        settings.paystack_secret_key.get_secret_value().encode('utf-8'),
        body,
        hashlib.sha512
    ).hexdigest()

    if paystack_signature != expected_signature:
        logger.warning("Paystack webhook signature mismatch from IP: %s",
                       request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    event_data = await request.json()
    event_type = event_data.get("event")
    logger.info("Received Paystack webhook event: %s", event_type)

    if event_type == "charge.success":
        reference = event_data["data"]["reference"]
        order_res = await db.execute(select(Order).where(Order.order_number == reference))
        order = order_res.scalars().first()

        if order:
            order.payment_status = PaymentStatus.SUCCESS
            order.status = OrderStatus.PAID
            await db.commit()
            logger.info(
                "Paystack webhook: Order %s updated to PAID", reference)
        else:
            logger.warning(
                "Paystack webhook: Received success event for non-existent order reference: %s", reference)

    return api_response(message="Webhook processed successfully")


# list all order history
@router.get("/orders", response_model=ApiResponse[list[OrderResponse]])
async def get_order_history(
    current_user: Annotated[User, Depends(require_customer)],
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
        .where(Order.customer_id == current_user.id)
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    data = [OrderResponse.model_validate(o) for o in orders]
    return api_response(data=data, message="Order history retrieved successfully")


# verify order payment with paystack
@router.get("/orders/verify/{reference}", response_model=ApiResponse[OrderResponse])
@limiter.limit("10/minute")
async def verify_order_payment(
    request: Request,
    reference: str,
    current_user: CurrentUser,
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
        .where(Order.order_number == reference, Order.customer_id == current_user.id)
    )
    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )

    if order.payment_status == PaymentStatus.SUCCESS:
        return api_response(data=OrderResponse.model_validate(order), message="Payment already verified")

    paystack_data = await verify_paystack_payment(reference)

    if paystack_data.get("status") == "success":
        order.payment_status = PaymentStatus.SUCCESS
        order.status = OrderStatus.PAID
        await db.commit()
        await db.refresh(order, attribute_names=["items", "delivery_address"])

    return api_response(data=OrderResponse.model_validate(order), message="Payment verification checked")


# get order details
@router.get("/orders/{order_id}", response_model=ApiResponse[OrderResponse])
async def get_order_details(
    order_id: int,
    current_user: CurrentUser,
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
        .where(Order.id == order_id, Order.customer_id == current_user.id)
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return api_response(data=OrderResponse.model_validate(order), message="Order details retrieved successfully")


# cancel an order before preparation
@router.post("/orders/{order_id}/cancel", response_model=ApiResponse[None])
@limiter.limit("5/minute")
async def cancel_order(
    request: Request,
    order_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Order).where(Order.id == order_id,
                            Order.customer_id == current_user.id)
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if order.status not in [OrderStatus.PENDING_PAYMENT, OrderStatus.PAID]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot cancel order once food preparation has started")

    order.status = OrderStatus.CANCELLED
    await db.commit()
    return api_response(message="Order cancelled successfully")


# rate restaurant and rider
@router.post("/orders/{order_id}/rate", response_model=ApiResponse[ReviewResponse])
@limiter.limit("5/minute")
async def rate_restaurant_and_rider(
    request: Request,
    order_id: int,
    rating_data: ReviewRequest,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.restaurant), selectinload(Order.rider))
        .where(Order.id == order_id, Order.customer_id == current_user.id)
    )
    user_order = result.scalars().first()

    if not user_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if user_order.status != OrderStatus.DELIVERED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot rate an order that has not been delivered")

    existing_review = await db.execute(
        select(Reviews).where(Reviews.order_id == user_order.id)
    )
    if existing_review.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order has already been reviewed"
        )

    review = Reviews(
        user_id=current_user.id,
        order_id=user_order.id,
        restaurant_id=user_order.restaurant_id,
        rider_id=user_order.rider_id,
        rating=rating_data.rating,
        comment=rating_data.comment,
        rider_rating=rating_data.rider_rating,
        rider_comment=rating_data.rider_comment,
    )

    db.add(review)
    await db.flush()

    stats_result = await db.execute(
        select(
            func.count(Reviews.id).label("total_reviews"),
            func.avg(Reviews.rating).label("avg_rating")
        )
        .where(Reviews.restaurant_id == user_order.restaurant_id)
    )
    stats = stats_result.first()
    total_reviews = stats.total_reviews or 0
    avg_rating = round(float(stats.avg_rating or 0.0), 2)

    restaurant_result = await db.execute(
        select(Resturants).where(Resturants.id == user_order.restaurant_id)
    )
    restaurant = restaurant_result.scalars().first()
    if restaurant:
        restaurant.review_count = total_reviews
        restaurant.rating = avg_rating

    await db.commit()

    refreshed_result = await db.execute(
        select(Reviews)
        .options(
            selectinload(Reviews.user),
            selectinload(Reviews.restaurant).selectinload(Resturants.user),
            selectinload(Reviews.order).options(
                selectinload(Order.items),
                selectinload(Order.delivery_address)
            )
        )
        .where(Reviews.id == review.id)
    )
    created_review = refreshed_result.scalars().first()

    return api_response(
        data=ReviewResponse.model_validate(created_review),
        message="Review added successfully!"
    )


# get all ratings and reviews for a restaurant
@router.get("/restaurants/{restaurant_id}/reviews", response_model=ApiResponse[list[ReviewResponse]])
async def get_restaurant_reviews(
    restaurant_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Reviews)
        .options(
            selectinload(Reviews.user),
            selectinload(Reviews.restaurant).selectinload(Resturants.user),
            selectinload(Reviews.order).options(
                selectinload(Order.items),
                selectinload(Order.delivery_address)
            )
        )
        .where(Reviews.restaurant_id == restaurant_id)
        .order_by(Reviews.created_at.desc())
    )
    reviews = result.scalars().all()
    data = [ReviewResponse.model_validate(r) for r in reviews]
    return api_response(data=data, message="Restaurant reviews fetched successfully")

# add to cart


@router.post('/{restaurant_id}/cart/{item_id}', response_model=ApiResponse[CartResponse], status_code=status.HTTP_200_OK)
async def add_to_cart(
    restaurant_id: int,
    item_id: int,
    item_data: CartItemAddRequest,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    # Validate restaurant exists
    result = await db.execute(
        select(Resturants).where(Resturants.id == restaurant_id)
    )
    restaurant = result.scalars().first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )

    # Check if menu item is available
    item_result = await db.execute(
        select(Menu)
        .where(Menu.id == item_id, Menu.restaurant_id == restaurant_id, Menu.is_available.is_(True))
    )
    menu = item_result.scalars().first()
    if not menu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Menu item not found or unavailable'
        )

    # Calculate unit price (base price + selected option prices)
    options_total = 0
    option_snapshots = []
    if item_data.selected_options:
        opts_result = await db.execute(
            select(MenuOptionItem).where(
                MenuOptionItem.id.in_(item_data.selected_options),
                MenuOptionItem.is_available.is_(True)
            )
        )
        db_options = {
            opt.id: opt
            for opt in opts_result.scalars().all()
        }
        for opt_id in item_data.selected_options:
            if opt_id in db_options:
                opt = db_options[opt_id]
                options_total += opt.additional_price
                option_snapshots.append({
                    "id": opt.id,
                    "name": opt.name,
                    "additional_price": opt.additional_price,
                })

    unit_price = menu.price + options_total

    # Get or create a cart for this user
    cart_result = await db.execute(
        select(Cart)
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.menu)
            .selectinload(Menu.menus_options)
            .selectinload(MenuOptionGroup.options),
            selectinload(Cart.restaurant)
        )
        .where(Cart.user_id == current_user.id)
    )
    cart = cart_result.scalars().first()

    if not cart:
        # First time adding to cart — create it
        cart = Cart(
            user_id=current_user.id,
            restaurant_id=restaurant_id,
            total_amount=0
        )
        db.add(cart)
        await db.flush()  # get cart.id
    elif cart.restaurant_id != restaurant_id:
        # Cart belongs to a different restaurant — clear it and start fresh
        await db.execute(
            select(CartItem).where(CartItem.cart_id == cart.id)
        )
        for existing_item in cart.items:
            await db.delete(existing_item)
        cart.restaurant_id = restaurant_id
        cart.total_amount = 0
        await db.flush()

    # Check if this menu item already exists in the cart
    existing_item_result = await db.execute(
        select(CartItem).where(
            CartItem.cart_id == cart.id,
            CartItem.menu_id == menu.id
        )
    )
    existing_item = existing_item_result.scalars().first()

    if existing_item:
        # Item already in cart — increment quantity and update prices
        existing_item.quantity += item_data.quantity
        existing_item.unit_price = unit_price
        existing_item.total_price = unit_price * existing_item.quantity
        existing_item.selected_options = option_snapshots
    else:
        # Add new cart item
        new_cart_item = CartItem(
            cart_id=cart.id,
            menu_id=menu.id,
            unit_price=unit_price,
            quantity=item_data.quantity,
            total_price=unit_price * item_data.quantity,
            selected_options=option_snapshots
        )
        db.add(new_cart_item)

    # Recalculate total_amount from all cart items
    await db.flush()
    items_result = await db.execute(
        select(CartItem).where(CartItem.cart_id == cart.id)
    )
    all_items = items_result.scalars().all()
    cart.total_amount = sum(i.total_price for i in all_items)

    await db.commit()

    # Re-fetch cart with all relationships loaded for serialization
    refreshed = await db.execute(
        select(Cart)
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.menu)
            .selectinload(Menu.menus_options)
            .selectinload(MenuOptionGroup.options),
            selectinload(Cart.restaurant)
        )
        .where(Cart.id == cart.id)
    )
    cart = refreshed.scalars().first()

    return api_response(
        data=CartResponse.model_validate(cart),
        message='Item added to cart successfully'
    )


# get cart
@router.get('/cart', response_model=ApiResponse[CartResponse | None])
async def get_cart(
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    # check if current user has a cart
    result = await db.execute(
        select(Cart)
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.menu)
            .selectinload(Menu.menus_options)
            .selectinload(MenuOptionGroup.options),
            selectinload(Cart.restaurant)
        )
        .where(Cart.user_id == current_user.id)
    )

    user_cart = result.scalars().first()

    if not user_cart:
        return api_response(
            message="Cart is empty"
        )

    return api_response(
        data=CartResponse.model_validate(user_cart),
        message="User Cart fetched successfully!"
    )

# clear cart


@router.delete('/cart/{cart_id}', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def clear_cart(
    cart_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart_id)
    )

    cart = result.scalars().first()

    if not cart:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Cart not found'
        )

    await db.delete(cart)
    await db.commit()

    return api_response(message="Cart cleared successfully")

# remove from cart


@router.delete('/cart/{item_id}', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def remove_from_cart(
    item_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(CartItem)
        .where(CartItem.id == item_id)
    )

    cart_item = result.scalars().first()

    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Cart item not found'
        )

    await db.delete(cart_item)
    await db.commit()

    return api_response(message="Cart item removed successfully")
