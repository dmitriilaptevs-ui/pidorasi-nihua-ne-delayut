"""API key management endpoints (browser session required)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_db, require_origin
from ..models import ApiKey, User
from ..schemas import KeyCreateRequest, KeyView
from ..services import keys as key_service

router = APIRouter(prefix="/api/keys", tags=["keys"])


@router.post("", status_code=201)
async def create_key(
    payload: KeyCreateRequest,
    _: None = Depends(require_origin),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    key, raw = await key_service.create_key(
        db, user=user, name=payload.name, monthly_limit_kopecks=payload.monthly_limit_kopecks
    )
    await db.commit()
    # The raw key is returned exactly once and never stored or logged.
    return {"key": raw, "item": KeyView.of(key)}


@router.get("")
async def list_keys(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    keys = await key_service.list_keys(db, user=user)
    return {"items": [KeyView.of(key) for key in keys]}


@router.post("/{key_id}/revoke")
async def revoke_key(
    key_id: uuid.UUID,
    _: None = Depends(require_origin),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    key: ApiKey = await key_service.revoke_key(db, user=user, key_id=key_id)
    await db.commit()
    return {"item": KeyView.of(key)}
