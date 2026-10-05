"""Payments: exactly-once credit, forged notifications, refunds."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.errors import ApiError
from app.services import payments as payment_service

AMOUNT = 50_000  # 500.00 RUB


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "payments_provider", "fake")
    payment_service.fake_provider.payments.clear()
    payment_service.fake_provider.refunds.clear()
    return payment_service.fake_provider


async def _new_user(*, seed_kopecks: int = 0):
    from app.db import session_factory
    from app.services import identity as identity_service
    from app.services import ledger as ledger_service

    async with session_factory()() as db:
        user, _ = await identity_service.register(
            db, email=f"pay-{uuid.uuid4().hex[:10]}@example.com", password="payments-password-1"
        )
        user.email_verified_at = identity_service.utcnow()
        if seed_kopecks:
            wallet = await ledger_service.get_or_create_wallet(db, user_id=user.id)
            await ledger_service.topup(
                db, wallet=wallet, amount_kopecks=seed_kopecks, reference=f"seed-{uuid.uuid4().hex}"
            )
        await db.commit()
        return user.id


async def _wallet_balance(user_id) -> int:
    from app.db import session_factory
    from app.services import ledger as ledger_service

    async with session_factory()() as db:
        wallet = await ledger_service.get_or_create_wallet(db, user_id=user_id)
        await db.commit()
        return wallet.balance_kopecks


async def _create(user_id, amount: int = AMOUNT):
    from app.db import session_factory
    from app.models import User

    async with session_factory()() as db:
        user = await db.get(User, user_id)
        payment = await payment_service.create_payment(
            db, user=user, amount_kopecks=amount, return_url="https://example.invalid/account"
        )
        await db.commit()
        return payment.provider_payment_id


async def _notify(provider_payment_id: str, *, body_amount: str | None = None):
    from app.db import session_factory

    payload = {"type": "notification", "event": "payment.succeeded", "object": {"id": provider_payment_id}}
    if body_amount is not None:
        payload["object"]["amount"] = {"value": body_amount, "currency": "RUB"}
    async with session_factory()() as db:
        payment = await payment_service.handle_notification(db, payload=payload)
        await db.commit()
        return payment


async def test_create_payment_is_pending_until_confirmed() -> None:
    user_id = await _new_user()
    provider_id = await _create(user_id)
    assert provider_id.startswith("fake-")
    assert await _wallet_balance(user_id) == 0

    from app.db import session_factory
    from app.models import Payment

    async with session_factory()() as db:
        stored = (await db.execute(select(Payment).where(Payment.provider_payment_id == provider_id))).scalar_one()
        assert stored.status == "pending"
        assert stored.confirmation_url and stored.confirmation_url.startswith("https://fake.local/")


async def test_amount_bounds_are_enforced() -> None:
    user_id = await _new_user()
    with pytest.raises(ApiError) as too_small:
        await _create(user_id, amount=1)
    assert too_small.value.code == "invalid_amount"
    with pytest.raises(ApiError):
        await _create(user_id, amount=999_999_999)


async def test_notification_credits_exactly_once() -> None:
    user_id = await _new_user()
    provider_id = await _create(user_id)
    payment_service.fake_provider.mark_succeeded(provider_id)

    first = await _notify(provider_id)
    assert first.status == "succeeded"
    assert await _wallet_balance(user_id) == AMOUNT

    # Duplicate/replayed notification: the ledger reference makes it a no-op.
    second = await _notify(provider_id)
    assert second.status == "succeeded"
    assert await _wallet_balance(user_id) == AMOUNT

    from app.db import session_factory
    from app.models import LedgerTransaction

    async with session_factory()() as db:
        credits = (
            await db.execute(
                select(LedgerTransaction).where(LedgerTransaction.reference == f"payment:{provider_id}")
            )
        ).scalars().all()
    assert len(credits) == 1


async def test_forged_body_amount_is_ignored() -> None:
    user_id = await _new_user()
    provider_id = await _create(user_id)
    payment_service.fake_provider.mark_succeeded(provider_id)

    # The notification claims 999.00, the provider says 500.00: the provider wins.
    await _notify(provider_id, body_amount="999.00")
    assert await _wallet_balance(user_id) == AMOUNT


async def test_amount_mismatch_is_rejected() -> None:
    user_id = await _new_user()
    provider_id = await _create(user_id)
    payment_service.fake_provider.mark_succeeded(provider_id)
    payment_service.fake_provider.override_amount(provider_id, AMOUNT + 1)

    with pytest.raises(ApiError) as exc:
        await _notify(provider_id)
    assert exc.value.code == "amount_mismatch"
    assert await _wallet_balance(user_id) == 0


async def test_canceled_payment_does_not_credit() -> None:
    user_id = await _new_user()
    provider_id = await _create(user_id)
    payment_service.fake_provider.mark_canceled(provider_id)

    payment = await _notify(provider_id)
    assert payment.status == "canceled"
    assert await _wallet_balance(user_id) == 0


async def test_unknown_payment_is_rejected() -> None:
    with pytest.raises(ApiError) as exc:
        await _notify("fake-does-not-exist")
    assert exc.value.status_code == 404


async def test_partial_then_full_refund_updates_ledger_and_status() -> None:
    user_id = await _new_user()
    provider_id = await _create(user_id)
    payment_service.fake_provider.mark_succeeded(provider_id)
    await _notify(provider_id)
    assert await _wallet_balance(user_id) == AMOUNT

    from app.db import session_factory
    from app.models import Payment

    async with session_factory()() as db:
        stored = (await db.execute(select(Payment).where(Payment.provider_payment_id == provider_id))).scalar_one()
        partial, refund_id = await payment_service.refund_payment(db, payment=stored, amount_kopecks=20_000)
        await db.commit()
        assert refund_id.startswith("fake-refund-")
        assert partial.status == "partially_refunded"
        assert partial.refunded_kopecks == 20_000
    assert await _wallet_balance(user_id) == AMOUNT - 20_000

    async with session_factory()() as db:
        stored = (await db.execute(select(Payment).where(Payment.provider_payment_id == provider_id))).scalar_one()
        full, _ = await payment_service.refund_payment(db, payment=stored)
        await db.commit()
        assert full.status == "refunded"
        assert full.refunded_kopecks == AMOUNT
    assert await _wallet_balance(user_id) == 0

    async with session_factory()() as db:
        stored = (await db.execute(select(Payment).where(Payment.provider_payment_id == provider_id))).scalar_one()
        with pytest.raises(ApiError) as exc:
            await payment_service.refund_payment(db, payment=stored)
        assert exc.value.code == "payment_not_refundable"  # already fully refunded
        await db.rollback()


async def test_unconfigured_provider_blocks_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "payments_provider", "yookassa")
    monkeypatch.setattr(settings, "yookassa_shop_id", "")
    monkeypatch.setattr(settings, "yookassa_secret_key", "")

    user_id = await _new_user()
    with pytest.raises(ApiError) as exc:
        await _create(user_id)
    assert exc.value.code == "payments_not_configured"


async def test_api_create_and_webhook_flow(client) -> None:
    """End-to-end through HTTP: create with a session cookie, then notify."""
    from app.db import session_factory
    from app.services import identity as identity_service

    async with session_factory()() as db:
        user, _ = await identity_service.register(
            db, email=f"pay-api-{uuid.uuid4().hex[:8]}@example.com", password="payments-password-1"
        )
        user.email_verified_at = identity_service.utcnow()
        _, raw_token = await identity_service.create_session(db, user=user, method="password")
        await db.commit()
        user_id = user.id

    client.cookies.set("rb_platform_session", raw_token)
    created = await client.post("/api/payments", json={"amount_kopecks": AMOUNT})
    assert created.status_code == 201, created.text
    provider_id = None
    payment_list = await client.get("/api/payments")
    assert payment_list.status_code == 200
    items = payment_list.json()["items"]
    assert items and items[0]["status"] == "pending"
    assert items[0]["confirmation_url"]

    from app.models import Payment

    async with session_factory()() as db:
        stored = (await db.execute(select(Payment).where(Payment.user_id == user_id))).scalar_one()
        provider_id = stored.provider_payment_id

    payment_service.fake_provider.mark_succeeded(provider_id)
    notified = await client.post("/api/payments/webhook", json={"event": "payment.succeeded", "object": {"id": provider_id}})
    assert notified.status_code == 200, notified.text
    assert notified.json()["status"] == "succeeded"
    assert await _wallet_balance(user_id) == AMOUNT
