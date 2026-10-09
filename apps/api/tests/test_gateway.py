"""OpenAI-compatible gateway: auth, reserve ordering, metering, stream outcomes."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.services import gateway as gw

UPSTREAM_KEY = "test-upstream-key"


class Behavior:
    """What the mocked OpenRouter should do for the current test."""

    def __init__(self) -> None:
        self.kind = "json"
        self.status = 200
        self.usage: dict | None = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        self.calls: list[dict] = []


BEHAVIOR = Behavior()


def handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content) if request.content else {}
    BEHAVIOR.calls.append({"body": body, "headers": dict(request.headers)})
    if BEHAVIOR.kind == "connect_error":
        raise httpx.ConnectError("upstream down")
    if BEHAVIOR.kind == "read_error":
        raise httpx.ReadError("connection dropped mid-request")
    if BEHAVIOR.status >= 400:
        return httpx.Response(BEHAVIOR.status, json={"error": {"message": "upstream failed"}})
    if BEHAVIOR.kind == "stream":
        chunks = [
            b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
        ]
        if BEHAVIOR.usage is not None:
            payload = json.dumps({"choices": [], "usage": BEHAVIOR.usage}).encode()
            chunks.append(b"data: " + payload + b"\n\n")
        chunks.append(b"data: [DONE]\n\n")

        async def stream():
            for chunk in chunks:
                yield chunk

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())
    payload = {"id": "gen-1", "choices": [{"message": {"role": "assistant", "content": "hello"}}]}
    if BEHAVIOR.usage is not None:
        payload["usage"] = BEHAVIOR.usage
    return httpx.Response(200, json=payload)


@pytest.fixture(autouse=True)
def mock_upstream(monkeypatch: pytest.MonkeyPatch) -> Behavior:
    from app.providers import openrouter as adapter_module

    BEHAVIOR.__init__()  # fresh state per test
    monkeypatch.setattr(
        adapter_module,
        "_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(adapter_module.settings, "openrouter_api_key", UPSTREAM_KEY)
    return BEHAVIOR


async def setup_account(*, balance_kopecks: int = 10_000, monthly_limit: int | None = None,
                        funding_source: str = "platform"):
    """Verified user + wallet + API key + one priced catalog model."""
    from app.db import session_factory
    from app.models import CatalogModel, CatalogPricing
    from app.services import identity as identity_service
    from app.services import keys as key_service
    from app.services import ledger as ledger_service

    async with session_factory()() as db:
        user, _ = await identity_service.register(
            db, email=f"gw-{uuid.uuid4().hex[:10]}@example.com", password="gateway-password-1"
        )
        user.email_verified_at = identity_service.utcnow()
        await db.flush()
        wallet = await ledger_service.get_or_create_wallet(db, user_id=user.id)
        if balance_kopecks:
            await ledger_service.topup(
                db,
                wallet=wallet,
                amount_kopecks=balance_kopecks,
                reference=f"gw-topup-{uuid.uuid4().hex}",
            )
        if funding_source == "customer":
            from app.services import provider_credentials as credential_service

            await credential_service.save_credential(
                db, user_id=user.id, api_key="customer-provider-secret"
            )
        key, raw = await key_service.create_key(
            db, user=user, name="gateway-test", monthly_limit_kopecks=monthly_limit,
            funding_source=funding_source,
        )
        model = (
            await db.execute(select(CatalogModel).where(CatalogModel.openrouter_id == "acme/chat-1"))
        ).scalar_one_or_none()
        if model is None:
            model = CatalogModel(
                openrouter_id="acme/chat-1",
                name="Acme Chat 1",
                provider="acme",
                context_length=4096,
                supports_tools=True,
                available=True,
            )
            db.add(model)
            await db.flush()
            db.add(
                CatalogPricing(
                    model_id=model.id,
                    version=1,
                    input_usd_per_mtok=Decimal("1.5"),
                    output_usd_per_mtok=Decimal("2"),
                    cached_usd_per_mtok=None,
                    fx_rate=Decimal("100"),
                    markup=Decimal("1.2"),
                    input_rub_per_mtok=Decimal("180.000000"),
                    output_rub_per_mtok=Decimal("240.000000"),
                    cached_rub_per_mtok=None,
                )
            )
        await db.commit()
        return {"raw_key": raw, "user_id": user.id, "key_id": key.id, "wallet_id": wallet.id}


def chat_body(*, stream: bool = False, content: str = "hi", **extra) -> dict:
    body = {
        "model": "acme/chat-1",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 100,
    }
    if stream:
        body["stream"] = True
    body.update(extra)
    return body


async def wallet_balance(wallet_id) -> int:
    from app.db import session_factory
    from app.models import Wallet

    async with session_factory()() as db:
        wallet = await db.get(Wallet, wallet_id)
        return int(wallet.balance_kopecks)


async def reserve_rows():
    from app.db import session_factory
    from app.models import Reserve

    async with session_factory()() as db:
        return list((await db.execute(select(Reserve))).scalars().all())


async def test_models_endpoint_lists_priced_models(client) -> None:
    await setup_account()
    response = await client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["id"] == "acme/chat-1"
    assert data[0]["x-rubai-pricing"]["input_rub_per_mtok"] == "180.000000"


async def test_missing_and_invalid_keys_are_rejected(client) -> None:
    await setup_account()
    missing = await client.post("/v1/chat/completions", json=chat_body())
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_api_key"

    invalid = await client.post(
        "/v1/chat/completions", json=chat_body(), headers={"authorization": "Bearer sk-rubai-nope"}
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_api_key"
    assert BEHAVIOR.calls == []


async def test_revoked_key_is_rejected(client) -> None:
    account = await setup_account()
    from app.db import session_factory
    from app.models import User
    from app.services import keys as key_service

    async with session_factory()() as db:
        user = await db.get(User, account["user_id"])
        assert user is not None
        key = await key_service.revoke_key(db, user=user, key_id=account["key_id"])
        await db.commit()
        assert key.revoked_at is not None

    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 401
    assert BEHAVIOR.calls == []


async def test_insufficient_balance_blocks_before_upstream(client) -> None:
    account = await setup_account(balance_kopecks=0)
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "insufficient_funds"
    assert BEHAVIOR.calls == []  # no upstream call without a committed reserve


async def test_non_stream_success_settles_exact_kopecks(client) -> None:
    account = await setup_account()
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "hello"

    # 100 input * 180/Mtok + 50 output * 240/Mtok = 0.018 + 0.012 = 3 kopecks
    assert await wallet_balance(account["wallet_id"]) == 10_000 - 3
    reserves = await reserve_rows()
    assert len(reserves) == 1
    assert reserves[0].status == "settled"
    assert reserves[0].settled_kopecks == 3

    # Outgoing request carries our server key, allowlisted params and usage include.
    call = BEHAVIOR.calls[0]
    assert call["headers"]["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert call["body"]["usage"] == {"include": True}
    assert call["body"]["max_tokens"] == 100


async def test_tools_and_stream_options_are_forwarded(client) -> None:
    account = await setup_account()
    tools = [{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}]
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(tools=tools, tool_choice="auto", temperature=0.2),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 200
    body = BEHAVIOR.calls[0]["body"]
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert body["temperature"] == 0.2


async def test_stream_success_settles_from_final_usage_chunk(client) -> None:
    account = await setup_account()
    BEHAVIOR.kind = "stream"
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(stream=True),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert BEHAVIOR.calls[0]["body"]["stream_options"] == {"include_usage": True}

    reserves = await reserve_rows()
    assert reserves[0].status == "settled"
    assert reserves[0].settled_kopecks == 3
    assert await wallet_balance(account["wallet_id"]) == 10_000 - 3


async def test_stream_without_usage_reconciles_and_keeps_funds(client) -> None:
    account = await setup_account()
    BEHAVIOR.kind = "stream"
    BEHAVIOR.usage = None
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(stream=True),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 200

    reserves = await reserve_rows()
    assert reserves[0].status == "reconciliation"
    assert await wallet_balance(account["wallet_id"]) == 10_000  # nothing charged blindly

    from app.models import ReconciliationItem
    from app.db import session_factory

    async with session_factory()() as db:
        items = (await db.execute(select(ReconciliationItem))).scalars().all()
    assert len(items) == 1 and items[0].kind == "usage_missing_stream"


async def test_upstream_connect_error_releases_reserve(client) -> None:
    account = await setup_account()
    BEHAVIOR.kind = "connect_error"
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unreachable"
    reserves = await reserve_rows()
    assert reserves[0].status == "released"
    assert await wallet_balance(account["wallet_id"]) == 10_000


async def test_upstream_http_error_releases_reserve(client) -> None:
    account = await setup_account()
    BEHAVIOR.status = 500
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 500
    assert "upstream failed" not in response.text
    assert response.json()["error"]["code"] == "upstream_error"
    reserves = await reserve_rows()
    assert reserves[0].status == "released"
    assert await wallet_balance(account["wallet_id"]) == 10_000


async def test_stream_upstream_error_hides_provider_body(client) -> None:
    account = await setup_account()
    BEHAVIOR.status = 401
    response = await client.post(
        "/v1/chat/completions", json=chat_body(stream=True),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 401
    assert "upstream failed" not in response.text
    assert response.json()["error"]["code"] == "upstream_error"


async def test_upstream_not_configured_releases_reserve(client, monkeypatch) -> None:
    account = await setup_account()
    from app.providers import openrouter as adapter_module

    monkeypatch.setattr(adapter_module.settings, "openrouter_api_key", "")
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_not_configured"
    reserves = await reserve_rows()
    assert reserves[0].status == "released"
    assert await wallet_balance(account["wallet_id"]) == 10_000
    assert BEHAVIOR.calls == []


async def test_ambiguous_upstream_error_reconciles(client) -> None:
    account = await setup_account()
    BEHAVIOR.kind = "read_error"
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_ambiguous"
    reserves = await reserve_rows()
    assert reserves[0].status == "reconciliation"
    assert await wallet_balance(account["wallet_id"]) == 10_000  # never charged blindly


async def test_missing_usage_json_reconciles(client) -> None:
    account = await setup_account()
    BEHAVIOR.usage = None
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 200
    reserves = await reserve_rows()
    assert reserves[0].status == "reconciliation"
    assert await wallet_balance(account["wallet_id"]) == 10_000


async def test_monthly_key_limit_is_enforced(client) -> None:
    account = await setup_account(monthly_limit=1)
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "key_limit_exceeded"
    assert BEHAVIOR.calls == []


async def test_unknown_model_is_404(client) -> None:
    account = await setup_account()
    body = chat_body()
    body["model"] = "nope/model"
    response = await client.post(
        "/v1/chat/completions", json=body, headers={"authorization": f"Bearer {account['raw_key']}"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert BEHAVIOR.calls == []


def test_usage_scanner_reads_only_real_usage() -> None:
    scanner = gw.UsageScanner()
    scanner.feed(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
    scanner.feed(b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n')
    scanner.feed(b"data: [DONE]\n\n")
    assert scanner.usage == {"prompt_tokens": 7, "completion_tokens": 3}


def test_estimate_reserve_never_below_one_kopeck() -> None:
    snapshot = gw.PricingSnapshot(
        input_rub_per_mtok=Decimal("180"), output_rub_per_mtok=Decimal("240")
    )
    assert gw.estimate_reserve_kopecks(snapshot, messages=[{"role": "user", "content": "hi"}], max_tokens=1) >= 1
    free = gw.PricingSnapshot(input_rub_per_mtok=Decimal("0"), output_rub_per_mtok=Decimal("0"))
    assert gw.estimate_reserve_kopecks(free, messages=[{"role": "user", "content": "hi"}], max_tokens=10) == 0


async def _request_rows():
    from app.db import session_factory
    from app.models import ApiRequest

    async with session_factory()() as db:
        return list((await db.execute(select(ApiRequest).order_by(ApiRequest.created_at))).scalars().all())


async def test_successful_call_records_request_history(client) -> None:
    account = await setup_account()
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 200
    rows = await _request_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "succeeded"
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 50
    assert row.cost_kopecks == 3
    assert row.model == "acme/chat-1"
    assert row.provider == "acme"
    assert row.price_version == 1
    assert row.provider_request_id == "gen-1"


@pytest.mark.parametrize("stream", [False, True])
async def test_customer_key_uses_user_provider_key_without_platform_charge(client, stream: bool) -> None:
    account = await setup_account(balance_kopecks=0, funding_source="customer")
    if stream:
        BEHAVIOR.kind = "stream"
    response = await client.post(
        "/v1/chat/completions", json=chat_body(stream=stream),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 200
    assert BEHAVIOR.calls[0]["headers"]["authorization"] == "Bearer customer-provider-secret"
    assert await wallet_balance(account["wallet_id"]) == 0
    assert await reserve_rows() == []
    rows = await _request_rows()
    assert len(rows) == 1
    assert rows[0].cost_kopecks == 0
    assert rows[0].price_version is None


async def test_boolean_max_tokens_is_rejected(client) -> None:
    account = await setup_account()
    response = await client.post(
        "/v1/chat/completions", json=chat_body(max_tokens=True),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_max_tokens"
    assert BEHAVIOR.calls == []


async def test_customer_key_never_falls_back_after_credential_deletion(client) -> None:
    account = await setup_account(funding_source="customer")
    from app.db import session_factory
    from app.models import ProviderCredential

    async with session_factory()() as db:
        credential = await db.get(ProviderCredential, account["user_id"])
        await db.delete(credential)
        await db.commit()

    response = await client.post(
        "/v1/chat/completions", json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_credential_missing"
    assert BEHAVIOR.calls == []


async def test_upstream_error_records_history_without_cost(client) -> None:
    account = await setup_account()
    BEHAVIOR.status = 500
    await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    rows = await _request_rows()
    assert len(rows) == 1
    assert rows[0].status == "upstream_error"
    assert rows[0].cost_kopecks == 0


async def test_stream_without_usage_records_reconciled(client) -> None:
    account = await setup_account()
    BEHAVIOR.kind = "stream"
    BEHAVIOR.usage = None
    await client.post(
        "/v1/chat/completions",
        json=chat_body(stream=True),
        headers={"authorization": f"Bearer {account['raw_key']}"},
    )
    rows = await _request_rows()
    assert len(rows) == 1
    assert rows[0].status == "reconciled"
    assert rows[0].cost_kopecks == 0


async def test_requests_endpoint_lists_only_the_own_user(client) -> None:
    from app.db import session_factory
    from app.models import User
    from app.services import identity as identity_service

    account_a = await setup_account()
    response = await client.post(
        "/v1/chat/completions",
        json=chat_body(),
        headers={"authorization": f"Bearer {account_a['raw_key']}"},
    )
    assert response.status_code == 200

    async with session_factory()() as db:
        user_a = await db.get(User, account_a["user_id"])
        assert user_a is not None
        _, raw_token = await identity_service.create_session(db, user=user_a, method="password")
        await db.commit()

    client.cookies.set("rb_platform_session", raw_token)
    listed = await client.get("/api/requests")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["model"] == "acme/chat-1"
    assert payload["items"][0]["cost_kopecks"] == 3

    # Another account sees an empty history.
    account_b = await setup_account()
    async with session_factory()() as db:
        user_b = await db.get(User, account_b["user_id"])
        assert user_b is not None
        _, raw_b = await identity_service.create_session(db, user=user_b, method="password")
        await db.commit()
    client.cookies.set("rb_platform_session", raw_b)
    other = await client.get("/api/requests")
    assert other.json()["total"] == 0
