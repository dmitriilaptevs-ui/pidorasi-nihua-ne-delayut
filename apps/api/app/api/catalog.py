"""Public model catalog with current RUB pricing."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..services import catalog as catalog_service

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("")
async def catalog(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    rows = await catalog_service.list_catalog(db)
    items: list[dict[str, object]] = []
    for model, pricing in rows:
        items.append(
            {
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
                        str(pricing.cached_rub_per_mtok)
                        if pricing.cached_rub_per_mtok is not None
                        else None
                    ),
                    "fx_rate": str(pricing.fx_rate),
                    "markup": str(pricing.markup),
                    "valid_from": pricing.valid_from.isoformat(),
                },
            }
        )
    return {"items": items}
