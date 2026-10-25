# Vendor Business Logic Services
from app.models import Coupon
from app.models import DiscountType
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from datetime import datetime, UTC
from sqlalchemy.exc import IntegrityError
from app.models import Resturants, Menu


async def verify_restaurant_ownership(
    restaurant_id: int,
    vendor_user_id: int,
    db: AsyncSession
) -> Resturants:
    res = await db.execute(
        select(Resturants)
        .options(selectinload(Resturants.user))
        .where(Resturants.id == restaurant_id)
    )
    restaurant = res.scalars().first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )
    if restaurant.user_id != vendor_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not authorized to modify this restaurant'
        )
    return restaurant


async def verify_menu_ownership(
    restaurant_id: int,
    menu_id: int,
    vendor_user_id: int,
    db: AsyncSession
) -> Menu:
    res = await db.execute(select(Resturants).where(Resturants.id == restaurant_id))
    restaurant = res.scalars().first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Restaurant not found'
        )
    if restaurant.user_id != vendor_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not authorized to modify this restaurant'
        )
    res_menu = await db.execute(select(Menu).where(Menu.id == menu_id))
    menu = res_menu.scalars().first()
    if not menu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Menu not found'
        )
    if menu.restaurant_id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Menu does not belong to this restaurant'
        )
    return menu


def _validate_discount(discount_type: str, discount_value: int) -> DiscountType:
    try:
        dtype = DiscountType(discount_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="discount_type must be 'percentage' or 'flat'",
        )
    if dtype == DiscountType.PERCENTAGE and not (1 <= discount_value <= 100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Percentage discount must be between 1 and 100",
        )
    return dtype


def _normalize_expiry(expires_at: datetime | None) -> datetime | None:
    if expires_at is not None and expires_at.tzinfo is None:
        return expires_at.replace(tzinfo=UTC)  # treat naive datetimes as UTC
    return expires_at


async def _get_vendor_coupon(
    restaurant_id: int, coupon_id: int, db: AsyncSession
) -> Coupon:
    result = await db.execute(
        select(Coupon).where(
            Coupon.id == coupon_id, Coupon.restaurant_id == restaurant_id
        )
    )
    coupon = result.scalars().first()
    if not coupon:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found"
        )
    return coupon
