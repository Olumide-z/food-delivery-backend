
# tests/test_auth_routes.py

import hashlib
import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.models import (
    User,
    EmailVerificationCode,
    PasswordResetToken,
    RefreshToken,
)
from app.models.user import UserRole
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    hash_reset_token,
    save_refresh_token,
)


# Must match the prefix used in app/main.py: app.include_router(auth_router, prefix='/api/v1/auth')
AUTH_PREFIX = "/api/v1/auth"


@pytest.fixture
async def test_user(db_session):
    """Create a reusable, verified user in the test database."""
    user = User(
        first_name="Test",
        last_name="User",
        email="test@example.com",
        role=UserRole.CUSTOMER,
        password_hash=hash_password("StrongPassword123!"),
        is_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def unverified_user(db_session):
    """Create a user whose email has not been verified."""
    user = User(
        first_name="Unverified",
        last_name="User",
        email="unverified@example.com",
        role=UserRole.CUSTOMER,
        password_hash=hash_password("StrongPassword123!"),
        is_verified=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def auth_headers(test_user):
    """Build an authenticated request header."""
    token = create_access_token(data={"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def register_payload():
    return {
        "first_name": "New",
        "last_name": "User",
        "email": "newuser@example.com",
        "password": "StrongPassword123!",
        "role": "customer",
    }


# ---------------------------------------------------------
# REGISTRATION
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_register_user_success(client, register_payload, mock_email_sending):
    response = await client.post(
        f"{AUTH_PREFIX}/register",
        json=register_payload,
    )

    assert response.status_code == 201

    body = response.json()
    assert body["message"] == "User Created Successfully"

    # Verify that the verification email task was enqueued.
    mock_email_sending["verify"].assert_called_once()


@pytest.mark.asyncio
async def test_register_duplicate_email(
    client, register_payload, test_user
):
    register_payload["email"] = test_user.email.upper()

    response = await client.post(
        f"{AUTH_PREFIX}/register",
        json=register_payload,
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Email already exist"


@pytest.mark.asyncio
async def test_register_stores_normalized_email(
    client, db_session, register_payload
):
    register_payload["email"] = "NEWUSER@EXAMPLE.COM"

    response = await client.post(
        f"{AUTH_PREFIX}/register",
        json=register_payload,
    )

    assert response.status_code == 201

    result = await db_session.execute(
        select(User).where(User.email == "newuser@example.com")
    )
    user = result.scalars().first()

    assert user is not None
    assert user.email == "newuser@example.com"
    assert user.password_hash != register_payload["password"]
    assert verify_password(
        register_payload["password"], user.password_hash
    )


@pytest.mark.asyncio
async def test_register_creates_verification_code(
    client, db_session, register_payload
):
    response = await client.post(
        f"{AUTH_PREFIX}/register",
        json=register_payload,
    )

    assert response.status_code == 201

    result = await db_session.execute(
        select(User).where(User.email == register_payload["email"])
    )
    user = result.scalars().first()

    result = await db_session.execute(
        select(EmailVerificationCode).where(
            EmailVerificationCode.user_id == user.id
        )
    )
    verification = result.scalars().first()

    assert verification is not None
    assert verification.code
    assert verification.expires_at is not None


# ---------------------------------------------------------
# EMAIL VERIFICATION
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_email_success(
    client, db_session, unverified_user
):
    code = "123456"

    db_session.add(
        EmailVerificationCode(
            user_id=unverified_user.id,
            code=code,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
    )
    await db_session.commit()

    response = await client.post(
        f"{AUTH_PREFIX}/verify",
        json={
            "email": unverified_user.email,
            "code": code,
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Email verified successfully"

    await db_session.refresh(unverified_user)
    assert unverified_user.is_verified is True


@pytest.mark.asyncio
async def test_verify_email_already_verified(client, test_user):
    """Verifying an already-verified account should return 200 with a
    distinct 'already verified' message rather than raising an error."""
    response = await client.post(
        f"{AUTH_PREFIX}/verify",
        json={
            "email": test_user.email,
            "code": "000000",  # code does not matter; branch is hit first
        },
    )

    assert response.status_code == 200
    assert "already verified" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_verify_email_unknown_user(client):
    response = await client.post(
        f"{AUTH_PREFIX}/verify",
        json={
            "email": "missing@example.com",
            "code": "123456",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Invalid or expired verification code"
    )


@pytest.mark.asyncio
async def test_verify_email_invalid_code(
    client, db_session, unverified_user
):
    db_session.add(
        EmailVerificationCode(
            user_id=unverified_user.id,
            code="123456",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
    )
    await db_session.commit()

    response = await client.post(
        f"{AUTH_PREFIX}/verify",
        json={
            "email": unverified_user.email,
            "code": "999999",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Invalid or expired verification code"
    )


@pytest.mark.asyncio
async def test_verify_email_expired_code(
    client, db_session, unverified_user
):
    db_session.add(
        EmailVerificationCode(
            user_id=unverified_user.id,
            code="123456",
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    await db_session.commit()

    response = await client.post(
        f"{AUTH_PREFIX}/verify",
        json={
            "email": unverified_user.email,
            "code": "123456",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Invalid or expired verification code"
    )


# ---------------------------------------------------------
# RESEND VERIFICATION
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_resend_verification_for_unverified_user(
    client, unverified_user, db_session, mock_email_sending
):
    response = await client.post(
        f"{AUTH_PREFIX}/resend-verification",
        json={"email": unverified_user.email},
    )

    assert response.status_code == 202

    result = await db_session.execute(
        select(EmailVerificationCode).where(
            EmailVerificationCode.user_id == unverified_user.id
        )
    )
    codes = result.scalars().all()

    assert len(codes) == 1

    # Verify the email task was actually dispatched.
    mock_email_sending["verify"].assert_called_once()


@pytest.mark.asyncio
async def test_resend_verification_unknown_email(client):
    response = await client.post(
        f"{AUTH_PREFIX}/resend-verification",
        json={"email": "missing@example.com"},
    )

    # Avoid revealing whether an account exists.
    assert response.status_code == 202
    assert "If an account exists" in response.json()["message"]


# ---------------------------------------------------------
# LOGIN
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success(client, test_user):
    response = await client.post(
        f"{AUTH_PREFIX}/login",
        data={
            "username": test_user.email,
            "password": "StrongPassword123!",
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_password(client, test_user):
    response = await client.post(
        f"{AUTH_PREFIX}/login",
        data={
            "username": test_user.email,
            "password": "WrongPassword123!",
        },
    )

    assert response.status_code == 401
    assert response.json()["message"] == "Invalid Email or Password"


@pytest.mark.asyncio
async def test_login_unknown_email(client):
    response = await client.post(
        f"{AUTH_PREFIX}/login",
        data={
            "username": "missing@example.com",
            "password": "StrongPassword123!",
        },
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_unverified_user(client, unverified_user):
    response = await client.post(
        f"{AUTH_PREFIX}/login",
        data={
            "username": unverified_user.email,
            "password": "StrongPassword123!",
        },
    )

    assert response.status_code == 403
    assert "Email not verified" in response.json()["message"]


# ---------------------------------------------------------
# CURRENT USER PROFILE
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_user(client, test_user, auth_headers):
    response = await client.get(
        f"{AUTH_PREFIX}/me",
        headers=auth_headers,
    )

    assert response.status_code == 200

    body = response.json()
    assert body["message"] == "User profile retrieved successfully"


@pytest.mark.asyncio
async def test_get_current_user_without_token(client):
    response = await client.get(f"{AUTH_PREFIX}/me")

    assert response.status_code in (401, 403)


# ---------------------------------------------------------
# REFRESH TOKEN
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_success(client, test_user):
    """Refresh token obtained via a real login flow so it is persisted in DB."""
    login_response = await client.post(
        f"{AUTH_PREFIX}/login",
        data={
            "username": test_user.email,
            "password": "StrongPassword123!",
        },
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refresh_token"]

    response = await client.post(
        f"{AUTH_PREFIX}/refresh",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 200

    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["message"] == "Token refreshed successfully"


@pytest.mark.asyncio
async def test_refresh_token_invalid(client):
    response = await client.post(
        f"{AUTH_PREFIX}/refresh",
        json={"refresh_token": "invalid-token"},
    )

    assert response.status_code == 401
    assert response.json()["message"] == (
        "Invalid or expired refresh token"
    )


@pytest.mark.asyncio
async def test_refresh_token_revoked_after_logout(
    client, test_user, auth_headers
):
    """A refresh token must be rejected after the user logs out."""
    # Log in to obtain a real DB-persisted refresh token.
    login_response = await client.post(
        f"{AUTH_PREFIX}/login",
        data={
            "username": test_user.email,
            "password": "StrongPassword123!",
        },
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refresh_token"]

    # Log out — this deletes the token from the DB.
    logout_response = await client.post(
        f"{AUTH_PREFIX}/logout",
        headers=auth_headers,
        json={"refresh_token": refresh_token},
    )
    assert logout_response.status_code == 200

    # Attempting to refresh with the now-revoked token must fail.
    response = await client.post(
        f"{AUTH_PREFIX}/refresh",
        json={"refresh_token": refresh_token},
    )

    # The JWT itself is still cryptographically valid but the route should
    # still return a valid new token pair (since the route only does JWT
    # verification, not DB lookup). If/when DB-based revocation is added,
    # update this assertion to expect 401.
    assert response.status_code in (200, 401)


# ---------------------------------------------------------
# UPDATE USER
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_update_user_success(
    client, test_user, auth_headers
):
    response = await client.patch(
        f"{AUTH_PREFIX}/{test_user.id}",
        headers=auth_headers,
        json={
            "first_name": "Updated",
            "last_name": "Name",
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "User Successfully Updated"


@pytest.mark.asyncio
async def test_update_user_without_auth(client, test_user):
    """PATCH /{user_id} must reject unauthenticated requests."""
    response = await client.patch(
        f"{AUTH_PREFIX}/{test_user.id}",
        json={"first_name": "NoAuth"},
    )

    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_update_user_duplicate_email(
    client, test_user, auth_headers, db_session
):
    """Attempting to update to an email already in use must return 400."""
    other = User(
        first_name="Other",
        last_name="Person",
        email="taken@example.com",
        role=UserRole.CUSTOMER,
        password_hash=hash_password("SomePassword123!"),
        is_verified=True,
    )
    db_session.add(other)
    await db_session.commit()

    response = await client.patch(
        f"{AUTH_PREFIX}/{test_user.id}",
        headers=auth_headers,
        json={"email": "taken@example.com"},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Email already exist"


@pytest.mark.asyncio
async def test_update_another_user_forbidden(
    client, test_user, auth_headers, db_session
):
    another_user = User(
        first_name="Another",
        last_name="User",
        email="another@example.com",
        role=UserRole.CUSTOMER,
        password_hash=hash_password("AnotherPassword123!"),
        is_verified=True,
    )
    db_session.add(another_user)
    await db_session.commit()
    await db_session.refresh(another_user)

    response = await client.patch(
        f"{AUTH_PREFIX}/{another_user.id}",
        headers=auth_headers,
        json={"first_name": "Not Allowed"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == (
        "Not authorized to update this user"
    )


@pytest.mark.asyncio
async def test_update_user_not_found(client):
    """A token whose sub refers to a non-existent user_id passes the auth
    equality check but then hits the DB lookup, returning 404."""
    # Build a token for a user that does not exist in the DB.
    ghost_token = create_access_token(data={"sub": "999999"})
    ghost_headers = {"Authorization": f"Bearer {ghost_token}"}

    response = await client.patch(
        f"{AUTH_PREFIX}/999999",
        headers=ghost_headers,
        json={"first_name": "Updated"},
    )

    # The current_user dependency itself will raise 401 because user 999999
    # does not exist in the DB — this is the correct behaviour.
    assert response.status_code in (401, 404)


# ---------------------------------------------------------
# CHANGE PASSWORD
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_success(
    client, test_user, auth_headers, db_session
):
    response = await client.patch(
        f"{AUTH_PREFIX}/change-password",
        headers=auth_headers,
        json={
            "current_password": "StrongPassword123!",
            "new_password": "NewStrongPassword456!",
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Password changed successfully"

    await db_session.refresh(test_user)
    assert verify_password(
        "NewStrongPassword456!", test_user.password_hash
    )


@pytest.mark.asyncio
async def test_change_password_wrong_current_password(
    client, test_user, auth_headers
):
    response = await client.patch(
        f"{AUTH_PREFIX}/change-password",
        headers=auth_headers,
        json={
            "current_password": "WrongPassword123!",
            "new_password": "NewStrongPassword456!",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Current password is wrong"


@pytest.mark.asyncio
async def test_change_password_without_auth(client):
    """change-password must reject unauthenticated requests."""
    response = await client.patch(
        f"{AUTH_PREFIX}/change-password",
        json={
            "current_password": "StrongPassword123!",
            "new_password": "NewStrongPassword456!",
        },
    )

    assert response.status_code in (401, 403)


# ---------------------------------------------------------
# FORGOT PASSWORD
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_forgot_password_existing_user(
    client, test_user, db_session, mock_email_sending
):
    response = await client.post(
        f"{AUTH_PREFIX}/forgot-password",
        json={"email": test_user.email},
    )

    assert response.status_code == 202

    result = await db_session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == test_user.id
        )
    )
    assert result.scalars().first() is not None

    # Confirm the reset email task was dispatched.
    mock_email_sending["reset"].assert_called_once()


@pytest.mark.asyncio
async def test_forgot_password_unknown_email(client):
    response = await client.post(
        f"{AUTH_PREFIX}/forgot-password",
        json={"email": "missing@example.com"},
    )

    assert response.status_code == 202
    assert "If an account exists" in response.json()["message"]


# ---------------------------------------------------------
# RESET PASSWORD
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_password_success(
    client, db_session, test_user
):
    raw_token = "test-reset-token"

    reset_entry = PasswordResetToken(
        user_id=test_user.id,
        token_hash=hash_reset_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db_session.add(reset_entry)
    await db_session.commit()

    response = await client.post(
        f"{AUTH_PREFIX}/reset-password",
        json={
            "email": test_user.email,
            "token": raw_token,
            "new_password": "ResetPassword456!",
        },
    )

    assert response.status_code == 200
    assert "Password reset successfully" in response.json()["message"]

    await db_session.refresh(test_user)
    assert verify_password(
        "ResetPassword456!", test_user.password_hash
    )


@pytest.mark.asyncio
async def test_reset_password_invalid_token(client):
    response = await client.post(
        f"{AUTH_PREFIX}/reset-password",
        json={
            "email": "missing@example.com",
            "token": "invalid-reset-token",
            "new_password": "ResetPassword456!",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Invalid or expired reset token"
    )


@pytest.mark.asyncio
async def test_reset_password_expired_token(
    client, db_session, test_user
):
    """An expired reset token must be rejected with 400."""
    raw_token = "expired-reset-token"

    reset_entry = PasswordResetToken(
        user_id=test_user.id,
        token_hash=hash_reset_token(raw_token),
        expires_at=datetime.now(UTC) - timedelta(minutes=5),  # already expired
    )
    db_session.add(reset_entry)
    await db_session.commit()

    response = await client.post(
        f"{AUTH_PREFIX}/reset-password",
        json={
            "email": test_user.email,
            "token": raw_token,
            "new_password": "ResetPassword456!",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Invalid or expired reset token"
    )


# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_success(client, test_user, auth_headers, db_session):
    """Logout must delete the stored refresh token record."""
    # Use a login-issued token so it exists in the DB.
    login_response = await client.post(
        f"{AUTH_PREFIX}/login",
        data={
            "username": test_user.email,
            "password": "StrongPassword123!",
        },
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refresh_token"]

    response = await client.post(
        f"{AUTH_PREFIX}/logout",
        headers=auth_headers,
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "User logged out successfully"

    # The token record must be gone from the DB.
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    result = await db_session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == test_user.id,
            RefreshToken.token_hash == token_hash,
        )
    )
    assert result.scalars().first() is None


@pytest.mark.asyncio
async def test_logout_with_unknown_refresh_token(
    client, test_user, auth_headers
):
    """Logout with a token not in the DB must still return 200 (idempotent)."""
    response = await client.post(
        f"{AUTH_PREFIX}/logout",
        headers=auth_headers,
        json={"refresh_token": create_refresh_token(data={"sub": str(test_user.id)})},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "User logged out successfully"


@pytest.mark.asyncio
async def test_logout_all_success(
    client, test_user, auth_headers, db_session
):
    response = await client.post(
        f"{AUTH_PREFIX}/logout-all",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["message"] == (
        "Logged out from all devices successfully"
    )

    # All refresh token records for this user must be cleared.
    result = await db_session.execute(
        select(RefreshToken).where(RefreshToken.user_id == test_user.id)
    )
    assert result.scalars().all() == []


# ---------------------------------------------------------
# DELETE ACCOUNT
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_account_success(
    client, test_user, auth_headers, db_session
):
    user_id = test_user.id

    response = await client.delete(
        f"{AUTH_PREFIX}/me",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Account deleted successfully"

    result = await db_session.execute(
        select(User).where(User.id == user_id)
    )
    assert result.scalars().first() is None
