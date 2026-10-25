"""
Wallet service — handles crediting vendor and rider wallets
when an order is marked as DELIVERED.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.order import Order
from app.models.payment import Wallet, WalletTransaction, TransactionType, TransactionStatus

logger = logging.getLogger(__name__)


async def _get_or_create_wallet(user_id: int, db: AsyncSession) -> Wallet:
    """Fetch the wallet for a user, creating one if it doesn't exist yet."""
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = result.scalars().first()

    if not wallet:
        wallet = Wallet(user_id=user_id, balance=0)
        db.add(wallet)
        await db.flush()  # Get wallet.id without committing

    return wallet


async def credit_wallets(order: Order, db: AsyncSession) -> None:
    """
    Called when an order status is set to DELIVERED.

    Credits:
      - Vendor wallet: order.subtotal  (food cost only, no fees)
      - Rider wallet:  order.delivery_fee
      - Platform keeps: order.service_fee (no wallet entry needed)
    """
    if not order.rider_id:
        logger.warning(
            "Order %s marked DELIVERED but has no rider_id — skipping rider wallet credit.",
            order.order_number
        )

    try:
        # ── Vendor wallet ──────────────────────────────────────────────
        vendor_wallet = await _get_or_create_wallet(order.customer_id, db)
        # Note: order.customer_id is the buyer — we need the restaurant owner's user_id
        # Get the restaurant owner's user_id from the restaurant
        from app.models.resturant import Resturants
        restaurant_result = await db.execute(
            select(Resturants).where(Resturants.id == order.restaurant_id)
        )
        restaurant = restaurant_result.scalars().first()

        if restaurant:
            vendor_wallet = await _get_or_create_wallet(restaurant.user_id, db)
            vendor_wallet.balance += order.subtotal

            db.add(WalletTransaction(
                wallet_id=vendor_wallet.id,
                order_id=order.id,
                amount=order.subtotal,
                type=TransactionType.CREDIT,
                status=TransactionStatus.COMPLETED,
                description=f"Payment for order {order.order_number}"
            ))
        else:
            logger.error("Could not find restaurant %s to credit vendor wallet.", order.restaurant_id)

        # ── Rider wallet ───────────────────────────────────────────────
        if order.rider_id:
            rider_wallet = await _get_or_create_wallet(order.rider_id, db)
            rider_wallet.balance += order.delivery_fee

            db.add(WalletTransaction(
                wallet_id=rider_wallet.id,
                order_id=order.id,
                amount=order.delivery_fee,
                type=TransactionType.CREDIT,
                status=TransactionStatus.COMPLETED,
                description=f"Delivery fee for order {order.order_number}"
            ))

        await db.commit()
        logger.info(
            "Wallets credited for order %s — vendor: ₦%s, rider: ₦%s",
            order.order_number,
            order.subtotal / 100,
            order.delivery_fee / 100,
        )

    except Exception:
        await db.rollback()
        logger.exception("Failed to credit wallets for order %s", order.order_number)
        raise
