"""Double-entry wallet ledger with reserves, settlement and reconciliation.

Invariants enforced here and asserted by tests:

- every ledger transaction's postings sum to zero;
- a wallet's ``balance_kopecks`` equals the sum of its postings;
- a reserve exists before any paid upstream call, and settling is idempotent
  per ``request_ref``; settling never charges more than the reserve holds;
- fractional kopecks accumulate in ``sub_kopeck_remainder`` and are never
  rounded away for free.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import LedgerPosting, LedgerTransaction, ReconciliationItem, Reserve, Wallet
from ..pricing import as_decimal

RESERVE_TTL_SECONDS = 300
PLATFORM_TOPUPS = "platform:topups"
PLATFORM_USAGE_REVENUE = "platform:usage_revenue"
PLATFORM_ADJUSTMENTS = "platform:adjustments"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def wallet_account(wallet_id: uuid.UUID) -> str:
    return f"wallet:{wallet_id}"


def wallet_state(wallet: Wallet) -> dict[str, int]:
    return {
        "balance_kopecks": wallet.balance_kopecks,
        "reserved_kopecks": wallet.reserved_kopecks,
        "available_kopecks": wallet.balance_kopecks - wallet.reserved_kopecks,
    }


async def get_or_create_wallet(db: AsyncSession, *, user_id: uuid.UUID) -> Wallet:
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if wallet is None:
        try:
            async with db.begin_nested():
                wallet = Wallet(user_id=user_id)
                db.add(wallet)
                await db.flush()
        except IntegrityError:  # concurrent creation
            wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one()
    return wallet


async def _find_transaction(db: AsyncSession, reference: str) -> LedgerTransaction | None:
    return (
        await db.execute(select(LedgerTransaction).where(LedgerTransaction.reference == reference))
    ).scalar_one_or_none()


async def _post(
    db: AsyncSession,
    *,
    kind: str,
    reference: str,
    entries: list[tuple[str, int]],
    memo: str | None = None,
) -> LedgerTransaction:
    total = sum(amount for _, amount in entries)
    if total != 0:
        raise RuntimeError(f"unbalanced ledger transaction: {reference} sums to {total}")
    try:
        async with db.begin_nested():
            transaction = LedgerTransaction(kind=kind, reference=reference, memo=memo)
            db.add(transaction)
            await db.flush()
            for account_code, amount in entries:
                db.add(
                    LedgerPosting(
                        transaction_id=transaction.id, account_code=account_code, amount_kopecks=amount
                    )
                )
            await db.flush()
    except IntegrityError:  # same reference already posted
        existing = await _find_transaction(db, reference)
        if existing is None:  # pragma: no cover - only if the constraint fired elsewhere
            raise
        return existing
    return transaction


async def topup(
    db: AsyncSession, *, wallet: Wallet, amount_kopecks: int, reference: str, memo: str | None = None
) -> LedgerTransaction:
    if amount_kopecks <= 0:
        raise ApiError(400, "invalid_amount", "Сумма пополнения должна быть положительной.")
    existing = await _find_transaction(db, reference)
    if existing is not None:
        return existing
    transaction = await _post(
        db,
        kind="topup",
        reference=reference,
        memo=memo,
        entries=[(PLATFORM_TOPUPS, -amount_kopecks), (wallet_account(wallet.id), amount_kopecks)],
    )
    await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet.id)
        .values(balance_kopecks=Wallet.balance_kopecks + amount_kopecks)
    )
    await db.refresh(wallet)
    return transaction


async def adjust(
    db: AsyncSession, *, wallet: Wallet, amount_kopecks: int, reference: str, memo: str | None = None
) -> LedgerTransaction:
    """Signed manual correction (audited, separate platform account)."""
    if amount_kopecks == 0:
        raise ApiError(400, "invalid_amount", "Корректировка не может быть нулевой.")
    existing = await _find_transaction(db, reference)
    if existing is not None:
        return existing
    transaction = await _post(
        db,
        kind="adjustment",
        reference=reference,
        memo=memo,
        entries=[(PLATFORM_ADJUSTMENTS, -amount_kopecks), (wallet_account(wallet.id), amount_kopecks)],
    )
    await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet.id)
        .values(balance_kopecks=Wallet.balance_kopecks + amount_kopecks)
    )
    await db.refresh(wallet)
    return transaction


async def reserve(
    db: AsyncSession,
    *,
    wallet: Wallet,
    request_ref: str,
    amount_kopecks: int,
    api_key_id: uuid.UUID | None = None,
    ttl_seconds: int = RESERVE_TTL_SECONDS,
) -> Reserve:
    if amount_kopecks <= 0:
        raise ApiError(400, "invalid_amount", "Резерв должен быть положительным.")
    existing = (
        await db.execute(select(Reserve).where(Reserve.request_ref == request_ref))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    claimed = await db.execute(
        update(Wallet)
        .where(
            Wallet.id == wallet.id,
            Wallet.balance_kopecks - Wallet.reserved_kopecks >= amount_kopecks,
        )
        .values(reserved_kopecks=Wallet.reserved_kopecks + amount_kopecks)
        .returning(Wallet.id)
    )
    if claimed.scalar_one_or_none() is None:
        raise ApiError(402, "insufficient_funds", "Недостаточно средств на балансе.")

    try:
        async with db.begin_nested():
            created = Reserve(
                wallet_id=wallet.id,
                api_key_id=api_key_id,
                request_ref=request_ref,
                amount_kopecks=amount_kopecks,
                expires_at=utcnow() + timedelta(seconds=ttl_seconds),
            )
            db.add(created)
            await db.flush()
    except IntegrityError:  # concurrent reserve with the same reference
        await db.refresh(wallet)
        return (await db.execute(select(Reserve).where(Reserve.request_ref == request_ref))).scalar_one()

    await db.refresh(wallet)
    return created


async def settle(
    db: AsyncSession, *, reserve: Reserve, cost_rub: Decimal, memo: str | None = None
) -> tuple[LedgerTransaction, int]:
    """Charge the actual cost, idempotent per reserve.

    The charge is capped by the reserved amount: anything above it becomes a
    reconciliation item instead of an unbounded surprise charge.
    """
    reference = f"usage:{reserve.request_ref}"
    existing = await _find_transaction(db, reference)
    if existing is not None:
        return existing, int(reserve.settled_kopecks or 0)
    if reserve.status != "held":
        raise ApiError(409, "reserve_not_held", "Резерв уже закрыт или находится на сверке.")

    wallet = await db.get(Wallet, reserve.wallet_id)
    assert wallet is not None
    cost = as_decimal(cost_rub) + wallet.sub_kopeck_remainder
    if cost < 0:
        cost = Decimal("0")
    charge_kopecks = int((cost * 100).to_integral_value(rounding=ROUND_FLOOR))

    overage = max(0, charge_kopecks - reserve.amount_kopecks)
    if overage:
        charge_kopecks = reserve.amount_kopecks
        db.add(
            ReconciliationItem(
                reserve_id=reserve.id,
                request_ref=reserve.request_ref,
                kind="reserve_exceeded",
                payload={
                    "charged_kopecks": charge_kopecks,
                    "overage_kopecks": overage,
                    "cost_rub": str(as_decimal(cost_rub)),
                },
            )
        )

    transaction = await _post(
        db,
        kind="usage",
        reference=reference,
        memo=memo,
        entries=[
            (wallet_account(wallet.id), -charge_kopecks),
            (PLATFORM_USAGE_REVENUE, charge_kopecks),
        ],
    )
    remainder = cost - Decimal(charge_kopecks) / 100
    await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet.id)
        .values(
            balance_kopecks=Wallet.balance_kopecks - charge_kopecks,
            reserved_kopecks=Wallet.reserved_kopecks - reserve.amount_kopecks,
            sub_kopeck_remainder=remainder,
        )
    )
    reserve.status = "settled"
    reserve.settled_kopecks = charge_kopecks
    reserve.released_at = utcnow()
    await db.flush()
    await db.refresh(wallet)
    return transaction, charge_kopecks


async def release(db: AsyncSession, *, reserve: Reserve, reason: str | None = None) -> Reserve:
    if reserve.status in ("settled", "released"):
        return reserve
    await db.execute(
        update(Wallet)
        .where(Wallet.id == reserve.wallet_id)
        .values(reserved_kopecks=Wallet.reserved_kopecks - reserve.amount_kopecks)
    )
    reserve.status = "released"
    reserve.released_at = utcnow()
    await db.flush()
    if reason:
        db.add(
            ReconciliationItem(
                reserve_id=reserve.id,
                request_ref=reserve.request_ref,
                kind="released",
                payload={"reason": reason},
                status="resolved",
                resolved_at=utcnow(),
            )
        )
        await db.flush()
    return reserve


async def flag_reconciliation(
    db: AsyncSession,
    *,
    request_ref: str,
    kind: str,
    payload: dict,
    reserve: Reserve | None = None,
    release_reserve: bool = False,
) -> ReconciliationItem:
    """Record an ambiguous outcome. Never releases a reserve unless told to."""
    if reserve is not None:
        if release_reserve:
            await release(db, reserve=reserve, reason=kind)
        else:
            reserve.status = "reconciliation"
            await db.flush()
    item = ReconciliationItem(
        reserve_id=reserve.id if reserve is not None else None,
        request_ref=request_ref,
        kind=kind,
        payload=payload,
    )
    db.add(item)
    await db.flush()
    return item


async def recompute_balance(db: AsyncSession, *, wallet: Wallet) -> int:
    """Sum of the wallet's postings — the auditable source of the projection."""
    rows = (
        await db.execute(
            select(LedgerPosting.amount_kopecks).where(
                LedgerPosting.account_code == wallet_account(wallet.id)
            )
        )
    ).scalars().all()
    return int(sum(rows))


async def open_reconciliation(db: AsyncSession) -> list[ReconciliationItem]:
    return list(
        (
            await db.execute(
                select(ReconciliationItem)
                .where(ReconciliationItem.status == "open")
                .order_by(ReconciliationItem.created_at)
            )
        )
        .scalars()
        .all()
    )
