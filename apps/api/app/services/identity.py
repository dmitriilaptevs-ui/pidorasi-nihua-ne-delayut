"""Identity service: registration, login, sessions, email tokens.

All state transitions live here and are exercised by tests. Tokens are stored
only as digests; consumption is guarded by row locks so a token cannot be used
twice even under concurrent requests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from ..errors import ApiError
from ..models import AuthSession, EmailToken, User
from ..security import hash_password, new_opaque_token, token_digest, verify_password
from ..settings import settings

_fallback_hash: str | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _fallback_password_hash() -> str:
    """One dummy hash so a missing user costs the same as a wrong password."""
    global _fallback_hash
    if _fallback_hash is None:
        _fallback_hash = hash_password("timing-equalizer-not-a-real-password")
    return _fallback_hash


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_user(db: AsyncSession, user_id) -> User | None:
    return await db.get(User, user_id)


async def get_or_create_site_user(db: AsyncSession, *, subject: str, email: str | None) -> User:
    """Use the Site's asserted identity; never link an existing account by email."""
    query = select(User).where(User.oauth_provider == "chatgpt", User.oauth_subject == token_digest(subject))
    user = (await db.execute(query)).scalar_one_or_none()
    if user is None:
        normalized = normalize_email(email) if email else None
        if normalized and await get_user_by_email(db, normalized) is not None:
            normalized = None
        user = User(
            email=normalized,
            display_name=(email or "ChatGPT")[:80],
            signup_method="chatgpt",
            oauth_provider="chatgpt",
            oauth_subject=token_digest(subject),
            email_verified_at=utcnow(),
        )
        try:
            async with db.begin_nested():
                db.add(user)
                await db.flush()
        except IntegrityError:
            user = (await db.execute(query)).scalar_one_or_none()
            if user is None:
                raise ApiError(409, "identity_conflict", "Войдите ещё раз.") from None
    if user.status != "active":
        raise ApiError(403, "account_disabled", "Аккаунт недоступен.")
    return user


async def register(db: AsyncSession, *, email: str, password: str, method: str = "password") -> tuple[User, str]:
    normalized = normalize_email(email)
    if await get_user_by_email(db, normalized) is not None:
        raise ApiError(409, "email_taken", "Этот адрес уже зарегистрирован.")
    user = User(email=normalized, password_hash=hash_password(password), signup_method=method)
    db.add(user)
    await db.flush()
    token = await issue_email_token(db, user=user, kind="verify_email", ttl_hours=settings.email_verify_ttl_hours)
    return user, token


async def issue_email_token(db: AsyncSession, *, user: User, kind: str, ttl_hours: int) -> str:
    token = new_opaque_token()
    row = EmailToken(
        user_id=user.id,
        kind=kind,
        token_hash=token_digest(token),
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(row)
    await db.flush()
    return token


async def consume_email_token(db: AsyncSession, *, token: str, kind: str) -> User:
    result = await db.execute(
        select(EmailToken).where(EmailToken.token_hash == token_digest(token)).with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None or row.kind != kind:
        raise ApiError(400, "invalid_token", "Ссылка недействительна.")
    if row.used_at is not None:
        raise ApiError(400, "token_used", "Ссылка уже использована.")
    if row.expires_at <= utcnow():
        raise ApiError(400, "token_expired", "Срок действия ссылки истёк.")
    row.used_at = utcnow()
    user = await db.get(User, row.user_id)
    if user is None:
        raise ApiError(400, "invalid_token", "Ссылка недействительна.")
    return user


async def verify_email(db: AsyncSession, *, token: str) -> User:
    user = await consume_email_token(db, token=token, kind="verify_email")
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    return user


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User | None:
    user = await get_user_by_email(db, email)
    if user is None or not user.password_hash:
        verify_password(password, _fallback_password_hash())
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def create_session(db: AsyncSession, *, user: User, method: str = "password") -> tuple[AuthSession, str]:
    token = new_opaque_token()
    session = AuthSession(
        user_id=user.id,
        token_hash=token_digest(token),
        method=method,
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    await db.flush()
    return session, token


async def resolve_session(db: AsyncSession, *, token: str) -> tuple[AuthSession, User] | None:
    result = await db.execute(select(AuthSession).where(AuthSession.token_hash == token_digest(token)))
    session = result.scalar_one_or_none()
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        return None
    user = await db.get(User, session.user_id)
    if user is None or user.status != "active":
        return None
    return session, user


async def revoke_session(db: AsyncSession, *, token: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == token_digest(token), AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all_sessions(db: AsyncSession, *, user: User) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def request_password_reset(db: AsyncSession, *, email: str) -> tuple[User, str] | None:
    user = await get_user_by_email(db, email)
    if user is None or user.password_hash is None:
        return None
    token = await issue_email_token(db, user=user, kind="reset_password", ttl_hours=settings.password_reset_ttl_hours)
    return user, token


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> User:
    user = await consume_email_token(db, token=token, kind="reset_password")
    user.password_hash = hash_password(new_password)
    await revoke_all_sessions(db, user=user)
    return user


async def promote_admin(db: AsyncSession, *, email: str) -> User:
    user = await get_user_by_email(db, email)
    if user is None:
        raise ApiError(404, "user_not_found", "Пользователь не найден.")
    user.role = "admin"
    return user
