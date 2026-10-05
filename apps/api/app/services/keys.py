"""API key lifecycle.

The raw key exists only in the response that creates it: the database stores a
SHA-256 digest plus a short display prefix. Revocation is a soft delete, and
authentication always reads current state, so a revoked key stops working for
every process immediately.
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import ApiKey, User
from ..security import token_digest
from . import identity as identity_service

KEY_PREFIX = "sk-rubai-"
PREFIX_DISPLAY_LENGTH = 16  # "sk-rubai-" plus 7 random characters
MAX_NAME_LENGTH = 64


def new_raw_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(24)


async def create_key(
    db: AsyncSession,
    *,
    user: User,
    name: str,
    monthly_limit_kopecks: int | None = None,
) -> tuple[ApiKey, str]:
    if user.email_verified_at is None:
        raise ApiError(403, "email_not_verified", "Подтвердите адрес электронной почты, чтобы создавать ключи.")
    clean_name = name.strip()
    if not clean_name or len(clean_name) > MAX_NAME_LENGTH:
        raise ApiError(400, "invalid_name", f"Имя ключа: от 1 до {MAX_NAME_LENGTH} символов.")
    if monthly_limit_kopecks is not None and monthly_limit_kopecks < 0:
        raise ApiError(400, "invalid_limit", "Лимит не может быть отрицательным.")

    raw = new_raw_key()
    key = ApiKey(
        user_id=user.id,
        name=clean_name,
        prefix=raw[:PREFIX_DISPLAY_LENGTH],
        key_hash=token_digest(raw),
        monthly_limit_kopecks=monthly_limit_kopecks,
    )
    db.add(key)
    await db.flush()
    return key, raw


async def list_keys(db: AsyncSession, *, user: User) -> list[ApiKey]:
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_key(db: AsyncSession, *, user: User, key_id: uuid.UUID) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        # Do not reveal whether the key exists for another account.
        raise ApiError(404, "key_not_found", "Ключ не найден.")
    if key.revoked_at is None:
        key.revoked_at = identity_service.utcnow()
    return key


def looks_like_api_key(value: str) -> bool:
    return value.startswith(KEY_PREFIX) and 20 <= len(value) <= 120


async def authenticate_api_key(db: AsyncSession, *, raw_key: str) -> tuple[User, ApiKey] | None:
    if not looks_like_api_key(raw_key):
        return None
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == token_digest(raw_key)))
    key = result.scalar_one_or_none()
    if key is None or key.revoked_at is not None:
        return None
    user = await db.get(User, key.user_id)
    if user is None or user.status != "active":
        return None
    return user, key
