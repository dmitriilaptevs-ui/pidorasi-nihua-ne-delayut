"""OAuth endpoints: start (returns provider URL) and callback (creates session).

Paths intentionally match the previous lab registration:
`/api/auth/start` and `/api/auth/callback/{provider}`, so existing VK/Yandex
application redirect URIs keep working.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import (
    clear_session_cookie,
    client_ip,
    get_db,
    require_origin,
    set_session_cookie,
)
from ..errors import ApiError
from ..schemas import StartRequest
from ..services import identity as identity_service
from ..services import oauth as oauth_service
from ..settings import settings
from ..throttle import allow

router = APIRouter(prefix="/api/auth", tags=["oauth"])

BIND_MAX_AGE_SECONDS = 600


def _secure() -> bool:
    return settings.public_origin.startswith("https:")


def _set_bind_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.oauth_bind_cookie_name,
        value=token,
        max_age=BIND_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


def _clear_bind_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.oauth_bind_cookie_name,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


@router.post("/start")
async def start(
    payload: StartRequest,
    request: Request,
    response: Response,
    _: None = Depends(require_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if not await allow(
        f"throttle:oauth:{client_ip(request)}",
        settings.throttle_login_limit,
        settings.throttle_login_window_minutes * 60,
    ):
        raise ApiError(429, "rate_limited", "Слишком много попыток. Попробуйте позже.")
    if not oauth_service.provider_ready(payload.provider):
        raise ApiError(400, "provider_not_configured", "Этот способ входа ещё не настроен на сервере.")
    started = await oauth_service.create_handshake(db, provider=payload.provider, variant=payload.variant)
    await db.commit()
    _set_bind_cookie(response, started.bind_token)
    return {"url": started.url}


@router.get("/callback/{provider}")
async def callback(provider: str, request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    bind_token = request.cookies.get(settings.oauth_bind_cookie_name)
    try:
        user, variant = await oauth_service.complete_handshake(
            db, provider=provider, params=request.query_params, bind_token=bind_token
        )
        _, token = await identity_service.create_session(db, user=user, method=provider)
        await db.commit()
    except ApiError as exc:
        await db.rollback()
        variant = getattr(exc, "variant", "canvas")
        redirect = RedirectResponse(url=f"/{variant}?auth_error={exc.code}", status_code=303)
        _clear_bind_cookie(redirect)
        return redirect

    redirect = RedirectResponse(url=f"/{variant}#workspace", status_code=303)
    set_session_cookie(redirect, token)
    _clear_bind_cookie(redirect)
    return redirect
