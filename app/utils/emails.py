from app.core.config import settings
from email.message import EmailMessage

import aiosmtplib


async def send_email(
    to_email: str,
    subject: str,
    plain_text: str
) -> None:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to_email
    message["Subject"] = subject

    message.set_content(plain_text)

    await aiosmtplib.send(
        message,
        hostname=settings.mail_server,
        port=settings.mail_port,
        start_tls=settings.mail_use_tls,
        username=settings.mail_username if settings.mail_username else None,
        password=settings.mail_password.get_secret_value() or None,
    )


async def send_reset_password_email(to_email: str, reset_token: str, username: str) -> None:
    reset_url = f"{settings.frontend_url}/reset-password?token={reset_token}"

    plain_text = f"""Hi {username}, 
    You requested to reset your password. Click the link below to set a new password:

{reset_url}

This link will expire in 1 hour.

If you didn't request this, you can safely ignore this email.

Best regards,
The Olumide Food Team
    """
    pass

    await send_email(
        to_email=to_email,
        subject="Reset Password Email",
        plain_text=plain_text
    )


async def send_verification_code_email(to_email: str, code: str, username: str) -> None:
    plain_text = f"""Hi {username},

Thank you for registering. Your email verification code is:

{code}

This code will expire in 15 minutes.

If you did not register for an account, please ignore this email.

Best regards,
The Olumide Food Team
"""

    await send_email(
        to_email=to_email,
        subject="Your Email Verification Code",
        plain_text=plain_text
    )
