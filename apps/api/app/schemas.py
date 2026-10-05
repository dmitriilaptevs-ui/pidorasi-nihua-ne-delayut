"""Request/response schemas for the identity API."""

from __future__ import annotations

import uuid
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
