"""Public model catalog with current RUB pricing (paginated and cached)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..redis_client import client as redis_client
from ..services import catalog as catalog_service

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

MAX_LIMIT = 200
CACHE_TTL_SECONDS = 30


def _item(model, pricing) -> dict[str, object]:
    return {
        "id": model.openrouter_id,
        "name": model.name,
        "provider": model.provider,
        "context_length": model.context_length,
        "supports_tools": model.supports_tools,
        "available": model.available,
        "pricing": None
        if pricing is None
        else {
            "version": pricing.version,
            "input_rub_per_mtok": str(pricing.input_rub_per_mtok),
            "output_rub_per_mtok": str(pricing.output_rub_per_mtok),
            "cached_rub_per_mtok": (
                str(pricing.cached_rub_per_mtok) if pricing.cached_rub_per_mtok is not None else None
            ),
            "fx_rate": str(pricing.fx_rate),
            "markup": str(pricing.markup),
            "valid_from": pricing.valid_from.isoformat(),
        },
    }


@router.get("")
async def catalog(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=80),
    sort: str = Query(default="name", pattern="^(name|price)$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    cache_key = f"catalog:v1:{limit}:{offset}:{sort}:{(q or '').strip().lower()}"
    try:
        cached = await redis_client().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001 - the cache must never break the catalog
        cached = None

    rows, total = await catalog_service.list_catalog(db, limit=limit, offset=offset, query=q, sort=sort)
    payload = {
        "items": [_item(model, pricing) for model, pricing in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
    try:
        await redis_client().set(cache_key, json.dumps(payload), ex=CACHE_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return payload
