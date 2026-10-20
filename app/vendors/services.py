# Vendor Business Logic Services
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
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
