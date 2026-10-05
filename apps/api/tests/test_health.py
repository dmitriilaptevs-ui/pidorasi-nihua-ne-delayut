"""Infrastructure milestone tests: liveness and readiness behaviour."""

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app

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
