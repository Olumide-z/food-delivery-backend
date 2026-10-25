from app.vendors.wallet_service import credit_wallets
from app.vendors.schemas import WalletResponse
from app.models import Resturants, Menu, MenuOptionGroup, DeliveryAddress, Wallet
from app.models.order import Order, OrderStatus, OrderItem
from app.riders.schemas import DeliveryUpdate
from fastapi import Query, APIRouter, Depends, status, HTTPException, Request
from app.core.limiter import limiter
from sqlalchemy.util.typing import Annotated
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.response import api_response, ApiResponse
from app.models.user import UserRole, User
from app.core.security import RoleChecker
from app.customers.schemas import OrderResponse


router = APIRouter()
require_rider = RoleChecker(allowed_roles=[UserRole.RIDER])

# Reusable selectinload options for full OrderResponse serialization
ORDER_LOAD_OPTIONS = [
    selectinload(Order.items)
    .selectinload(OrderItem.menu)
    .selectinload(Menu.menus_options)
    .selectinload(MenuOptionGroup.options),
    selectinload(Order.delivery_address)
    .selectinload(DeliveryAddress.user),
]


# get active trip details
@router.get('/orders/active', response_model=ApiResponse[list[OrderResponse]])
async def get_active_orders(
    current_user: Annotated[User, Depends(require_rider)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    result = await db.execute(
        select(Order)
        .options(*ORDER_LOAD_OPTIONS)
        .where(
            Order.status != OrderStatus.DELIVERED,
            Order.rider_id == current_user.id
        )
        .order_by(Order.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    active_orders = result.scalars().all()
    data = [OrderResponse.model_validate(o) for o in active_orders]
    return api_response(data=data, message="Active orders retrieved successfully")


# available orders for delivery
@router.get('/orders/available', response_model=ApiResponse[list[OrderResponse]])
async def get_available_orders(
    current_user: Annotated[User, Depends(require_rider)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    result = await db.execute(
        select(Order)
        .options(*ORDER_LOAD_OPTIONS)
        .where(
            Order.status == OrderStatus.READY_FOR_PICKUP,
            Order.rider_id.is_(None)
        )
        .order_by(Order.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    available_orders = result.scalars().all()
    data = [OrderResponse.model_validate(o) for o in available_orders]
    return api_response(data=data, message="Available delivery orders retrieved successfully")


# accept an order
@router.post("/orders/{order_id}/accept", response_model=ApiResponse[OrderResponse])
@limiter.limit("10/minute")
async def accept_delivery_order(
    request: Request,
    order_id: int,
    current_user: Annotated[User, Depends(require_rider)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Order)
        .options(*ORDER_LOAD_OPTIONS)
        .where(Order.id == order_id)
    )
    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if order.status != OrderStatus.READY_FOR_PICKUP and order.status != OrderStatus.RIDER_GOING_TO_VENDOR:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Order is not ready for pickup")

    if order.rider_id is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Order has already been accepted by another rider")

    # Assign rider
    order.rider_id = current_user.id
    order.status = OrderStatus.RIDER_GOING_TO_VENDOR
    await db.commit()

    # Re-query with full eager loads after commit
    refreshed = await db.execute(
        select(Order).options(*ORDER_LOAD_OPTIONS).where(Order.id == order_id)
    )
    order = refreshed.scalars().first()

    return api_response(
        data=OrderResponse.model_validate(order),
        message="Delivery order accepted successfully"
    )


# update delivery status
@router.patch("/orders/{order_id}/status", response_model=ApiResponse[OrderResponse])
@limiter.limit("20/minute")
async def update_delivery_status(
    request: Request,
    order_id: int,
    status_data: DeliveryUpdate,
    current_user: Annotated[User, Depends(require_rider)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(Order)
        .options(*ORDER_LOAD_OPTIONS)
        .where(Order.id == order_id, Order.rider_id == current_user.id)
    )

    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Order not found'
        )

    if status_data.status == OrderStatus.OUT_FOR_DELIVERY and order.status != OrderStatus.RIDER_GOING_TO_VENDOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order must be in RIDER GOING TO VENDOR status before marking OUT FOR DELIVERY"
        )

    if status_data.status == OrderStatus.DELIVERED and order.status != OrderStatus.OUT_FOR_DELIVERY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order must be in OUT FOR DELIVERY status before marking DELIVERED"
        )

    if status_data.status == OrderStatus.DELIVERED:
        await credit_wallets(order, db)

    order.status = status_data.status
    await db.commit()

    # Re-query with full eager loads after commit
    refreshed = await db.execute(
        select(Order).options(*ORDER_LOAD_OPTIONS).where(Order.id == order_id)
    )
    order = refreshed.scalars().first()

    return api_response(
        data=OrderResponse.model_validate(order),
        message="Delivery status updated successfully"
    )


# get completed orders
@router.get('/orders/completed', response_model=ApiResponse[list[OrderResponse]])
async def get_completed_orders(
    current_user: Annotated[User, Depends(require_rider)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    result = await db.execute(
        select(Order)
        .options(*ORDER_LOAD_OPTIONS)
        .where(
            Order.status == OrderStatus.DELIVERED,
            Order.rider_id == current_user.id
        )
        .order_by(Order.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    completed_orders = result.scalars().all()
    data = [OrderResponse.model_validate(o) for o in completed_orders]
    return api_response(data=data, message="Completed orders retrieved successfully")


@router.get('/{rider_id}/wallet', response_model=ApiResponse[WalletResponse])
async def get_wallet(
    current_user: Annotated[User, Depends(require_rider)],
    rider_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):

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
