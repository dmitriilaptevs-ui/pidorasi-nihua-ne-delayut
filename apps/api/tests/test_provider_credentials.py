"""Customer OpenRouter credential isolation and encrypted storage."""

from __future__ import annotations

import pytest
from sqlalchemy import select


async def test_credentials_are_encrypted_hidden_and_deleted(verified_client) -> None:
    from app.db import session_factory
    from app.errors import ApiError
    from app.models import ProviderCredential
    from app.services import identity as identity_service
    from app.services import provider_credentials as credential_service
    from app.services.provider_credentials import decrypt_key

    secret = "sk-or-v1-sensitive-provider-value"
    initial = await verified_client.get("/api/provider-credentials")
    assert initial.json() == {"configured": False, "suffix": None, "updated_at": None}

    saved = await verified_client.put("/api/provider-credentials", json={"api_key": secret})
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["suffix"] == secret[-4:]
    assert secret not in saved.text

    async with session_factory()() as db:
        credential = (await db.execute(select(ProviderCredential))).scalar_one()
        assert secret.encode() not in credential.encrypted_key
        assert decrypt_key(credential) == secret
        other_user, _ = await identity_service.register(
            db, email="unrelated@example.com", password="unrelated-password-1"
        )
        assert await credential_service.get_credential(db, user_id=other_user.id) is None
        credential.user_id = other_user.id
        with pytest.raises(ApiError):
            decrypt_key(credential)

    deleted = await verified_client.delete("/api/provider-credentials")
    assert deleted.status_code == 200
    assert deleted.json() == {"configured": False, "suffix": None, "updated_at": None}
    assert (await verified_client.get("/api/provider-credentials")).json()["configured"] is False


async def test_customer_funding_requires_credential_and_origin_check(verified_client) -> None:
    missing = await verified_client.post("/api/keys", json={"name": "customer", "funding_source": "customer"})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "provider_credential_required"

    secret = "do-not-return-this-value"
    invalid = await verified_client.put("/api/provider-credentials", json={"api_key": secret}, headers={"origin": "https://evil.example"})
    assert invalid.status_code == 403
    assert secret not in invalid.text

    saved = await verified_client.put("/api/provider-credentials", json={"api_key": secret})
    assert saved.status_code == 200
    created = await verified_client.post("/api/keys", json={"name": "customer", "funding_source": "customer"})
    assert created.status_code == 201
    assert created.json()["item"]["funding_source"] == "customer"


@pytest.mark.parametrize("bad_value", [None, 12, "", "secret-value-" * 100])
async def test_credential_validation_does_not_echo_input(verified_client, bad_value) -> None:
    response = await verified_client.put("/api/provider-credentials", json={"api_key": bad_value})
    assert response.status_code == 400
    if isinstance(bad_value, str):
        assert bad_value not in response.text
