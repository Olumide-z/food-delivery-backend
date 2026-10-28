import asyncio

from app.core.celery_app import celery_app
from app.utils.emails import send_email


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # retry after 60 seconds
)
def send_order_confirmation_email(self, user_email: str, order_id: str):
    """Sends order confirmation email. Retries up to 3 times on failure."""
    try:
        asyncio.run(
            send_email(
                to_email=user_email,
                subject="Your order has been placed!",
                plain_text=f"Order #{order_id} is being prepared.",
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_vendor_notification_email(self, vendor_email: str, order_id: str):
    """Notify vendor of a new order."""
    try:
        asyncio.run(
            send_email(
                to_email=vendor_email,
                subject="New Order Received!",
                plain_text=f"You have a new order #{order_id}. Please confirm.",
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_payment_success_email(self, user_email: str, order_number: str, total_amount: int):
    """Confirms to the customer their payment was successful."""
    try:
        amount_formatted = f"₦{total_amount}"   # kobo → naira
        asyncio.run(
            send_email(
                to_email=user_email,
                subject="✅ Payment Confirmed!",
                plain_text=(
                    f"Your payment of {amount_formatted} for order #{order_number} "
                    f"was successful. Your food is being prepared!"
                ),
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_order_cancelled_email(self, user_email: str, order_number: str):
    """Notifies customer their order was cancelled."""
    try:
        asyncio.run(
            send_email(
                to_email=user_email,
                subject="Order Cancelled",
                plain_text=(
                    f"Your order #{order_number} has been cancelled. "
                    f"If you used a coupon, it has been restored. "
                    f"If you were charged, a refund will be processed within 3-5 business days."
                ),
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_order_status_update_email(
    self,
    user_email: str,
    order_number: str,
    new_status: str,
):
    """Notifies customer of a status change on their order."""
    STATUS_MESSAGES = {
        "preparing":        "🍳 Your order is being prepared!",
        "ready_for_pickup": "📦 Your order is ready and waiting for a rider!",
        "rider_going_to_vendor": "🛵 A rider is heading to pick up your order!",
        "out_for_delivery": "🚀 Your order is out for delivery — ETA soon!",
        "delivered":        "✅ Your order has been delivered. Enjoy your meal!",
        "cancelled":        "❌ Your order has been cancelled.",
    }
    message = STATUS_MESSAGES.get(
        new_status, f"Your order status: {new_status}")
    try:
        asyncio.run(
            send_email(
                to_email=user_email,
                subject=f"Order #{order_number} — Update",
                plain_text=message,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)
