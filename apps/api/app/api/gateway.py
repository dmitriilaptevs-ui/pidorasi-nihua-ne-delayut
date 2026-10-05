"""OpenAI-compatible gateway: /v1/models and /v1/chat/completions.

Money safety order: authenticate -> resolve model -> estimate -> reserve commit
-> upstream call. Ambiguous outcomes never release funds blindly; they open a
reconciliation item instead.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_factory
from ..deps import get_db
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


def _error_response(exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=gw.oai_payload(exc))


async def _json_body(request: Request) -> object:
    try:
        return await request.json()
    except (ValueError, UnicodeDecodeError):
        raise gw.GatewayError(400, "Request body must be valid JSON.") from None


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
        model, pricing = await gw.resolve_model(db, model_id=validated["model"])
        snapshot = gw.snapshot_of(pricing)
        reserve_kopecks = gw.estimate_reserve_kopecks(
            snapshot, messages=validated["raw"]["messages"], max_tokens=validated["max_tokens"]
        )
        wallet = await ledger_service.get_or_create_wallet(db, user_id=user.id)
        await gw.check_monthly_limit(db, api_key=api_key, wallet=wallet, estimated_kopecks=reserve_kopecks)

        request_ref = uuid.uuid4().hex
        reserve: Reserve | None = None
        if reserve_kopecks > 0:
            reserve = await ledger_service.reserve(
                db,
                wallet=wallet,
                request_ref=request_ref,
                amount_kopecks=reserve_kopecks,
                api_key_id=api_key.id,
                ttl_seconds=settings.gateway_reserve_ttl_seconds,
            )
        await db.commit()
    except ApiError as exc:
        return _error_response(exc)

    upstream = upstream_payload(validated["raw"], stream=validated["stream"])
    upstream["max_tokens"] = validated["max_tokens"]
    adapter = OpenRouterAdapter()

    if not validated["stream"]:
        try:
            status, data = await adapter.complete(upstream)
        except UpstreamNotConfigured:
            if reserve is not None:
                await ledger_service.release(db, reserve=reserve, reason="upstream_not_configured")
                await db.commit()
            return JSONResponse(status_code=503, content=UPSTREAM_NOT_CONFIGURED)
        except UpstreamConnectError:
            if reserve is not None:
                await ledger_service.release(db, reserve=reserve, reason="upstream_unreachable")
                await db.commit()
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
                await db.commit()
            return JSONResponse(status_code=502, content=UPSTREAM_AMBIGUOUS)

        if status >= 400:
            if reserve is not None:
                await ledger_service.release(db, reserve=reserve, reason="upstream_error")
                await db.commit()
            return JSONResponse(status_code=status, content=data)

        usage = data.get("usage") if isinstance(data, dict) else None
        if reserve is not None:
            if isinstance(usage, dict) and usage.get("prompt_tokens") is not None:
                cost = gw.compute_cost_rub(snapshot, usage)
                await ledger_service.settle(db, reserve=reserve, cost_rub=cost)
            else:
                await ledger_service.flag_reconciliation(
                    db,
                    request_ref=request_ref,
                    kind="usage_missing",
                    payload={"model": model.openrouter_id},
                    reserve=reserve,
                )
            await db.commit()
        return JSONResponse(content=data)

    # Streaming path
    try:
        client, response = await adapter.open_stream(upstream)
    except UpstreamNotConfigured:
        if reserve is not None:
            await ledger_service.release(db, reserve=reserve, reason="upstream_not_configured")
            await db.commit()
        return JSONResponse(status_code=503, content=UPSTREAM_NOT_CONFIGURED)
    except UpstreamConnectError:
        if reserve is not None:
            await ledger_service.release(db, reserve=reserve, reason="upstream_unreachable")
            await db.commit()
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
            await db.commit()
        return JSONResponse(status_code=502, content=UPSTREAM_AMBIGUOUS)

    if response.status_code >= 400:
        raw = await response.aread()
        await response.aclose()
        await client.aclose()
        if reserve is not None:
            await ledger_service.release(db, reserve=reserve, reason="upstream_error")
            await db.commit()
        try:
            content = json.loads(raw)
        except ValueError:
            content = {"error": {"message": "Upstream error.", "type": "server_error", "code": "upstream_error"}}
        return JSONResponse(status_code=response.status_code, content=content)

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
            if reserve_id is not None:
                # A disconnect can still have produced provider cost: account in
                # a fresh session and reconcile instead of releasing blindly.
                async with session_factory()() as accounting:
                    fresh = await accounting.get(Reserve, reserve_id)
                    if fresh is not None and fresh.status == "held":
                        if scanner.usage is not None:
                            cost = gw.compute_cost_rub(snapshot, scanner.usage)
                            await ledger_service.settle(accounting, reserve=fresh, cost_rub=cost)
                        else:
                            await ledger_service.flag_reconciliation(
                                accounting,
                                request_ref=request_ref,
                                kind="usage_missing_stream",
                                payload={"model": model.openrouter_id, "stream": True},
                                reserve=fresh,
                            )
                        await accounting.commit()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )
