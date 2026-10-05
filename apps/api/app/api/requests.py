"""Per-request history for the signed-in user (ТЗ 4.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_db
from ..models import ApiRequest, User

router = APIRouter(prefix="/api/requests", tags=["requests"])

MAX_LIMIT = 200


def _view(row: ApiRequest) -> dict[str, object]:
    return {
        "id": str(row.id),
        "request_ref": row.request_ref,
        "model": row.model,
        "provider": row.provider,
        "status": row.status,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "cached_tokens": row.cached_tokens,
        "cost_kopecks": row.cost_kopecks,
        "price_version": row.price_version,
        "created_at": row.created_at.isoformat(),
    }


@router.get("")
async def list_requests(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    total = (
        await db.execute(select(func.count(ApiRequest.id)).where(ApiRequest.user_id == user.id))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ApiRequest)
            .where(ApiRequest.user_id == user.id)
            .order_by(ApiRequest.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {"items": [_view(row) for row in rows], "total": int(total)}
