"""Ledger invariants: double entry, reserves, settlement, concurrency."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.errors import ApiError

WALLET_USER_EMAIL = "ledger@example.com"


async def _ensure_user(db) -> uuid.UUID:
    from app.models import User

    user = (
        await db.execute(select(User).where(User.email == WALLET_USER_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=WALLET_USER_EMAIL, signup_method="password")
        db.add(user)
        await db.flush()
    return user.id


async def _wallet(db):
    from app.services import ledger as ledger_service

    user_id = await _ensure_user(db)
    return await ledger_service.get_or_create_wallet(db, user_id=user_id)


async def _new_session():
    from app.db import session_factory

    return session_factory()()


async def test_topup_is_double_entry_and_idempotent() -> None:
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=150000, reference="topup-1")
        await db.commit()
        assert wallet.balance_kopecks == 150000

        # Same reference again: no second transaction, no double credit.
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=150000, reference="topup-1")
        await db.commit()
        await db.refresh(wallet)
        assert wallet.balance_kopecks == 150000

        from app.models import LedgerPosting, LedgerTransaction

        transactions = (await db.execute(select(func.count(LedgerTransaction.id)))).scalar_one()
        postings = (await db.execute(select(LedgerPosting))).scalars().all()
    
    assert transactions == 1
    assert sum(row.amount_kopecks for row in postings) == 0
    assert wallet.balance_kopecks == await _recompute(wallet.id)


async def _recompute(wallet_id) -> int:
    from app.models import LedgerPosting
    from app.services.ledger import wallet_account

    async with await _new_session() as db:
        rows = (
            await db.execute(
                select(LedgerPosting.amount_kopecks).where(
                    LedgerPosting.account_code == wallet_account(wallet_id)
                )
            )
        ).scalars().all()
    return int(sum(rows))


async def test_reserve_requires_funds_and_holds_them() -> None:
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=1000, reference="topup-2")
        await db.commit()

        with_error = None
        try:
            await ledger_service.reserve(
                db, wallet=wallet, request_ref="too-big", amount_kopecks=5000
            )
        except ApiError as exc:
            with_error = exc
        assert with_error is not None and with_error.code == "insufficient_funds"

        held = await ledger_service.reserve(db, wallet=wallet, request_ref="r1", amount_kopecks=600)
        await db.commit()
        await db.refresh(wallet)
        state = ledger_service.wallet_state(wallet)
        assert held.status == "held"
        assert state == {"balance_kopecks": 1000, "reserved_kopecks": 600, "available_kopecks": 400}


async def test_settle_charges_actual_cost_and_accumulates_sub_kopeck() -> None:
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=1000, reference="topup-3")
        await db.commit()

        total_charged = 0
        # Six reserves of 0.0018 RUB each: floor charges 0 kopecks five times
        # (remainder accumulates) and 1 kopeck on the sixth.
        for index in range(6):
            held = await ledger_service.reserve(
                db, wallet=wallet, request_ref=f"tiny-{index}", amount_kopecks=100
            )
            _, charged = await ledger_service.settle(db, reserve=held, cost_rub=Decimal("0.0018"))
            await db.commit()
            total_charged += charged
        await db.refresh(wallet)

        # 6 * 0.0018 RUB = 1.08 kopecks -> 1 kopeck charged, 0.0008 remainder.
        assert total_charged == 1
        assert wallet.balance_kopecks == 999
        assert wallet.reserved_kopecks == 0
        assert wallet.sub_kopeck_remainder == Decimal("0.000800")
        assert wallet.balance_kopecks == await _recompute(wallet.id)


async def test_settle_is_idempotent_per_reserve() -> None:
    from app.models import LedgerTransaction
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=1000, reference="topup-4")
        await db.commit()
        held = await ledger_service.reserve(db, wallet=wallet, request_ref="once", amount_kopecks=100)
        await ledger_service.settle(db, reserve=held, cost_rub=Decimal("0.50"))
        await db.commit()
        await ledger_service.settle(db, reserve=held, cost_rub=Decimal("0.50"))
        await db.commit()
        await db.refresh(wallet)
        usage_count = (
            await db.execute(
                select(func.count(LedgerTransaction.id)).where(LedgerTransaction.kind == "usage")
            )
        ).scalar_one()
    assert usage_count == 1
    assert wallet.balance_kopecks == 950


async def test_settle_above_reserve_is_capped_and_reconciled() -> None:
    from app.models import ReconciliationItem
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=1000, reference="topup-5")
        await db.commit()
        held = await ledger_service.reserve(db, wallet=wallet, request_ref="over", amount_kopecks=100)
        _, charged = await ledger_service.settle(db, reserve=held, cost_rub=Decimal("5.00"))
        await db.commit()
        await db.refresh(wallet)
        items = (
            await db.execute(select(ReconciliationItem).where(ReconciliationItem.kind == "reserve_exceeded"))
        ).scalars().all()
    assert charged == 100
    assert wallet.balance_kopecks == 900
    assert len(items) == 1
    assert items[0].payload["overage_kopecks"] == 400


async def test_release_returns_held_funds() -> None:
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=1000, reference="topup-6")
        await db.commit()
        held = await ledger_service.reserve(db, wallet=wallet, request_ref="rel", amount_kopecks=400)
        await ledger_service.release(db, reserve=held, reason="upstream error before send")
        await db.commit()
        await db.refresh(wallet)
    assert wallet.reserved_kopecks == 0
    assert wallet.balance_kopecks == 1000
    assert held.status == "released"


async def test_unknown_usage_keeps_reserve_and_opens_item() -> None:
    from app.models import ReconciliationItem
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=1000, reference="topup-7")
        await db.commit()
        held = await ledger_service.reserve(db, wallet=wallet, request_ref="unknown", amount_kopecks=100)
        item = await ledger_service.flag_reconciliation(
            db, request_ref="unknown", kind="usage_unknown", payload={"note": "no usage in response"}, reserve=held
        )
        await db.commit()
        await db.refresh(wallet)
        open_items = await ledger_service.open_reconciliation(db)
    assert item.status == "open"
    assert held.status == "reconciliation"
    # Money must not move on an ambiguous outcome.
    assert wallet.balance_kopecks == 1000
    assert wallet.reserved_kopecks == 100
    assert any(row.kind == "usage_unknown" for row in open_items)


async def test_concurrent_reserves_never_overspend() -> None:
    """100 simultaneous reserves against 550 kopecks: exactly 55 succeed."""
    from app.services import ledger as ledger_service

    async with await _new_session() as db:
        wallet = await _wallet(db)
        await ledger_service.topup(db, wallet=wallet, amount_kopecks=550, reference="topup-race")
        await db.commit()
        wallet_id = wallet.id

    async def attempt(index: int) -> bool:
        from app.models import Wallet

        async with await _new_session() as db:
            wallet = await db.get(Wallet, wallet_id)
            assert wallet is not None
            try:
                await ledger_service.reserve(
                    db, wallet=wallet, request_ref=f"race-{index}", amount_kopecks=10
                )
                await db.commit()
                return True
            except ApiError:
                await db.rollback()
                return False

    results = await asyncio.gather(*(attempt(index) for index in range(100)))
    succeeded = sum(1 for ok in results if ok)
    assert succeeded == 55

    async with await _new_session() as db:
        from app.models import Reserve, Wallet

        wallet = (await db.execute(select(Wallet).where(Wallet.id == wallet_id))).scalar_one()
        held = (
            await db.execute(
                select(func.count(Reserve.id)).where(Reserve.status == "held")
            )
        ).scalar_one()
        reserved_sum = (
            await db.execute(select(func.coalesce(func.sum(Reserve.amount_kopecks), 0)).where(Reserve.status == "held"))
        ).scalar_one()

    assert held == 55
    assert reserved_sum == 550
    assert wallet.reserved_kopecks == 550
    assert wallet.balance_kopecks - wallet.reserved_kopecks == 0
