"""OpenRouter catalog sync.

Every sync creates a new pricing version when the provider price, FX rate or
markup changed, retires the previous version and never edits an existing row —
historical charges stay explainable. Models missing from the feed are marked
unavailable, not deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogModel, CatalogPricing
from ..pricing import as_decimal, rub_per_mtok, usd_per_mtok
from ..settings import settings

FETCH_TIMEOUT_SECONDS = 20.0
MAX_FEED_BYTES = 4 * 1024 * 1024


@dataclass
class SyncReport:
    seen: int = 0
    created: int = 0
    repriced: int = 0
    unchanged: int = 0
    unavailable: int = 0
    errors: list[str] = field(default_factory=list)


async def fetch_openrouter_models() -> list[dict]:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.get(f"{settings.openrouter_api_base.rstrip('/')}/models")
    response.raise_for_status()
    if len(response.content) > MAX_FEED_BYTES:
        raise ValueError("catalog feed too large")
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError("unexpected catalog payload")
    return [entry for entry in data if isinstance(entry, dict)]


def _supports_tools(entry: dict) -> bool:
    parameters = entry.get("supported_parameters")
    if not isinstance(parameters, list):
        return False
    lowered = {str(item).lower() for item in parameters}
    return bool({"tools", "tool_choice"} & lowered)


def _is_text_model(entry: dict) -> bool:
    architecture = entry.get("architecture")
    if not isinstance(architecture, dict):
        return True
    modalities = architecture.get("input_modalities")
    if not isinstance(modalities, list):
        return True
    return "text" in {str(item).lower() for item in modalities}


def _mapped(entry: dict) -> dict | None:
    openrouter_id = str(entry.get("id") or "").strip()
    if not openrouter_id or len(openrouter_id) > 200:
        return None
    provider = openrouter_id.split("/", 1)[0][:100]
    pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
    try:
        input_usd = usd_per_mtok(pricing.get("prompt", "0"))
        output_usd = usd_per_mtok(pricing.get("completion", "0"))
        cached_usd = (
            usd_per_mtok(pricing["input_cache_read"]) if pricing.get("input_cache_read") else None
        )
    except ValueError:
        return None
    return {
        "openrouter_id": openrouter_id,
        "name": str(entry.get("name") or openrouter_id)[:200],
        "provider": provider,
        "context_length": int(entry.get("context_length") or 0),
        "supports_tools": _supports_tools(entry),
        "input_usd_per_mtok": input_usd,
        "output_usd_per_mtok": output_usd,
        "cached_usd_per_mtok": cached_usd,
    }


async def sync_catalog(
    db: AsyncSession,
    *,
    fetch: Callable[[], Awaitable[list[dict]]] = fetch_openrouter_models,
) -> SyncReport:
    entries = await fetch()
    report = SyncReport()
    fx_rate = as_decimal(settings.fx_rate_rub_per_usd)
    markup = as_decimal(settings.price_markup)
    now = datetime.now(timezone.utc)
    seen_ids: set[str] = set()

    for entry in entries:
        mapped = _mapped(entry)
        if mapped is None or not _is_text_model(entry):
            continue
        seen_ids.add(mapped["openrouter_id"])
        report.seen += 1

        model = (
            await db.execute(
                select(CatalogModel).where(CatalogModel.openrouter_id == mapped["openrouter_id"])
            )
        ).scalar_one_or_none()
        if model is None:
            model = CatalogModel(
                openrouter_id=mapped["openrouter_id"],
                name=mapped["name"],
                provider=mapped["provider"],
                context_length=mapped["context_length"],
                supports_tools=mapped["supports_tools"],
                available=True,
            )
            db.add(model)
            await db.flush()
            report.created += 1
        else:
            model.name = mapped["name"]
            model.provider = mapped["provider"]
            model.context_length = mapped["context_length"]
            model.supports_tools = mapped["supports_tools"]
            model.available = True

        active = (
            await db.execute(
                select(CatalogPricing).where(
                    CatalogPricing.model_id == model.id, CatalogPricing.retired_at.is_(None)
                )
            )
        ).scalar_one_or_none()

        same = (
            active is not None
            and active.input_usd_per_mtok == mapped["input_usd_per_mtok"]
            and active.output_usd_per_mtok == mapped["output_usd_per_mtok"]
            and active.cached_usd_per_mtok == mapped["cached_usd_per_mtok"]
            and active.fx_rate == fx_rate
            and active.markup == markup
        )
        if same:
            report.unchanged += 1
            continue

        if active is not None:
            active.retired_at = now
            highest = (
                await db.execute(
                    select(func.max(CatalogPricing.version)).where(CatalogPricing.model_id == model.id)
                )
            ).scalar_one()
            version = int(highest or 0) + 1
            report.repriced += 1
        else:
            version = 1

        db.add(
            CatalogPricing(
                model_id=model.id,
                version=version,
                input_usd_per_mtok=mapped["input_usd_per_mtok"],
                output_usd_per_mtok=mapped["output_usd_per_mtok"],
                cached_usd_per_mtok=mapped["cached_usd_per_mtok"],
                fx_rate=fx_rate,
                markup=markup,
                input_rub_per_mtok=rub_per_mtok(mapped["input_usd_per_mtok"], fx_rate, markup),
                output_rub_per_mtok=rub_per_mtok(mapped["output_usd_per_mtok"], fx_rate, markup),
                cached_rub_per_mtok=(
                    rub_per_mtok(mapped["cached_usd_per_mtok"], fx_rate, markup)
                    if mapped["cached_usd_per_mtok"] is not None
                    else None
                ),
            )
        )

    # Models that disappeared from the feed stay in history but become unavailable.
    missing = (
        await db.execute(select(CatalogModel).where(CatalogModel.available.is_(True)))
    ).scalars().all()
    for model in missing:
        if model.openrouter_id not in seen_ids:
            model.available = False
            report.unavailable += 1

    await db.flush()
    return report


async def list_catalog(db: AsyncSession) -> list[tuple[CatalogModel, CatalogPricing | None]]:
    models = (
        await db.execute(select(CatalogModel).order_by(CatalogModel.name))
    ).scalars().all()
    active = (
        await db.execute(select(CatalogPricing).where(CatalogPricing.retired_at.is_(None)))
    ).scalars().all()
    by_model = {row.model_id: row for row in active}
    return [(model, by_model.get(model.id)) for model in models]
