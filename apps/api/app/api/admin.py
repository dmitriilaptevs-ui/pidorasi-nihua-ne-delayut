"""Minimal admin surface: RBAC witness endpoint used by tests and ops checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import require_admin
from ..models import User

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.get("/whoami")
async def admin_whoami(user: User = Depends(require_admin)) -> dict[str, str]:
    return {"admin": user.email}
