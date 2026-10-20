from fastapi import HTTPException, status
import httpx
from app.core.config import settings
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

    logger.info("Initializing Paystack payment for reference: %s, amount: ₦%s", reference, amount)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )
        except httpx.RequestError as e:
            logger.error("Paystack API connection failed for reference %s: %s", reference, str(e), exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not connect to payment gateway",
            )

        try:
            res_data = response.json()
        except ValueError:
            logger.error("Paystack returned a non-JSON response (HTTP %s) for reference %s", response.status_code, reference)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from payment provider",
            )

    if response.is_error or not res_data.get("status"):
        logger.error("Paystack initialization failed for reference %s: %s", reference, res_data.get("message"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to initialize payment. Please try again.",
        )

    logger.info("Paystack payment initialized successfully for reference: %s", reference)
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
            logger.error("Paystack API verification connection failed for reference %s: %s", reference, str(e), exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not connect to payment gateway",
            )

        try:
            res_data = response.json()
        except ValueError:
            logger.error("Paystack verification returned a non-JSON response for reference %s", reference)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from payment provider."
            )

        if response.is_error or not res_data.get("status"):
            logger.warning("Paystack verification failed for reference %s: %s", reference, res_data.get("message"))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to verify payment with payment provider."
            )

        logger.info("Paystack payment verified successfully for reference: %s", reference)
        return res_data["data"]

