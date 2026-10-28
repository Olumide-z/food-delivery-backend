# app/tasks/auth_tasks.py
import asyncio
from app.core.celery_app import celery_app
from app.utils.emails import send_verification_code_email, send_reset_password_email


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def task_send_verification_code(self, to_email: str, code: str, username: str):
    try:
        asyncio.run(
            send_verification_code_email(
                to_email=to_email, code=code, username=username
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def task_send_reset_password(self, to_email: str, reset_token: str, username: str):
    try:
        asyncio.run(
            send_reset_password_email(
                to_email=to_email, reset_token=reset_token, username=username
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)
