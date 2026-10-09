"""Identity flows: registration, verification, login, reset, RBAC, limits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from tests.conftest import TOKEN_RE

PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "a-different-long-password"


def token_from(mail: dict[str, str]) -> str:
    match = TOKEN_RE.search(mail["text"])
    assert match is not None, mail["text"]
    return match.group(1)


async def register(client, email: str = "user@example.com", password: str = PASSWORD):
    return await client.post("/api/auth/register", json={"email": email, "password": password})


async def test_register_creates_unverified_user(client, mailbox) -> None:
    response = await register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "user@example.com"
    assert body["user"]["email_verified"] is False
    assert body["verification_sent"] is True
    assert mailbox and mailbox[0]["to"] == "user@example.com"


async def test_register_rejects_duplicate_email(client, mailbox) -> None:
    assert (await register(client)).status_code == 201
    duplicate = await register(client)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "email_taken"


async def test_register_normalizes_email_case(client, mailbox) -> None:
    assert (await register(client, email="User@Example.COM")).status_code == 201
    duplicate = await register(client, email="user@example.com")
    assert duplicate.status_code == 409


async def test_verify_email_is_single_use(client, mailbox) -> None:
    await register(client)
    token = token_from(mailbox[-1])

    first = await client.post("/api/auth/verify-email", json={"token": token})
    assert first.status_code == 200
    assert first.json()["user"]["email_verified"] is True

    second = await client.post("/api/auth/verify-email", json={"token": token})
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "token_used"


async def test_expired_verification_token_is_rejected(client, mailbox) -> None:
    from app.db import session_factory
    from app.models import EmailToken

    await register(client)
    token = token_from(mailbox[-1])
    async with session_factory()() as db:
        await db.execute(update(EmailToken).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()

    response = await client.post("/api/auth/verify-email", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "token_expired"


async def test_login_me_logout_cycle(client, mailbox) -> None:
    await register(client)
    await client.post("/api/auth/verify-email", json={"token": token_from(mailbox[-1])})

    wrong = await client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong-password"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_credentials"

    login = await client.post("/api/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert login.status_code == 200
    assert login.cookies.get("rb_platform_session")

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "user@example.com"

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    after = await client.get("/api/auth/me")
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "not_authenticated"


async def test_session_expiry_is_enforced(client, mailbox) -> None:
    from app.db import session_factory
    from app.models import AuthSession

    await register(client)
    await client.post("/api/auth/verify-email", json={"token": token_from(mailbox[-1])})
    await client.post("/api/auth/login", json={"email": "user@example.com", "password": PASSWORD})

    async with session_factory()() as db:
        await db.execute(update(AuthSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()

    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_invalid"


async def test_password_reset_revokes_sessions(client, mailbox) -> None:
    await register(client)
    await client.post("/api/auth/verify-email", json={"token": token_from(mailbox[-1])})
    await client.post("/api/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert (await client.get("/api/auth/me")).status_code == 200

    forgot = await client.post("/api/auth/password/forgot", json={"email": "user@example.com"})
    assert forgot.status_code == 202
    reset_token = token_from(mailbox[-1])

    reset = await client.post(
        "/api/auth/password/reset", json={"token": reset_token, "password": NEW_PASSWORD}
    )
    assert reset.status_code == 200
    assert reset.json()["sessions_revoked"] is True

    # The pre-reset cookie is dead.
    assert (await client.get("/api/auth/me")).status_code == 401

    assert (
        await client.post("/api/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    ).status_code == 401
    assert (
        await client.post("/api/auth/login", json={"email": "user@example.com", "password": NEW_PASSWORD})
    ).status_code == 200


async def test_forgot_password_does_not_reveal_accounts(client, mailbox) -> None:
    response = await client.post("/api/auth/password/forgot", json={"email": "nobody@example.com"})
    assert response.status_code == 202
    assert response.json() == {"ok": True}
    assert mailbox == []


async def test_foreign_origin_is_rejected(client, mailbox) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


async def test_login_throttle_blocks_after_limit(client, mailbox, monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "throttle_login_limit", 3)
    await register(client)
    await client.post("/api/auth/verify-email", json={"token": token_from(mailbox[-1])})

    for _ in range(3):
        response = await client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong"})
        assert response.status_code == 401

    blocked = await client.post("/api/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


async def test_admin_rbac(client, mailbox) -> None:
    from app.db import session_factory
    from app.services import identity as service

    await register(client)
    await client.post("/api/auth/verify-email", json={"token": token_from(mailbox[-1])})
    await client.post("/api/auth/login", json={"email": "user@example.com", "password": PASSWORD})

    assert (await client.get("/api/admin/whoami")).status_code == 403

    async with session_factory()() as db:
        await service.promote_admin(db, email="user@example.com")
        await db.commit()

    response = await client.get("/api/admin/whoami")
    assert response.status_code == 200
    assert response.json() == {"admin": "user@example.com"}


async def test_me_requires_session(client) -> None:
    response = await client.get("/api/auth/me")
    assert response.status_code == 401

async def test_site_sign_in_requires_trusted_proxy_and_identity(client, monkeypatch) -> None:
    from app.settings import settings
    from app.deps import API_PROXY_HEADER
    monkeypatch.setattr(settings, 'api_proxy_token', 'test-site-proxy')
    missing = await client.get('/api/auth/sites', headers={'x-rubai-sites-user-id':'visitor-a'})
    assert missing.status_code == 403
    missing_identity = await client.get('/api/auth/sites', headers={API_PROXY_HEADER:'test-site-proxy'})
    assert missing_identity.status_code == 401
    headers = {API_PROXY_HEADER:'test-site-proxy', 'x-rubai-sites-user-id':'visitor-a', 'x-rubai-sites-user-email':'visitor@example.com'}
    signed_in = await client.get('/api/auth/sites', headers=headers)
    assert signed_in.status_code == 303
    assert signed_in.headers['location'] == '/account'
    assert 'httponly' in signed_in.headers['set-cookie'].lower()
    me = await client.get('/api/auth/me', headers={API_PROXY_HEADER:'test-site-proxy'})
    assert me.status_code == 200
    first_id = me.json()['user']['id']
    assert me.json()['user']['email_verified'] is True
    await client.get('/api/auth/sites', headers=headers)
    assert (await client.get('/api/auth/me', headers={API_PROXY_HEADER:'test-site-proxy'})).json()['user']['id'] == first_id


async def test_site_identity_does_not_link_account_by_email(verified_client, monkeypatch) -> None:
    from app.settings import settings
    from app.deps import API_PROXY_HEADER
    original = (await verified_client.get('/api/auth/me')).json()['user']['id']
    monkeypatch.setattr(settings, 'api_proxy_token', 'test-site-proxy')
    response = await verified_client.get('/api/auth/sites', headers={API_PROXY_HEADER:'test-site-proxy','x-rubai-sites-user-id':'different-visitor','x-rubai-sites-user-email':'verified@example.com'})
    assert response.status_code == 303
    user = (await verified_client.get('/api/auth/me',headers={API_PROXY_HEADER:'test-site-proxy'})).json()['user']
    assert user['id'] != original
    assert user['email'] is None
