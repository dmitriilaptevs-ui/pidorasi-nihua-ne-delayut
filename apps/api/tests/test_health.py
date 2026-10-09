"""Infrastructure milestone tests: liveness and readiness behaviour."""

from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main as main_module
from app.main import app
from app.settings import settings

client = TestClient(app)


def test_root_reports_service() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "rubai-api"


def test_healthz_is_dependency_free() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_dependency_state(monkeypatch) -> None:
    async def up() -> bool:
        return True

    async def down() -> bool:
        return False

    monkeypatch.setattr(main_module.db, "check_database", up)
    monkeypatch.setattr(main_module.redis_client, "check_redis", up)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True, "redis": True}

    monkeypatch.setattr(main_module.redis_client, "check_redis", down)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": True, "redis": False}


def test_readyz_degrades_when_database_is_down(monkeypatch) -> None:
    async def up() -> bool:
        return True

    async def down() -> bool:
        return False

    monkeypatch.setattr(main_module.db, "check_database", down)
    monkeypatch.setattr(main_module.redis_client, "check_redis", up)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": False, "redis": True}


def test_production_api_requires_valid_site_proxy_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "api_proxy_token", "")
    missing = client.get("/api/auth/providers")
    assert missing.status_code == 503
    assert missing.json()["error"]["code"] == "proxy_not_configured"

    monkeypatch.setattr(settings, "api_proxy_token", "configured-site-token")
    incorrect = client.get("/api/auth/providers", headers={"x-rubai-proxy-token": "wrong-token"})
    assert incorrect.status_code == 403
    assert incorrect.json()["error"]["code"] == "invalid_proxy"

    correct = client.get(
        "/api/auth/providers", headers={"x-rubai-proxy-token": "configured-site-token"}
    )
    assert correct.status_code == 200
    assert correct.json()["chatgpt"] is True


def test_client_ip_ignores_forwarded_header_without_configured_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_proxy_token", "")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.99")],
            "client": ("127.0.0.1", 4321),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )

    from app.deps import client_ip

    assert client_ip(request) == "127.0.0.1"
