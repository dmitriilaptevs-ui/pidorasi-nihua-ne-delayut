"""Authenticated agent routes and their isolated runtime boundary."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from app.api.agents import MAX_BODY_BYTES


async def create_model(*, priced: bool = True) -> None:
    from app.db import session_factory
    from app.models import CatalogModel, CatalogPricing

    async with session_factory()() as db:
        model = CatalogModel(
            openrouter_id="acme/agent-test",
            name="Agent Test",
            provider="acme",
            context_length=4096,
            supports_tools=False,
            available=True,
        )
        db.add(model)
        await db.flush()
        if priced:
            db.add(
                CatalogPricing(
                    model_id=model.id,
                    version=1,
                    input_usd_per_mtok=Decimal("1"),
                    output_usd_per_mtok=Decimal("1"),
                    cached_usd_per_mtok=None,
                    fx_rate=Decimal("100"),
                    markup=Decimal("1"),
                    input_rub_per_mtok=Decimal("100"),
                    output_rub_per_mtok=Decimal("100"),
                    cached_rub_per_mtok=None,
                )
            )
        await db.commit()


async def make_run_key(client, *, funding_source: str = "platform") -> str:
    if funding_source == "customer":
        credential = await client.put(
            "/api/provider-credentials", json={"api_key": "customer-provider-test-key"}
        )
        assert credential.status_code == 200, credential.text
    created = await client.post("/api/keys", json={"name": "agent-test", "funding_source": funding_source})
    assert created.status_code == 201, created.text
    return created.json()["key"]


def run_payload(api_key: str) -> dict[str, str]:
    return {
        "engine": "hermes",
        "api_key": api_key,
        "model": "acme/agent-test",
        "prompt": "Say hello.",
    }


class RuntimeClient:
    def __init__(self, *, response: httpx.Response | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url: str, **kwargs) -> httpx.Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response

    async def post(self, url: str, **kwargs) -> httpx.Response:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def mock_runtime(monkeypatch, runtime: RuntimeClient) -> None:
    from app.api import agents

    monkeypatch.setattr(agents, "httpx", type("Httpx", (), {"AsyncClient": lambda *args, **kwargs: runtime, "Timeout": httpx.Timeout, "TimeoutException": httpx.TimeoutException, "HTTPError": httpx.HTTPError}))
    monkeypatch.setattr(agents.settings, "agent_runtime_url", "http://localhost:8787")
    monkeypatch.setattr(agents.settings, "agent_runtime_token", "runtime-test-token")


async def test_run_rejects_another_users_platform_key(verified_client) -> None:
    from app.db import session_factory
    from app.services import identity as identity_service
    from app.services import keys as key_service

    async with session_factory()() as db:
        other_user, _ = await identity_service.register(
            db, email="other-agent-owner@example.com", password="other-agent-password-1"
        )
        other_user.email_verified_at = identity_service.utcnow()
        await db.flush()
        _, other_key = await key_service.create_key(db, user=other_user, name="other-agent")
        await db.commit()

    response = await verified_client.post("/api/agents/run", json=run_payload(other_key))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


async def test_run_rejects_revoked_key(verified_client) -> None:
    key = await make_run_key(verified_client)
    listed = await verified_client.get("/api/keys")
    key_id = listed.json()["items"][0]["id"]
    revoked = await verified_client.post(f"/api/keys/{key_id}/revoke")
    assert revoked.status_code == 200

    response = await verified_client.post("/api/agents/run", json=run_payload(key))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


async def test_customer_key_can_run_model_without_platform_pricing(verified_client, monkeypatch) -> None:
    await create_model(priced=False)
    key = await make_run_key(verified_client, funding_source="customer")
    runtime = RuntimeClient(response=httpx.Response(200, json={"text": "BYOK works"}))
    mock_runtime(monkeypatch, runtime)

    response = await verified_client.post("/api/agents/run", json=run_payload(key))
    assert response.status_code == 200, response.text
    assert response.json() == {"engine": "hermes", "text": "BYOK works"}
    assert runtime.calls[0]["json"]["api_key"] == key


async def test_run_accepts_max_length_escaped_unicode_prompt(verified_client, monkeypatch) -> None:
    await create_model()
    key = await make_run_key(verified_client)
    prompt = "ࠀ" * 12_000
    body = run_payload(key)
    body["prompt"] = prompt
    content = json.dumps(body, ensure_ascii=True).encode("utf-8")
    assert len(content) > 16 * 1024
    assert len(content) <= MAX_BODY_BYTES
    runtime = RuntimeClient(response=httpx.Response(200, json={"text": "accepted"}))
    mock_runtime(monkeypatch, runtime)

    response = await verified_client.post(
        "/api/agents/run", content=content, headers={"content-type": "application/json"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"engine": "hermes", "text": "accepted"}
    assert runtime.calls[0]["json"]["prompt"] == prompt


async def test_authenticated_agent_list_reports_runtime_health(verified_client, monkeypatch) -> None:
    runtime = RuntimeClient(response=httpx.Response(200, json={"agents": {"hermes": True, "pi": False}}))
    mock_runtime(monkeypatch, runtime)

    response = await verified_client.get("/api/agents")
    assert response.status_code == 200
    assert [(item["id"], item["available"]) for item in response.json()["items"]] == [
        ("hermes", True), ("pi", False)
    ]
    assert runtime.calls[0]["url"] == "http://localhost:8787/health"
    assert runtime.calls[0]["headers"]["authorization"] == "Bearer runtime-test-token"


async def test_agent_list_requires_authentication(client) -> None:
    response = await client.get("/api/agents")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize(
    ("body", "expected_status", "expected_code"),
    [
        (b"x" * (MAX_BODY_BYTES + 1), 413, "request_too_large"),
        (b'{"engine":"unknown"}', 400, "invalid_request"),
    ],
    ids=["oversized-body", "invalid-payload"],
)
async def test_run_bounds_and_validates_request_body(verified_client, body, expected_status, expected_code) -> None:
    response = await verified_client.post("/api/agents/run", content=body)
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


async def test_run_maps_runtime_timeout_and_failure(verified_client, monkeypatch) -> None:
    await create_model()
    key = await make_run_key(verified_client)

    mock_runtime(monkeypatch, RuntimeClient(error=httpx.ReadTimeout("runtime timed out")))
    timed_out = await verified_client.post("/api/agents/run", json=run_payload(key))
    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "agent_timeout"

    mock_runtime(monkeypatch, RuntimeClient(response=httpx.Response(500, json={"detail": "secret runtime failure"})))
    failed = await verified_client.post("/api/agents/run", json=run_payload(key))
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "agent_failed"
    assert "secret runtime failure" not in failed.text


async def test_run_rejects_oversized_runtime_text(verified_client, monkeypatch) -> None:
    await create_model()
    key = await make_run_key(verified_client)
    mock_runtime(monkeypatch, RuntimeClient(response=httpx.Response(200, json={"text": "x" * 64_001})))

    response = await verified_client.post("/api/agents/run", json=run_payload(key))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_failed"
