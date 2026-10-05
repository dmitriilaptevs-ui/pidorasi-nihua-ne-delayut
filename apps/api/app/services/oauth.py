"""OAuth flows for VK ID and Yandex ID.

Handshakes are durable rows: state, PKCE verifier and the browser-binding hash.
Consumption happens under a row lock *before* any provider network call, so a
replayed state can never reach a token endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import OAuthHandshake, User
from ..security import new_opaque_token, token_digest
from ..settings import settings
from . import identity as identity_service

PROVIDER_TIMEOUT_SECONDS = 8.0
MAX_PROVIDER_BODY_BYTES = 64 * 1024
DUPLICATE_GUARDED_PARAMS = ("code", "state", "device_id", "error")


class OAuthFlowError(ApiError):
    """ApiError carrying the variant so the callback can redirect back into it."""

    def __init__(self, code: str, message: str, variant: str = "canvas") -> None:
        super().__init__(400, code, message)
        self.variant = variant


@dataclass(frozen=True)
class StartedOAuth:
    url: str
    bind_token: str


@dataclass(frozen=True)
class ProviderIdentity:
    subject: str
    display_name: str
    email: str | None = None
    access_token: str | None = None


def provider_ready(provider: str) -> bool:
    if provider == "vk":
        if not settings.vk_client_id or settings.vk_app_type not in ("public", "confidential"):
            return False
        return settings.vk_app_type == "public" or bool(settings.vk_service_token)
    if provider == "yandex":
        return bool(settings.yandex_client_id)
    return False


def redirect_uri(provider: str) -> str:
    return f"{settings.public_origin}/api/auth/callback/{provider}"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(provider: str, state: str, challenge: str) -> str:
    common = {
        "response_type": "code",
        "client_id": settings.vk_client_id if provider == "vk" else settings.yandex_client_id,
        "redirect_uri": redirect_uri(provider),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider == "vk":
        common.update({"provider": "vkid", "scope": "vkid.personal_info"})
        return "https://id.vk.ru/authorize?" + urlencode(common)
    common["scope"] = "login:info"
    return "https://oauth.yandex.ru/authorize?" + urlencode(common)


async def create_handshake(db: AsyncSession, *, provider: str, variant: str) -> StartedOAuth:
    state = new_opaque_token()
    bind_token = new_opaque_token()
    verifier, challenge = _pkce()
    now = identity_service.utcnow()
    db.add(
        OAuthHandshake(
            state_hash=token_digest(state),
            provider=provider,
            variant=variant,
            code_verifier=verifier,
            bind_hash=token_digest(bind_token),
            expires_at=now + timedelta(minutes=settings.oauth_handshake_ttl_minutes),
        )
    )
    await db.flush()
    return StartedOAuth(url=authorize_url(provider, state, challenge), bind_token=bind_token)


async def complete_handshake(
    db: AsyncSession, *, provider: str, params, bind_token: str | None
) -> tuple[User, str]:
    """Validate a provider callback and return the linked user plus variant."""
    if provider not in ("vk", "yandex"):
        raise OAuthFlowError("invalid_request", "Неизвестный провайдер входа.")
    if not provider_ready(provider):
        raise OAuthFlowError("unavailable", "Этот способ входа ещё не настроен.")

    for name in DUPLICATE_GUARDED_PARAMS:
        if len(params.getlist(name)) > 1:
            raise OAuthFlowError("invalid_request", "Повторяющиеся параметры входа.")

    state = params.get("state") or ""
    if len(state) != 43:
        raise OAuthFlowError("state", "Проверка состояния входа не прошла.")

    result = await db.execute(
        select(OAuthHandshake).where(OAuthHandshake.state_hash == token_digest(state)).with_for_update()
    )
    handshake = result.scalar_one_or_none()
    if handshake is None or handshake.used_at is not None or handshake.provider != provider:
        raise OAuthFlowError("state", "Проверка состояния входа не прошла.")
    if handshake.expires_at <= identity_service.utcnow():
        raise OAuthFlowError("state", "Время ожидания входа истекло.", handshake.variant)

    variant = handshake.variant
    # A valid browser-binding cookie proves the callback belongs to this browser.
    if not bind_token or token_digest(bind_token) != handshake.bind_hash:
        raise OAuthFlowError("state", "Проверка безопасности входа не прошла.", variant)

    # One-use consume before any provider network call.
    handshake.used_at = identity_service.utcnow()
    await db.flush()

    if params.get("error"):
        raise OAuthFlowError("denied", "Вход отменён или не разрешён провайдером.", variant)

    code = params.get("code") or ""
    if not code or len(code) > 2048:
        raise OAuthFlowError("invalid_request", "Сервис входа вернул неполные данные.", variant)

    try:
        if provider == "vk":
            device_id = params.get("device_id") or ""
            if not device_id or len(device_id) > 256:
                raise OAuthFlowError("invalid_request", "Сервис входа не передал идентификатор устройства.", variant)
            identity = await exchange_vk(
                code=code, verifier=handshake.code_verifier, device_id=device_id, state=state
            )
        else:
            identity = await exchange_yandex(code=code, verifier=handshake.code_verifier)
    except OAuthFlowError:
        raise
    except Exception:  # noqa: BLE001 - provider failures must not leak details
        raise OAuthFlowError("provider", "Сервис входа временно недоступен.", variant) from None

    user = await link_identity(db, provider=provider, identity=identity)
    return user, variant


async def link_identity(db: AsyncSession, *, provider: str, identity: ProviderIdentity) -> User:
    result = await db.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_subject == identity.subject)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            email=identity.email,
            display_name=identity.display_name,
            signup_method=provider,
            oauth_provider=provider,
            oauth_subject=identity.subject,
            email_verified_at=identity_service.utcnow() if identity.email else None,
        )
        db.add(user)
        await db.flush()
    elif identity.display_name and user.display_name != identity.display_name:
        user.display_name = identity.display_name
    return user


# ---------------------------------------------------------------------------
# Provider HTTP exchange
# ---------------------------------------------------------------------------


async def _post_form(url: str, form: dict[str, str]) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.post(url, data=form, headers={"content-type": "application/x-www-form-urlencoded"})
    if response.status_code >= 400:
        raise OAuthFlowError("provider", "Сервис входа отклонил обмен кода.")
    body = response.content
    if len(body) > MAX_PROVIDER_BODY_BYTES:
        raise OAuthFlowError("invalid_response", "Ответ сервиса входа слишком большой.")
    try:
        payload = response.json()
    except ValueError:
        raise OAuthFlowError("invalid_response", "Сервис входа вернул некорректный ответ.") from None
    if not isinstance(payload, dict):
        raise OAuthFlowError("invalid_response", "Сервис входа вернул некорректный ответ.")
    return payload


def _as_string(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _display_name(value: object, fallback: str) -> str:
    name = _as_string(value).strip()
    return name[:80] if name else fallback


async def exchange_vk(*, code: str, verifier: str, device_id: str, state: str) -> ProviderIdentity:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri("vk"),
        "client_id": settings.vk_client_id,
        "device_id": device_id,
        "state": state,
    }
    if settings.vk_app_type == "confidential":
        form["service_token"] = settings.vk_service_token
    token_payload = await _post_form("https://id.vk.ru/oauth2/auth", form)
    access_token = _as_string(token_payload.get("access_token"))
    if not access_token:
        raise OAuthFlowError("invalid_response", "Сервис входа не выдал токен.")
    returned_state = _as_string(token_payload.get("state"))
    if returned_state and returned_state != state:
        raise OAuthFlowError("state", "Проверка состояния входа не прошла.")
    token_user_id = _as_string(token_payload.get("user_id")) or None

    info = await _post_form(
        "https://id.vk.ru/oauth2/user_info",
        {"client_id": settings.vk_client_id, "access_token": access_token},
    )
    raw_user = info.get("user")
    if not isinstance(raw_user, dict):
        raise OAuthFlowError("invalid_response", "Сервис входа вернул некорректный профиль.")
    user_id = _as_string(raw_user.get("user_id"))
    if not user_id:
        raise OAuthFlowError("invalid_response", "Сервис входа вернул некорректный профиль.")
    if token_user_id and token_user_id != user_id:
        raise OAuthFlowError("invalid_response", "Профиль не совпал с выданным токеном.")
    name = _display_name(
        " ".join(part for part in (_as_string(raw_user.get("first_name")), _as_string(raw_user.get("last_name"))) if part),
        "VK ID",
    )
    email = _as_string(raw_user.get("email")) or None
    return ProviderIdentity(subject=user_id, display_name=name, email=email, access_token=access_token)


async def exchange_yandex(*, code: str, verifier: str) -> ProviderIdentity:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.yandex_client_id,
        "code_verifier": verifier,
    }
    if settings.yandex_client_secret:
        form["client_secret"] = settings.yandex_client_secret
    token_payload = await _post_form("https://oauth.yandex.ru/token", form)
    access_token = _as_string(token_payload.get("access_token"))
    if not access_token:
        raise OAuthFlowError("invalid_response", "Сервис входа не выдал токен.")

    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.get(
            "https://login.yandex.ru/info",
            params={"format": "json"},
            headers={"authorization": f"OAuth {access_token}"},
        )
    if response.status_code >= 400:
        raise OAuthFlowError("provider", "Сервис входа отклонил запрос профиля.")
    if len(response.content) > MAX_PROVIDER_BODY_BYTES:
        raise OAuthFlowError("invalid_response", "Ответ сервиса входа слишком большой.")
    try:
        payload = response.json()
    except ValueError:
        raise OAuthFlowError("invalid_response", "Сервис входа вернул некорректный профиль.") from None
    if not isinstance(payload, dict):
        raise OAuthFlowError("invalid_response", "Сервис входа вернул некорректный профиль.")

    client_id = _as_string(payload.get("client_id"))
    if not client_id or client_id != settings.yandex_client_id:
        raise OAuthFlowError("invalid_response", "Профиль принадлежит другому приложению.")
    user_id = _as_string(payload.get("id"))
    if not user_id.isdigit() or len(user_id) > 32:
        raise OAuthFlowError("invalid_response", "Сервис входа вернул некорректный профиль.")
    name = _display_name(
        _as_string(payload.get("real_name")) or _as_string(payload.get("display_name")) or _as_string(payload.get("login")),
        "Яндекс ID",
    )
    email = _as_string(payload.get("default_email")) or None
    return ProviderIdentity(subject=user_id, display_name=name, email=email, access_token=access_token)
