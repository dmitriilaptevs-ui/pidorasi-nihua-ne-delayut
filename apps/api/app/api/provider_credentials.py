"""Per-user OpenRouter credential management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_db, read_json_body, require_origin
from ..errors import ApiError
from ..models import ProviderCredential, User
from ..services import provider_credentials as credential_service

router = APIRouter(prefix="/api/provider-credentials", tags=["provider credentials"])


def _view(credential: ProviderCredential | None) -> dict[str, object]:
    return {
        "configured": credential is not None,
        "suffix": credential.suffix if credential else None,
        "updated_at": credential.updated_at.isoformat() if credential else None,
    }


@router.get("")
async def get_provider_credential(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    return _view(await credential_service.get_credential(db, user_id=user.id))


@router.put("")
async def put_provider_credential(
    request: Request,
    _: None = Depends(require_origin),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if user.email_verified_at is None:
        raise ApiError(403, "email_not_verified", "Подтвердите адрес электронной почты.")
    payload = await read_json_body(request, max_bytes=8 * 1024)
    api_key = payload.get("api_key") if isinstance(payload, dict) else None
    if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 1024:
        raise ApiError(400, "invalid_credential", "Передайте корректный ключ OpenRouter.")
    credential = await credential_service.save_credential(
        db, user_id=user.id, api_key=api_key.strip()
    )
    await db.commit()
    return _view(credential)


@router.delete("")
async def delete_provider_credential(
    _: None = Depends(require_origin),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if user.email_verified_at is None:
        raise ApiError(403, "email_not_verified", "Подтвердите адрес электронной почты.")
    credential = await db.get(ProviderCredential, user.id)
    if credential is not None:
        await db.delete(credential)
        await db.commit()
    return _view(None)
