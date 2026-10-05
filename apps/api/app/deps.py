"""FastAPI dependencies: database session, authenticated user, origin checks."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session_factory
from .errors import ApiError
from .models import AuthSession, User
from .services.identity import resolve_session
from .settings import settings


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


def require_origin(request: Request) -> None:
    """Reject cross-origin browser mutations.

    Browsers always attach Origin to cross-site POSTs; same-site reads carry
    none. API clients without the cookie are unaffected by CSRF.
    """
    origin = request.headers.get("origin")
    if origin is not None and origin != settings.public_origin:
        raise ApiError(403, "invalid_origin", "Запрос пришёл с недопустимого адреса.")


async def current_session(
    request: Request, db: AsyncSession = Depends(get_db)
) -> tuple[AuthSession, User]:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise ApiError(401, "not_authenticated", "Требуется вход.")
    resolved = await resolve_session(db, token=token)
    if resolved is None:
        raise ApiError(401, "session_invalid", "Сессия истекла. Войдите снова.")
    return resolved


async def current_user(session_and_user: tuple[AuthSession, User] = Depends(current_session)) -> User:
    return session_and_user[1]


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise ApiError(403, "admin_required", "Недостаточно прав.")
    return user


def client_ip(request: Request) -> str:
    """Best-effort client address.

    The API is only reachable through the web container's proxy, which sets
    X-Forwarded-For; the header is trusted only inside that boundary.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _secure() -> bool:
    return settings.public_origin.startswith("https:")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )
