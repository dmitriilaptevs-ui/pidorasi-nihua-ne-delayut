"""Admin surface: RBAC witness and catalog operations."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db, require_admin
from ..errors import ApiError
from ..models import Payment, ReconciliationItem, User
from ..schemas import CreditRequest, RefundRequest
from ..services import catalog as catalog_service
from ..services import identity as identity_service
from ..services import ledger as ledger_service
from ..services import payments as payment_service

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


@admin_router.post("/wallet/credit")
async def credit_wallet(
    payload: CreditRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Manual credit for sandbox testing; the ledger stays double-entry."""
    target = await identity_service.get_user_by_email(db, payload.email)
    if target is None:
        raise ApiError(404, "user_not_found", "Пользователь не найден.")
    wallet = await ledger_service.get_or_create_wallet(db, user_id=target.id)
    await ledger_service.topup(
        db,
        wallet=wallet,
        amount_kopecks=payload.amount_kopecks,
        reference=payload.reference,
        memo="admin credit (sandbox)",
    )
    await db.commit()
    await db.refresh(wallet)
    return {"user": target.email, "wallet": ledger_service.wallet_state(wallet)}



@admin_router.get("/payments")
async def admin_payments(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    rows = (
        await db.execute(select(Payment).order_by(Payment.created_at.desc()).limit(100))
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(row.id),
                "provider_payment_id": row.provider_payment_id,
                "amount_kopecks": row.amount_kopecks,
                "refunded_kopecks": row.refunded_kopecks,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@admin_router.post("/payments/{payment_id}/refund")
async def admin_refund(
    payment_id: uuid.UUID,
    payload: RefundRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise ApiError(404, "payment_not_found", "Платёж не найден.")
    payment, refund_id = await payment_service.refund_payment(
        db, payment=payment, amount_kopecks=payload.amount_kopecks
    )
    await db.commit()
    return {
        "refund_id": refund_id,
        "status": payment.status,
        "refunded_kopecks": payment.refunded_kopecks,
    }


@admin_router.get("/reconciliation")
async def reconciliation(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    rows = (
        await db.execute(
            select(ReconciliationItem)
            .where(ReconciliationItem.status == "open")
            .order_by(ReconciliationItem.created_at)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(row.id),
                "request_ref": row.request_ref,
                "kind": row.kind,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }
