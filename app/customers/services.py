from fastapi import HTTPException, status
import httpx
from app.core.config import settings
from datetime import datetime, UTC
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.coupon import Coupon, DiscountType

import logging

logger = logging.getLogger(__name__)


async def initialize_paystack_payment(email: str, amount: int, reference: str) -> str:
    url = "https://api.paystack.co/transaction/initialize"
    headers = {
        "Authorization": (
            f"Bearer {settings.paystack_secret_key.get_secret_value()}"
        ),
        "Content-Type": "application/json",
    }
    payload = {
        "email": email,
        "amount": amount * 100,
        "reference": reference,
        "callback_url": f"{settings.paystack_callback_url}/orders/verify"
    }

    logger.info(
        "Initializing Paystack payment for reference: %s, amount: ₦%s", reference, amount)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )
        except httpx.RequestError as e:
            logger.error("Paystack API connection failed for reference %s: %s",
                         reference, str(e), exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not connect to payment gateway",
            )

        try:
            res_data = response.json()
        except ValueError:
            logger.error("Paystack returned a non-JSON response (HTTP %s) for reference %s",
                         response.status_code, reference)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from payment provider",
            )

    if response.is_error or not res_data.get("status"):
        logger.error("Paystack initialization failed for reference %s: %s",
                     reference, res_data.get("message"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to initialize payment. Please try again.",
        )

    logger.info(
        "Paystack payment initialized successfully for reference: %s", reference)
    return res_data["data"]["authorization_url"]


async def verify_paystack_payment(reference: str) -> dict:
    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {
        "Authorization": f"Bearer {settings.paystack_secret_key.get_secret_value()}",
        "Content-Type": "application/json",
    }

    logger.info("Verifying Paystack payment for reference: %s", reference)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.RequestError as e:
            logger.error("Paystack API verification connection failed for reference %s: %s",
                         reference, str(e), exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not connect to payment gateway",
            )

        try:
            res_data = response.json()
        except ValueError:
            logger.error(
                "Paystack verification returned a non-JSON response for reference %s", reference)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from payment provider."
            )

        if response.is_error or not res_data.get("status"):
            logger.warning("Paystack verification failed for reference %s: %s",
                           reference, res_data.get("message"))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to verify payment with payment provider."
            )

        logger.info(
            "Paystack payment verified successfully for reference: %s", reference)
        return res_data["data"]


async def get_valid_coupon(
    db: AsyncSession, restaurant_id: int, code: str, subtotal: int
) -> Coupon:
    """Fetch the coupon and check every rule. Raises 400 with a clear reason."""
    result = await db.execute(
        select(Coupon).where(
            Coupon.restaurant_id == restaurant_id,
            Coupon.code == code.strip().upper(),
        )
    )
    coupon = result.scalars().first()

    if not coupon or not coupon.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Invalid or inactive coupon code")

    if coupon.expires_at and coupon.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This coupon has expired")

    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This coupon has reached its usage limit")

    if subtotal < (coupon.min_order_amount or 0):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Minimum order amount for this coupon is {coupon.min_order_amount}",
        )

    return coupon


def calculate_discount(coupon: Coupon, subtotal: int) -> int:
    """Discount applies to the subtotal only (not delivery/service fees)."""
    if coupon.discount_type == DiscountType.PERCENTAGE:
        discount = subtotal * coupon.discount_value // 100
    else:
        discount = coupon.discount_value
    return min(discount, subtotal)  # never exceed the subtotal


async def redeem_coupon(db: AsyncSession, coupon: Coupon) -> None:
    """
    Atomically bump used_count. The WHERE clause re-checks max_uses at write time,
    so two concurrent orders can't both take the last use.
    """
    result = await db.execute(
        update(Coupon)
        .where(
            Coupon.id == coupon.id,
            Coupon.is_active.is_(True),
            (Coupon.max_uses.is_(None)) | (Coupon.used_count < Coupon.max_uses),
        )
        .values(used_count=Coupon.used_count + 1)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This coupon is no longer available")
