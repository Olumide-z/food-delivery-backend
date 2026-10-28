from app.tasks.auth_tasks import task_send_reset_password
from app.tasks.auth_tasks import task_send_verification_code
import logging
import hashlib
from typing import Annotated
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, status, HTTPException, BackgroundTasks, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sql_delete

from app.core.limiter import limiter
from app.core.config import settings
from app.core.database import get_db
from app.core.response import api_response, ApiResponse
from app.core.security import (
    hash_password,
    verify_password,
    generate_reset_token,
    hash_reset_token,
    verify_refresh_token,
    create_access_token,
    create_refresh_token,
    generate_verification_code,
    save_refresh_token,
    CurrentUser,
)
from app.models import User, PasswordResetToken, EmailVerificationCode, RefreshToken
from app.auth.schemas import (
    Token,
    RefreshTokenRequest,
    TokenRefreshResponse,
    ForgotPassword,
    ResetPassword,
    ChangePasswordReset,
    VerifyEmailRequest,
    ResendVerificationRequest,
    UserPrivateResponse,
    UserCreationRequest,
    UserCreatedResponse,
    UserUpdatedResponse,
    UpdateUserRequest,
)
from app.utils.emails import send_verification_code_email, send_reset_password_email

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post('/register', response_model=ApiResponse[UserCreatedResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register_user(request: Request, user: UserCreationRequest, background_tasks: BackgroundTasks, db: Annotated[AsyncSession, Depends(get_db)]):
    logger.info("Registration attempt for email: %s", user.email.lower())

    results = await db.execute(
        select(User).where(func.lower(User.email) == user.email.lower())
    )
    existing_email = results.scalars().first()

    if existing_email:
        logger.warning(
            "Registration failed: Email %s already exists", user.email.lower())
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Email already exist'
        )

    newUser = User(
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email.lower(),
        role=user.role,
        password_hash=hash_password(user.password)
    )

    db.add(newUser)
    await db.commit()
    await db.refresh(newUser)

    logger.info("User ID %s created successfully with role '%s'",
                newUser.id, newUser.role)

    code = generate_verification_code()
    expires_at = datetime.now(UTC) + timedelta(minutes=15)

    verification_code_obj = EmailVerificationCode(
        user_id=newUser.id,
        code=code,
        expires_at=expires_at
    )
    db.add(verification_code_obj)
    await db.commit()

    background_tasks.add_task(
        send_verification_code_email,
        to_email=newUser.email,
        code=code,
        username=newUser.first_name or newUser.email
    )
    logger.info("Verification code email queued for: %s", newUser.email)

    access_token = create_access_token(data={"sub": str(newUser.id)})
    refresh_token = create_refresh_token(data={"sub": str(newUser.id)})

    return api_response(
        data={
            "user": UserPrivateResponse.model_validate(newUser),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        },
        message="User Created Successfully"
    )


@router.post('/verify', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    request_data: VerifyEmailRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    results = await db.execute(
        select(User).where(func.lower(User.email)
                           == request_data.email.lower())
    )
    user = results.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    if user.is_verified:
        return api_response(message="Email is already verified")

    code_results = await db.execute(
        select(EmailVerificationCode).where(
            EmailVerificationCode.user_id == user.id,
            EmailVerificationCode.code == request_data.code,
        )
    )
    verification_entry = code_results.scalars().first()

    if not verification_entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    entry_expires = verification_entry.expires_at.replace(
        tzinfo=UTC) if verification_entry.expires_at.tzinfo is None else verification_entry.expires_at
    if entry_expires < datetime.now(UTC):
        await db.delete(verification_entry)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    user.is_verified = True
    await db.execute(
        sql_delete(EmailVerificationCode).where(
            EmailVerificationCode.user_id == user.id
        )
    )
    await db.commit()

    return api_response(message="Email verified successfully")


@router.post("/resend-verification", response_model=ApiResponse[None], status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/minute")
async def resend_verification(
    request: Request,
    request_data: ResendVerificationRequest,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    results = await db.execute(
        select(User).where(func.lower(User.email)
                           == request_data.email.lower())
    )
    user = results.scalars().first()

    if user and not user.is_verified:
        await db.execute(
            sql_delete(EmailVerificationCode).where(
                EmailVerificationCode.user_id == user.id
            )
        )

        code = generate_verification_code()
        expires_at = datetime.now(UTC) + timedelta(minutes=15)

        verification_code_obj = EmailVerificationCode(
            user_id=user.id,
            code=code,
            expires_at=expires_at
        )

        db.add(verification_code_obj)
        await db.commit()

        task_send_verification_code.delay(
            to_email=user.email,
            code=code,
            username=user.first_name or user.email
        )

    return api_response(message="If an account exists with this email and is not verified, a verification code has been sent.")


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
async def login_user(request: Request, form_data: Annotated[OAuth2PasswordRequestForm, Depends()], db: Annotated[AsyncSession, Depends(get_db)]):
    logger.info("Login attempt for email: %s", form_data.username.lower())

    results = await db.execute(select(User).where(func.lower(User.email) == form_data.username.lower()))
    existing_user = results.scalars().first()

    if not existing_user or not verify_password(form_data.password, existing_user.password_hash):
        logger.warning(
            "Login failed: Invalid email or password for email '%s'", form_data.username.lower())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Email or Password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not existing_user.is_verified:
        logger.warning("Login blocked: Email not verified for user ID %s (%s)",
                       existing_user.id, existing_user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please verify your email before logging in."
        )

    access_token = create_access_token(data={"sub": str(existing_user.id)})
    refresh_token = create_refresh_token(data={"sub": str(existing_user.id)})

    await save_refresh_token(user_id=existing_user.id, refresh_token=refresh_token, db=db)

    logger.info("User ID %s (%s) logged in successfully",
                existing_user.id, existing_user.email)

    token_payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        status="success",
        message="User logged in successfully",
        data=token_payload,
    )


@router.get("/me", response_model=ApiResponse[UserPrivateResponse])
async def get_user(current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    return api_response(
        data=UserPrivateResponse.model_validate(current_user),
        message="User profile retrieved successfully"
    )


@router.post("/refresh", response_model=TokenRefreshResponse)
@limiter.limit("10/minute")
async def get_refresh_token(
    request: Request,
    body: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    user_id = verify_refresh_token(body.refresh_token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id_int))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    new_access_token = create_access_token(data={"sub": str(user.id)})
    new_refresh_token = create_refresh_token(data={"sub": str(user.id)})

    token_payload = {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }

    return TokenRefreshResponse(
        status="success",
        message="Token refreshed successfully",
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        data=token_payload,
    )


@router.post("/logout", response_model=ApiResponse[None])
async def logout(request_body: RefreshTokenRequest, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    token_hash = hashlib.sha256(
        request_body.refresh_token.encode()).hexdigest()

    await db.execute(
        sql_delete(RefreshToken).where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.token_hash == token_hash
        )
    )
    await db.commit()

    return api_response(message="User logged out successfully")


@router.post("/logout-all", response_model=ApiResponse[None])
async def logout_all(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await db.execute(
        sql_delete(RefreshToken).where(RefreshToken.user_id == current_user.id)
    )
    await db.commit()
    return api_response(message="Logged out from all devices successfully")


@router.patch("/change-password", response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    password_data: ChangePasswordReset,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Current password is wrong'
        )

    current_user.password_hash = hash_password(password_data.new_password)
    db.add(current_user)

    await db.execute(
        sql_delete(PasswordResetToken).where(
            PasswordResetToken.user_id == current_user.id,
        ),
    )

    await db.commit()
    return api_response(message="Password changed successfully")


@router.patch("/{user_id}", response_model=ApiResponse[UserPrivateResponse])
async def update_user(
    current_user: CurrentUser,
    user_id: int,
    user_update: UpdateUserRequest,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    if user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user"
        )

    results = await db.execute(select(User).where(User.id == user_id))
    user = results.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    if user_update.email is not None and user_update.email.lower() != user.email.lower():
        result = await db.execute(
            select(User).where(func.lower(User.email)
                               == user_update.email.lower())
        )
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Email already exist'
            )

    if user_update.first_name is not None:
        user.first_name = user_update.first_name
    if user_update.last_name is not None:
        user.last_name = user_update.last_name
    if user_update.email is not None:
        user.email = user_update.email.lower()

    await db.commit()
    await db.refresh(user)
    return api_response(
        data=UserPrivateResponse.model_validate(user),
        message="User Successfully Updated"
    )


@router.post("/forgot-password", response_model=ApiResponse[None], status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    request_data: ForgotPassword,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    results = await db.execute(select(User).where(func.lower(User.email) == request_data.email.lower()))
    user = results.scalars().first()

    if user:
        await db.execute(
            sql_delete(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
            ),
        )

        token = generate_reset_token()
        token_hash = hash_reset_token(token)
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.reset_token_expire_minutes,
        )

        reset_token_obj = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at
        )

        db.add(reset_token_obj)
        await db.commit()

        task_send_reset_password.delay(
            to_email=user.email,
            reset_token=token,
            username=user.first_name or user.email
        )

    return api_response(message="If an account exists with this email, you will receive password reset instructions.")


@router.post('/reset-password', response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    request_data: ResetPassword,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    token_hash = hash_reset_token(request_data.token)
    result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    reset_token = result.scalars().first()

    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    token_expires = reset_token.expires_at.replace(
        tzinfo=UTC) if reset_token.expires_at.tzinfo is None else reset_token.expires_at
    if token_expires < datetime.now(UTC):
        await db.delete(reset_token)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    result = await db.execute(select(User).where(User.id == reset_token.user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    user.password_hash = hash_password(request_data.new_password)
    await db.execute(
        sql_delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
        ),
    )

    await db.commit()
    return api_response(message="Password reset successfully. You can now log in with your new password.")


@router.delete("/me", response_model=ApiResponse[None], status_code=status.HTTP_200_OK)
async def delete_account(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await db.execute(
        sql_delete(User).where(
            User.id == current_user.id,
        ),
    )
    await db.commit()

    return api_response(message="Account deleted successfully")
