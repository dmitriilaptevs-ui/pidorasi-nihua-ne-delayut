"""Request/response schemas for the identity API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class StartRequest(BaseModel):
    provider: Literal["vk", "yandex"]
    variant: Literal["canvas"] = "canvas"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ForgotRequest(BaseModel):
    email: EmailStr


class ResetRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=200)


class CreditRequest(BaseModel):
    email: EmailStr
    amount_kopecks: int = Field(ge=1, le=100_000_000)
    reference: str = Field(min_length=3, max_length=120)


class PaymentCreateRequest(BaseModel):
    amount_kopecks: int = Field(ge=1, le=100_000_000)


class RefundRequest(BaseModel):
    amount_kopecks: int | None = Field(default=None, ge=1)


class KeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    monthly_limit_kopecks: int | None = Field(default=None, ge=0)


class KeyView(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    monthly_limit_kopecks: int | None

    @classmethod
    def of(cls, key: object) -> "KeyView":
        return cls(
            id=key.id,  # type: ignore[attr-defined]
            name=key.name,  # type: ignore[attr-defined]
            prefix=key.prefix,  # type: ignore[attr-defined]
            created_at=key.created_at,  # type: ignore[attr-defined]
            revoked_at=key.revoked_at,  # type: ignore[attr-defined]
            last_used_at=key.last_used_at,  # type: ignore[attr-defined]
            monthly_limit_kopecks=key.monthly_limit_kopecks,  # type: ignore[attr-defined]
        )


class UserView(BaseModel):
    id: uuid.UUID
    email: str | None
    display_name: str | None
    role: str
    status: str
    email_verified: bool
    signup_method: str

    @classmethod
    def of(cls, user: object) -> "UserView":
        return cls(
            id=user.id,  # type: ignore[attr-defined]
            email=user.email,  # type: ignore[attr-defined]
            display_name=user.display_name,  # type: ignore[attr-defined]
            role=user.role,  # type: ignore[attr-defined]
            status=user.status,  # type: ignore[attr-defined]
            email_verified=user.email_verified_at is not None,  # type: ignore[attr-defined]
            signup_method=user.signup_method,  # type: ignore[attr-defined]
        )
