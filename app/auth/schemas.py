import re
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from app.models.user import UserRole


# ── User / Registration ───────────────────────────────────────────────────────

class UserBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(max_length=50)
    email: EmailStr = Field(min_length=1, max_length=120)
    role: UserRole = Field(default=UserRole.CUSTOMER)


class UserCreationRequest(UserBase):
    password: str = Field(min_length=6)

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, value: str) -> str:
        if not re.search(r"[A-Z]", value):
            raise ValueError(
                "Password must contain at least one uppercase letter.")
        if not re.search(r"[^\w\s]", value):
            raise ValueError(
                "Password must contain at least one special character/symbol.")
        return value


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class UpdateUserRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=50)
    last_name: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = Field(default=None, min_length=1, max_length=120)


# ── User Responses ────────────────────────────────────────────────────────────

class UserPublicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str


class UserPrivateResponse(UserPublicResponse):
    email: EmailStr
    role: str
    is_verified: bool


class UserCreatedResponse(BaseModel):
    message: str | None = None
    user: UserPrivateResponse
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class UserUpdatedResponse(BaseModel):
    message: str | None = None
    user: UserPrivateResponse


# ── Tokens ────────────────────────────────────────────────────────────────────

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    status: str = "success"
    message: str = "User logged in successfully"
    data: dict | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenRefreshResponse(BaseModel):
    status: str = "success"
    message: str = "Token refreshed successfully"
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    data: dict | None = None


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str = "Success"


# ── Password flows ────────────────────────────────────────────────────────────

class ForgotPassword(BaseModel):
    email: EmailStr = Field(max_length=120)


class ResetPassword(BaseModel):
    email: EmailStr = Field(max_length=120)
    new_password: str = Field(min_length=6)
    token: str


class ChangePasswordReset(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


# ── Email verification ────────────────────────────────────────────────────────

class VerifyEmailRequest(BaseModel):
    email: EmailStr = Field(max_length=120)
    code: str = Field(min_length=6, max_length=6)


class ResendVerificationRequest(BaseModel):
    email: EmailStr = Field(max_length=120)
