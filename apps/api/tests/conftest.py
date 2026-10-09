"""Test bootstrap: real PostgreSQL via pgserver, fakeredis, captured mail.

Environment is prepared in `pytest_configure` *before* any test module imports
`app.*`, so `app.settings` reads the test database URL.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
from app.local_postgres import start_postgres

TOKEN_RE = re.compile(r"token=([A-Za-z0-9_-]{43})")

_BOX: list[dict[str, str]] = []


def pytest_configure(config: pytest.Config) -> None:
    data_dir = tempfile.mkdtemp(prefix="rubai-pg-")
    server = start_postgres(data_dir)
    os.environ["DATABASE_URL"] = server.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)
    os.environ["APP_ENV"] = "test"
    os.environ["PUBLIC_ORIGIN"] = "http://localhost:3001"
    os.environ["MAIL_TRANSPORT"] = "console"
    os.environ["PROVIDER_KEY_ENCRYPTION_KEY"] = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    os.environ.setdefault("SESSION_SECRET", "test-only-secret-not-used-by-api")

    from alembic import command
    from alembic.config import Config

    here = os.path.dirname(os.path.abspath(__file__))
    cfg = Config(os.path.join(here, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")

class RecordingMailer:
    def __init__(self, box: list[dict[str, str]]) -> None:
        self.box = box

    async def send(self, *, to: str, subject: str, text: str) -> None:
        self.box.append({"to": to, "subject": subject, "text": text})


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import throttle

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(throttle, "client", lambda: fake)


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    from app.api import identity as identity_module

    box: list[dict[str, str]] = []
    monkeypatch.setattr(identity_module, "get_mailer", lambda: RecordingMailer(box))
    return box


@pytest.fixture
async def client() -> Any:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3001") as test_client:
        yield test_client


@pytest.fixture
async def verified_client(client: Any, mailbox: list[dict[str, str]]) -> Any:
    """A client whose user is registered, verified and signed in."""
    email = "verified@example.com"
    password = "verified-password-1"
    await client.post("/api/auth/register", json={"email": email, "password": password})
    match = TOKEN_RE.search(mailbox[-1]["text"])
    assert match is not None
    await client.post("/api/auth/verify-email", json={"token": match.group(1)})
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return client


@pytest.fixture(autouse=True)
async def clean_database() -> Any:
    from sqlalchemy import text

    from app.db import session_factory

    async def truncate() -> None:
        async with session_factory()() as db:
            await db.execute(
                text(
                    "TRUNCATE TABLE email_tokens, auth_sessions, oauth_handshakes, provider_credentials, api_keys, "
                    "catalog_pricing, catalog_models, ledger_postings, ledger_transactions, "
                    "reconciliation_items, reserves, wallets, payments, api_requests, users CASCADE"
                )
            )
            await db.commit()

    await truncate()
    yield
    await truncate()
