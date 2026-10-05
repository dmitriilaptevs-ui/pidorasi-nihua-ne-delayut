"""OAuth flows: authorize URLs, handshake validation, account linking."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.services import oauth as oauth_service
from app.services.oauth import ProviderIdentity


@pytest.fixture(autouse=True)
def configured_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "vk_client_id", "vk-client")
    monkeypatch.setattr(settings, "vk_app_type", "public")
    monkeypatch.setattr(settings, "vk_service_token", "")
    monkeypatch.setattr(settings, "yandex_client_id", "ya-client")
    monkeypatch.setattr(settings, "yandex_client_secret", "")


async def start_oauth(client, provider: str = "vk") -> str:
    response = await client.post("/api/auth/start", json={"provider": provider, "variant": "canvas"})
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    assert client.cookies.get("rb_oauth_bind")
    return url


def state_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


async def test_start_builds_vk_authorize_url(client) -> None:
    url = await start_oauth(client, "vk")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "id.vk.ru"
    assert query["client_id"] == ["vk-client"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://localhost:3001/api/auth/callback/vk"]
    assert query["provider"] == ["vkid"]


async def test_start_builds_yandex_authorize_url(client) -> None:
    url = await start_oauth(client, "yandex")
    parsed = urlparse(url)
    assert parsed.netloc == "oauth.yandex.ru"
    assert parse_qs(parsed.query)["scope"] == ["login:info"]


async def test_start_rejects_unconfigured_provider(client, monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "vk_client_id", "")
    response = await client.post("/api/auth/start", json={"provider": "vk", "variant": "canvas"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider_not_configured"


async def test_vk_callback_creates_user_and_session(client, monkeypatch) -> None:
    async def fake_exchange_vk(*, code, verifier, device_id, state):  # noqa: ANN001
        assert code == "vk-code"
        assert device_id == "device-1"
        return ProviderIdentity(subject="42", display_name="Иван ВК", access_token="vk-token")

    monkeypatch.setattr(oauth_service, "exchange_vk", fake_exchange_vk)
    url = await start_oauth(client, "vk")
    state = state_from(url)

    response = await client.get(f"/api/auth/callback/vk?code=vk-code&state={state}&device_id=device-1")
    assert response.status_code == 303
    assert response.headers["location"] == "/canvas#workspace"
    assert client.cookies.get("rb_platform_session")

    from app.db import session_factory
    from app.models import User

    async with session_factory()() as db:
        result = await db.execute(select(User).where(User.oauth_provider == "vk"))
        user = result.scalar_one()
        assert user.oauth_subject == "42"
        assert user.display_name == "Иван ВК"
        assert user.email is None
        assert user.signup_method == "vk"


async def test_callback_rejects_replayed_state(client, monkeypatch) -> None:
    async def fake_exchange_vk(**_):  # noqa: ANN001
        return ProviderIdentity(subject="42", display_name="Иван")

    monkeypatch.setattr(oauth_service, "exchange_vk", fake_exchange_vk)
    url = await start_oauth(client, "vk")
    state = state_from(url)

    first = await client.get(f"/api/auth/callback/vk?code=c&state={state}&device_id=d")
    assert first.status_code == 303
    assert first.headers["location"] == "/canvas#workspace"

    second = await client.get(f"/api/auth/callback/vk?code=c&state={state}&device_id=d")
    assert second.headers["location"] == "/canvas?auth_error=state"


async def test_callback_requires_bind_cookie(client, monkeypatch) -> None:
    async def fake_exchange_vk(**_):  # noqa: ANN001
        return ProviderIdentity(subject="42", display_name="Иван")

    monkeypatch.setattr(oauth_service, "exchange_vk", fake_exchange_vk)
    url = await start_oauth(client, "vk")
    state = state_from(url)

    fresh = httpx.AsyncClient(transport=client._transport, base_url="http://localhost:3001")  # type: ignore[attr-defined]
    async with fresh:
        response = await fresh.get(f"/api/auth/callback/vk?code=c&state={state}&device_id=d")
    assert response.headers["location"] == "/canvas?auth_error=state"


async def test_callback_rejects_duplicate_parameters(client) -> None:
    response = await client.get("/api/auth/callback/vk?code=a&code=b&state=x")
    assert response.headers["location"] == "/canvas?auth_error=invalid_request"


async def test_callback_reports_unconfigured_provider(client, monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "vk_client_id", "")
    response = await client.get("/api/auth/callback/vk?code=a&state=x")
    assert response.headers["location"] == "/canvas?auth_error=unavailable"


async def test_yandex_callback_links_account(client, monkeypatch) -> None:
    async def fake_exchange_yandex(*, code, verifier):  # noqa: ANN001
        assert code == "ya-code"
        return ProviderIdentity(subject="777", display_name="Пётр", email="petr@example.com")

    monkeypatch.setattr(oauth_service, "exchange_yandex", fake_exchange_yandex)
    url = await start_oauth(client, "yandex")
    state = state_from(url)

    response = await client.get(f"/api/auth/callback/yandex?code=ya-code&state={state}")
    assert response.headers["location"] == "/canvas#workspace"

    from app.db import session_factory
    from app.models import User

    async with session_factory()() as db:
        result = await db.execute(select(User).where(User.oauth_provider == "yandex"))
        user = result.scalar_one()
        assert user.email == "petr@example.com"
        assert user.email_verified_at is not None


async def test_callback_provider_error_is_mapped(client) -> None:
    url = await start_oauth(client, "vk")
    state = state_from(url)
    response = await client.get(f"/api/auth/callback/vk?error=access_denied&state={state}")
    assert response.headers["location"] == "/canvas?auth_error=denied"
