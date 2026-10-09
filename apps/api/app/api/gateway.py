"""OpenAI-compatible gateway: /v1/models and /v1/chat/completions.

Money safety order: authenticate -> resolve model -> estimate -> reserve commit
-> upstream call. Ambiguous outcomes never release funds blindly; they open a
reconciliation item instead.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_factory
from ..deps import get_db, read_json_body
from ..errors import ApiError
from ..models import CatalogModel, CatalogPricing, Reserve
from ..providers.openrouter import (
    OpenRouterAdapter,
    UpstreamConnectError,
    UpstreamError,
    UpstreamNotConfigured,
    upstream_payload,
)
from ..services import gateway as gw
from ..services import ledger as ledger_service
from ..services import provider_credentials as credential_service
from ..settings import settings

router = APIRouter(prefix="/v1", tags=["gateway"])

UPSTREAM_UNREACHABLE = {
    "error": {
        "message": "Upstream provider unreachable; no charge was made.",
        "type": "server_error",
        "code": "upstream_unreachable",
    }
}

UPSTREAM_NOT_CONFIGURED = {
    "error": {
        "message": "Server-side provider key is not configured.",
        "type": "server_error",
        "code": "upstream_not_configured",
    }
}

UPSTREAM_AMBIGUOUS = {
    "error": {
        "message": "Upstream failure after dispatch; the request is under reconciliation.",
        "type": "server_error",
        "code": "upstream_ambiguous",
    }
}


async def _record_failure(db, *, user, api_key, request_ref: str, model, status: str) -> None:
    await gw.record_request(
        db,
        user_id=user.id,
        api_key_id=api_key.id,
        request_ref=request_ref,
        model_id=model.openrouter_id,
        provider=model.provider,
        status=status,
    )
    await db.commit()


def _error_response(exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=gw.oai_payload(exc))


async def _json_body(request: Request) -> object:
    return await read_json_body(request, max_bytes=settings.gateway_max_input_chars * 4)


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)) -> dict:
    rows = (
        await db.execute(
            select(CatalogModel, CatalogPricing)
            .join(
                CatalogPricing,
                (CatalogPricing.model_id == CatalogModel.id) & (CatalogPricing.retired_at.is_(None)),
            )
            .where(CatalogModel.available.is_(True))
            .order_by(CatalogModel.openrouter_id)
        )
    ).all()
    data = []
    for model, pricing in rows:
        data.append(
            {
                "id": model.openrouter_id,
                "object": "model",
                "created": 0,
                "owned_by": model.provider,
                "context_length": model.context_length,
                "x-rubai-pricing": {
                    "input_rub_per_mtok": str(pricing.input_rub_per_mtok),
                    "output_rub_per_mtok": str(pricing.output_rub_per_mtok),
                    "cached_rub_per_mtok": (
                        str(pricing.cached_rub_per_mtok) if pricing.cached_rub_per_mtok is not None else None
                    ),
                },
            }
        )
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        user, api_key = await gw.authenticate(db, authorization=request.headers.get("authorization"))
        validated = gw.validate_chat_request(await _json_body(request))
        customer_funded = api_key.funding_source == "customer"
        model, pricing = await gw.resolve_model(
            db, model_id=validated["model"], require_pricing=not customer_funded
        )
        provider_key = None
        if customer_funded:
            credential = await credential_service.get_credential(db, user_id=user.id)
            if credential is None:
                raise gw.GatewayError(503, "Customer provider key is not configured.", "provider_credential_missing", "server_error")
            provider_key = credential_service.decrypt_key(credential)
            snapshot = None
            reserve_kopecks = 0
        else:
            snapshot = gw.snapshot_of(pricing)
            reserve_kopecks = gw.estimate_reserve_kopecks(
                snapshot, messages=validated["raw"]["messages"], max_tokens=validated["max_tokens"]
            )
            wallet = await ledger_service.get_or_create_wallet(db, user_id=user.id)
            await gw.check_monthly_limit(db, api_key=api_key, wallet=wallet, estimated_kopecks=reserve_kopecks)

        request_ref = uuid.uuid4().hex
        reserve: Reserve | None = None
        if reserve_kopecks > 0:
            try:
                reserve = await ledger_service.reserve(
                    db,
                    wallet=wallet,
                    request_ref=request_ref,
                    amount_kopecks=reserve_kopecks,
                    api_key_id=api_key.id,
                    ttl_seconds=settings.gateway_reserve_ttl_seconds,
                )
            except ApiError as exc:
                if exc.code == "insufficient_funds":
                    raise gw.GatewayError(
                        402,
                        "Insufficient balance for this request.",
                        "insufficient_funds",
                        "insufficient_quota",
                    ) from None
                raise
        await db.commit()
    except ApiError as exc:
        return _error_response(exc)

    upstream = upstream_payload(validated["raw"], stream=validated["stream"])
    upstream["max_tokens"] = validated["max_tokens"]
    adapter = OpenRouterAdapter(api_key=provider_key) if customer_funded else OpenRouterAdapter()

    if not validated["stream"]:
        try:
            status, data = await adapter.complete(upstream)
        except UpstreamNotConfigured:
            if reserve is not None:
                await ledger_service.release(db, reserve=reserve, reason="upstream_not_configured")
            await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="upstream_not_configured")
            return JSONResponse(status_code=503, content=UPSTREAM_NOT_CONFIGURED)
        except UpstreamConnectError:
            if reserve is not None:
                await ledger_service.release(db, reserve=reserve, reason="upstream_unreachable")
            await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="upstream_unreachable")
            return JSONResponse(status_code=502, content=UPSTREAM_UNREACHABLE)
        except UpstreamError:
            if reserve is not None:
                await ledger_service.flag_reconciliation(
                    db,
                    request_ref=request_ref,
                    kind="upstream_ambiguous",
                    payload={"model": model.openrouter_id},
                    reserve=reserve,
                )
            await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="ambiguous")
            return JSONResponse(status_code=502, content=UPSTREAM_AMBIGUOUS)

        if status >= 400:
            if reserve is not None:
                await ledger_service.release(db, reserve=reserve, reason="upstream_error")
            await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="upstream_error")
            return JSONResponse(status_code=status, content={"error": {"message": "Upstream provider rejected the request.", "type": "server_error", "code": "upstream_error"}})

        usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else None
        charged = 0
        status = "succeeded" if usage else "reconciled"
        if reserve is not None:
            if usage is not None and usage.get("prompt_tokens") is not None:
                cost = gw.compute_cost_rub(snapshot, usage)
                _, charged = await ledger_service.settle(db, reserve=reserve, cost_rub=cost)
            else:
                await ledger_service.flag_reconciliation(
                    db,
                    request_ref=request_ref,
                    kind="usage_missing",
                    payload={"model": model.openrouter_id},
                    reserve=reserve,
                )
        await gw.record_request(
            db,
            user_id=user.id,
            api_key_id=api_key.id,
            request_ref=request_ref,
            model_id=model.openrouter_id,
            provider=model.provider,
            status=status,
            usage=usage,
            cost_kopecks=charged,
            pricing=pricing if not customer_funded else None,
            provider_request_id=str(data.get("id") or "") if isinstance(data, dict) else None,
        )
        await db.commit()
        return JSONResponse(content=data)

    # Streaming path
    try:
        client, response = await adapter.open_stream(upstream)
    except UpstreamNotConfigured:
        if reserve is not None:
            await ledger_service.release(db, reserve=reserve, reason="upstream_not_configured")
        await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="upstream_not_configured")
        return JSONResponse(status_code=503, content=UPSTREAM_NOT_CONFIGURED)
    except UpstreamConnectError:
        if reserve is not None:
            await ledger_service.release(db, reserve=reserve, reason="upstream_unreachable")
        await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="upstream_unreachable")
        return JSONResponse(status_code=502, content=UPSTREAM_UNREACHABLE)
    except UpstreamError:
        if reserve is not None:
            await ledger_service.flag_reconciliation(
                db,
                request_ref=request_ref,
                kind="upstream_ambiguous",
                payload={"model": model.openrouter_id, "stream": True},
                reserve=reserve,
            )
        await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="ambiguous")
        return JSONResponse(status_code=502, content=UPSTREAM_AMBIGUOUS)

    if response.status_code >= 400:
        await response.aread()
        await response.aclose()
        await client.aclose()
        if reserve is not None:
            await ledger_service.release(db, reserve=reserve, reason="upstream_error")
        await _record_failure(db, user=user, api_key=api_key, request_ref=request_ref, model=model, status="upstream_error")
        return JSONResponse(status_code=response.status_code, content={"error": {"message": "Upstream provider rejected the request.", "type": "server_error", "code": "upstream_error"}})

    reserve_id = reserve.id if reserve is not None else None

    async def event_stream():
        scanner = gw.UsageScanner()
        try:
            async for chunk in response.aiter_raw():
                scanner.feed(chunk)
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()
            if reserve_id is not None or customer_funded:
                # A disconnect can still have produced provider cost: account in
                # a fresh session and reconcile instead of releasing blindly.
                async with session_factory()() as accounting:
                    fresh = await accounting.get(Reserve, reserve_id) if reserve_id is not None else None
                    charged = 0
                    status = "succeeded" if scanner.usage is not None else "reconciled"
                    if fresh is not None and fresh.status == "held":
                        if scanner.usage is not None and snapshot is not None:
                            cost = gw.compute_cost_rub(snapshot, scanner.usage)
                            _, charged = await ledger_service.settle(accounting, reserve=fresh, cost_rub=cost)
                        elif scanner.usage is None:
                            await ledger_service.flag_reconciliation(
                                accounting,
                                request_ref=request_ref,
                                kind="usage_missing_stream",
                                payload={"model": model.openrouter_id, "stream": True},
                                reserve=fresh,
                            )
                    await gw.record_request(
                        accounting,
                        user_id=user.id,
                        api_key_id=api_key.id,
                        request_ref=request_ref,
                        model_id=model.openrouter_id,
                        provider=model.provider,
                        status=status,
                        usage=scanner.usage,
                        cost_kopecks=charged,
                        pricing=pricing if not customer_funded else None,
                    )
                    await accounting.commit()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )
