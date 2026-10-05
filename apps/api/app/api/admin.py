"""Admin surface: RBAC witness and catalog operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db, require_admin
from ..models import User
from ..services import catalog as catalog_service

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.get("/whoami")
async def admin_whoami(user: User = Depends(require_admin)) -> dict[str, str]:
    return {"admin": user.email or user.display_name or str(user.id)}


@admin_router.post("/catalog/sync")
async def sync_catalog_now(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    report = await catalog_service.sync_catalog(db)
    await db.commit()
    return {
        "seen": report.seen,
        "created": report.created,
        "repriced": report.repriced,
        "unchanged": report.unchanged,
        "unavailable": report.unavailable,
    }
