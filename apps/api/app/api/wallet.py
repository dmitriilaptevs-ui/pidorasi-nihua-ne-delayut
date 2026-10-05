"""Wallet endpoints: balance projection and the user's ledger history."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_db
from ..models import LedgerPosting, LedgerTransaction, User
from ..services import ledger as ledger_service

router = APIRouter(prefix="/api/wallet", tags=["wallet"])

HISTORY_LIMIT = 50


@router.get("")
async def wallet(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    row = await ledger_service.get_or_create_wallet(db, user_id=user.id)
    await db.commit()
    return {"wallet": ledger_service.wallet_state(row)}


@router.get("/ledger")
async def history(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    row = await ledger_service.get_or_create_wallet(db, user_id=user.id)
    account = ledger_service.wallet_account(row.id)
    result = await db.execute(
        select(LedgerPosting, LedgerTransaction)
        .join(LedgerTransaction, LedgerTransaction.id == LedgerPosting.transaction_id)
        .where(LedgerPosting.account_code == account)
        .order_by(LedgerPosting.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    items = [
        {
            "transaction_id": str(posting.transaction_id),
            "kind": transaction.kind,
            "amount_kopecks": posting.amount_kopecks,
            "memo": transaction.memo,
            "created_at": posting.created_at.isoformat(),
        }
        for posting, transaction in result.all()
    ]
    await db.commit()
    return {"items": items}
