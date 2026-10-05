"""Identity API router: email+password registration and sessions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import (
    clear_session_cookie,
    client_ip,
    current_user,
    get_db,
    require_origin,
    set_session_cookie,
)
from ..errors import ApiError
from ..mail import get_mailer
from ..models import User
from ..schemas import ForgotRequest, LoginRequest, RegisterRequest, ResetRequest, TokenRequest, UserView
from ..services import identity as service
from ..settings import settings
from ..throttle import allow

router = APIRouter(prefix="/api/auth", tags=["identity"])


async def _throttled(key: str, limit: int, minutes: int) -> None:
    if not await allow(key, limit, minutes * 60):
        raise ApiError(429, "rate_limited", "Слишком много попыток. Попробуйте позже.")


async def _send_verification(user: User, token: str) -> None:
    link = f"{settings.public_origin}/verify-email?token={token}"
    await get_mailer().send(
        to=user.email,
        subject="Подтверждение адреса — AI API Platform",
        text=(
            "Здравствуйте!\n\n"
            "Подтвердите адрес электронной почты по ссылке:\n"
            f"{link}\n\n"
            f"Ссылка действует {settings.email_verify_ttl_hours} ч.\n"
            "Если вы не регистрировались, просто проигнорируйте это письмо.\n"
        ),
    )


async def _send_reset(user: User, token: str) -> None:
    link = f"{settings.public_origin}/reset-password?token={token}"
    await get_mailer().send(
        to=user.email,
        subject="Сброс пароля — AI API Platform",
        text=(
            "Здравствуйте!\n\n"
            "Ссылка для сброса пароля:\n"
            f"{link}\n\n"
            f"Ссылка действует {settings.password_reset_ttl_hours} ч и отзывает все активные сессии.\n"
            "Если вы не запрашивали сброс, просто проигнорируйте это письмо.\n"
        ),
    )


@router.get("/providers")
async def providers() -> dict[str, bool]:
    return {
        "email_password": True,
        "vk": bool(settings.vk_client_id),
        "yandex": bool(settings.yandex_client_id),
    }


@router.post("/register", status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    _: None = Depends(require_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    await _throttled(
        f"throttle:register:{client_ip(request)}",
        settings.throttle_register_limit,
        settings.throttle_register_window_minutes,
    )
    user, token = await service.register(db, email=payload.email, password=payload.password)
    await _send_verification(user, token)
    await db.commit()
    return {"user": UserView.of(user), "verification_sent": True}


@router.post("/verify-email")
async def verify_email(
    payload: TokenRequest,
    _: None = Depends(require_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    user = await service.verify_email(db, token=payload.token)
    await db.commit()
    return {"user": UserView.of(user)}


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    _: None = Depends(require_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    await _throttled(
        f"throttle:login:{service.normalize_email(payload.email)}:{client_ip(request)}",
        settings.throttle_login_limit,
        settings.throttle_login_window_minutes,
    )
    user = await service.authenticate(db, email=payload.email, password=payload.password)
    if user is None:
        raise ApiError(401, "invalid_credentials", "Неверный адрес или пароль.")
    if user.status != "active":
        raise ApiError(403, "account_blocked", "Аккаунт заблокирован.")
    _, token = await service.create_session(db, user=user, method="password")
    await db.commit()
    set_session_cookie(response, token)
    return {"user": UserView.of(user)}


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    _: None = Depends(require_origin),
    db: AsyncSession = Depends(get_db),
) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await service.revoke_session(db, token=token)
        await db.commit()
    clear_session_cookie(response)
    return None


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict[str, object]:
    return {"user": UserView.of(user)}


@router.post("/password/forgot", status_code=202)
async def forgot_password(
    payload: ForgotRequest,
    request: Request,
    _: None = Depends(require_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    await _throttled(
        f"throttle:reset:{client_ip(request)}",
        settings.throttle_reset_limit,
        settings.throttle_reset_window_minutes,
    )
    result = await service.request_password_reset(db, email=payload.email)
    if result is not None:
        user, token = result
        await _send_reset(user, token)
    await db.commit()
    # Never reveal whether the address exists.
    return {"ok": True}


@router.post("/password/reset")
async def reset_password(
    payload: ResetRequest,
    _: None = Depends(require_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    user = await service.reset_password(db, token=payload.token, new_password=payload.password)
    await db.commit()
    return {"ok": True, "email": user.email, "sessions_revoked": True}
