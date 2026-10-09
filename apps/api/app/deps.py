"""FastAPI dependencies: database session, authenticated user, origin checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session_factory
from .errors import ApiError
from .models import AuthSession, User
from .services.identity import resolve_session
from .settings import settings

API_PROXY_HEADER = "x-rubai-proxy-token"


async def read_json_body(request: Request, *, max_bytes: int) -> object:
    """Bound streamed input before parsing, including requests without a length."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError:
            raise ApiError(400, "invalid_request", "Некорректный запрос.") from None
        if size < 0:
            raise ApiError(400, "invalid_request", "Некорректный запрос.")
        if size > max_bytes:
            raise ApiError(413, "request_too_large", "Запрос слишком большой.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise ApiError(413, "request_too_large", "Запрос слишком большой.")
        body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid_request", "Передайте корректный JSON.") from None


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

    Forwarded addresses are trusted only behind the authenticated site proxy.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and settings.api_proxy_token:
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
