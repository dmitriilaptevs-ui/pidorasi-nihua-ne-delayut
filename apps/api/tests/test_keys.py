"""API key lifecycle: show-once, digest-only storage, revocation, ownership."""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import TOKEN_RE


async def test_create_key_returns_raw_once_and_stores_only_digest(verified_client) -> None:
    response = await verified_client.post(
        "/api/keys", json={"name": "Cursor", "monthly_limit_kopecks": 300000}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    raw = body["key"]
    assert raw.startswith("sk-rubai-")
    assert body["item"]["prefix"] == raw[:16]
    assert body["item"]["monthly_limit_kopecks"] == 300000
    assert body["item"]["funding_source"] == "platform"

    from app.db import session_factory
    from app.models import ApiKey

    async with session_factory()() as db:
        row = (await db.execute(select(ApiKey))).scalar_one()
        assert row.key_hash != raw
        assert raw not in row.key_hash
        assert row.prefix == raw[:16]

    listing = await verified_client.get("/api/keys")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert "key" not in items[0]


async def test_revoked_key_stops_authenticating(verified_client) -> None:
    created = await verified_client.post("/api/keys", json={"name": "agent"})
    raw = created.json()["key"]
    key_id = created.json()["item"]["id"]

    from app.db import session_factory
    from app.services import keys as key_service

    async with session_factory()() as db:
        assert await key_service.authenticate_api_key(db, raw_key=raw) is not None

    revoked = await verified_client.post(f"/api/keys/{key_id}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["item"]["revoked_at"] is not None

    async with session_factory()() as db:
        assert await key_service.authenticate_api_key(db, raw_key=raw) is None
        assert await key_service.authenticate_api_key(db, raw_key="sk-rubai-not-a-real-key") is None


async def test_unverified_user_cannot_create_key(client) -> None:
    await client.post(
        "/api/auth/register", json={"email": "unverified@example.com", "password": "unverified-password-1"}
    )
    await client.post(
        "/api/auth/login", json={"email": "unverified@example.com", "password": "unverified-password-1"}
    )
    response = await client.post("/api/keys", json={"name": "x"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "email_not_verified"


async def test_cannot_revoke_another_users_key(verified_client, mailbox) -> None:
    created = await verified_client.post("/api/keys", json={"name": "mine"})
    key_id = created.json()["item"]["id"]

    await verified_client.post("/api/auth/logout")
    await verified_client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "second-password-1"}
    )
    match = TOKEN_RE.search(mailbox[-1]["text"])
    assert match is not None
    await verified_client.post("/api/auth/verify-email", json={"token": match.group(1)})
    await verified_client.post(
        "/api/auth/login", json={"email": "second@example.com", "password": "second-password-1"}
    )

    response = await verified_client.post(f"/api/keys/{key_id}/revoke")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "key_not_found"


async def test_key_name_and_limit_are_validated(verified_client) -> None:
    assert (await verified_client.post("/api/keys", json={"name": ""})).status_code == 422
    assert (await verified_client.post("/api/keys", json={"name": "ok", "monthly_limit_kopecks": -1})).status_code == 422
