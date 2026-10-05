"""Payments: prepayment, webhook verification and refunds.

Security rules (ADR-0004):

- the webhook body is never trusted: every notification triggers a fresh fetch
  of the payment from the provider, and the credited amount comes from that
  fetch (amount and currency must match what we stored);
- the ledger reference is unique per provider payment, so duplicate, replayed
  or repeated notifications credit exactly once;
- refunds are ledger adjustments with their own reference, never edits.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import Payment
from ..services import ledger as ledger_service
from ..settings import settings

PROVIDER_TIMEOUT_SECONDS = 20.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def rub_string(kopecks: int) -> str:
    return f"{Decimal(kopecks) / 100:.2f}"


def kopecks_from_value(value: object) -> int:
    amount = Decimal(str(value))
    if amount < 0:
        raise ApiError(409, "amount_mismatch", "Некорректная сумма от платёжного провайдера.")
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ProviderPayment:
    id: str
    status: str
    amount_kopecks: int
    currency: str
    confirmation_url: str | None = None


class PaymentProvider(Protocol):
    async def create_payment(
        self,
        *,
        amount_kopecks: int,
        idempotence_key: str,
        description: str,
        metadata: dict,
        return_url: str,
    ) -> ProviderPayment: ...

    async def fetch_payment(self, provider_payment_id: str) -> ProviderPayment: ...

    async def create_refund(
        self, *, provider_payment_id: str, amount_kopecks: int, idempotence_key: str
    ) -> str: ...


def _parse_payment(data: dict) -> ProviderPayment:
    amount = data.get("amount") if isinstance(data.get("amount"), dict) else {}
    confirmation = data.get("confirmation") if isinstance(data.get("confirmation"), dict) else {}
    return ProviderPayment(
        id=str(data.get("id") or ""),
        status=str(data.get("status") or ""),
        amount_kopecks=kopecks_from_value(amount.get("value", "0")),
        currency=str(amount.get("currency") or "RUB"),
        confirmation_url=confirmation.get("confirmation_url"),
    )


class YooKassaProvider:
    """Documented YooKassa API (v3). Test keys start with ``test_``."""

    def __init__(self, *, shop_id: str, secret_key: str, base_url: str) -> None:
        self.shop_id = shop_id
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")

    def _headers(self, idempotence_key: str | None = None) -> dict[str, str]:
        token = base64.b64encode(f"{self.shop_id}:{self.secret_key}".encode()).decode()
        headers = {"Authorization": f"Basic {token}", "content-type": "application/json"}
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key
        return headers

    async def create_payment(
        self,
        *,
        amount_kopecks: int,
        idempotence_key: str,
        description: str,
        metadata: dict,
        return_url: str,
    ) -> ProviderPayment:
        payload = {
            "amount": {"value": rub_string(amount_kopecks), "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": metadata,
        }
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{self.base_url}/payments", json=payload, headers=self._headers(idempotence_key)
            )
        if response.status_code >= 400:
            raise ApiError(502, "provider_error", "Платёжный провайдер отклонил создание платежа.")
        return _parse_payment(response.json())

    async def fetch_payment(self, provider_payment_id: str) -> ProviderPayment:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{self.base_url}/payments/{provider_payment_id}", headers=self._headers()
            )
        if response.status_code >= 400:
            raise ApiError(404, "payment_unknown", "Платёж не найден у провайдера.")
        return _parse_payment(response.json())

    async def create_refund(
        self, *, provider_payment_id: str, amount_kopecks: int, idempotence_key: str
    ) -> str:
        payload = {
            "payment_id": provider_payment_id,
            "amount": {"value": rub_string(amount_kopecks), "currency": "RUB"},
        }
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{self.base_url}/refunds", json=payload, headers=self._headers(idempotence_key)
            )
        if response.status_code >= 400:
            raise ApiError(502, "provider_error", "Платёжный провайдер отклонил возврат.")
        return str(response.json().get("id") or "")


class FakeProvider:
    """Deterministic local provider for tests and offline development."""

    def __init__(self) -> None:
        self.payments: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}

    async def create_payment(
        self,
        *,
        amount_kopecks: int,
        idempotence_key: str,
        description: str,
        metadata: dict,
        return_url: str,
    ) -> ProviderPayment:
        provider_id = f"fake-{uuid.uuid4().hex[:12]}"
        self.payments[provider_id] = {
            "status": "pending",
            "amount_kopecks": amount_kopecks,
            "currency": "RUB",
        }
        return ProviderPayment(
            id=provider_id,
            status="pending",
            amount_kopecks=amount_kopecks,
            currency="RUB",
            confirmation_url=f"https://fake.local/pay/{provider_id}",
        )

    async def fetch_payment(self, provider_payment_id: str) -> ProviderPayment:
        record = self.payments.get(provider_payment_id)
        if record is None:
            raise ApiError(404, "payment_unknown", "Платёж не найден у провайдера.")
        return ProviderPayment(
            id=provider_payment_id,
            status=record["status"],
            amount_kopecks=record["amount_kopecks"],
            currency=record["currency"],
        )

    async def create_refund(
        self, *, provider_payment_id: str, amount_kopecks: int, idempotence_key: str
    ) -> str:
        refund_id = f"fake-refund-{uuid.uuid4().hex[:10]}"
        self.refunds[refund_id] = {
            "payment_id": provider_payment_id,
            "amount_kopecks": amount_kopecks,
        }
        return refund_id

    # --- test helpers ---
    def mark_succeeded(self, provider_payment_id: str) -> None:
        self.payments[provider_payment_id]["status"] = "succeeded"

    def mark_canceled(self, provider_payment_id: str) -> None:
        self.payments[provider_payment_id]["status"] = "canceled"

    def override_amount(self, provider_payment_id: str, amount_kopecks: int) -> None:
        self.payments[provider_payment_id]["amount_kopecks"] = amount_kopecks


fake_provider = FakeProvider()


def get_provider() -> PaymentProvider:
    if settings.payments_provider == "fake":
        return fake_provider
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise ApiError(503, "payments_not_configured", "Платёжный провайдер ещё не настроен.")
    return YooKassaProvider(
        shop_id=settings.yookassa_shop_id,
        secret_key=settings.yookassa_secret_key,
        base_url=settings.yookassa_api_base,
    )


async def create_payment(
    db: AsyncSession, *, user, amount_kopecks: int, return_url: str
) -> Payment:
    if not settings.payments_min_kopecks <= amount_kopecks <= settings.payments_max_kopecks:
        raise ApiError(
            400,
            "invalid_amount",
            f"Сумма должна быть от {settings.payments_min_kopecks // 100} до {settings.payments_max_kopecks // 100} ₽.",
        )
    provider = get_provider()
    idempotence_key = uuid.uuid4().hex
    remote = await provider.create_payment(
        amount_kopecks=amount_kopecks,
        idempotence_key=idempotence_key,
        description=f"Пополнение баланса {user.email or user.id}",
        metadata={"user_id": str(user.id)},
        return_url=return_url,
    )
    if not remote.id:
        raise ApiError(502, "provider_error", "Провайдер не вернул идентификатор платежа.")
    payment = Payment(
        user_id=user.id,
        provider=settings.payments_provider,
        provider_payment_id=remote.id,
        idempotence_key=idempotence_key,
        amount_kopecks=amount_kopecks,
        currency=remote.currency,
        status="pending",
        confirmation_url=remote.confirmation_url,
    )
    db.add(payment)
    await db.flush()
    return payment


async def get_payment(db: AsyncSession, *, provider_payment_id: str) -> Payment:
    payment = (
        await db.execute(select(Payment).where(Payment.provider_payment_id == provider_payment_id))
    ).scalar_one_or_none()
    if payment is None:
        raise ApiError(404, "payment_unknown", "Платёж не найден.")
    return payment


async def credit_payment(db: AsyncSession, *, payment: Payment) -> Payment:
    """Credit the wallet exactly once, guarded by the ledger reference."""
    if payment.status == "succeeded":
        return payment
    wallet = await ledger_service.get_or_create_wallet(db, user_id=payment.user_id)
    await ledger_service.topup(
        db,
        wallet=wallet,
        amount_kopecks=payment.amount_kopecks,
        reference=f"payment:{payment.provider_payment_id}",
        memo=f"{payment.provider} payment",
    )
    payment.status = "succeeded"
    payment.paid_at = utcnow()
    await db.flush()
    return payment


async def handle_notification(db: AsyncSession, *, payload: object) -> Payment:
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_notification", "Некорректное уведомление.")
    obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
    provider_payment_id = str(obj.get("id") or "").strip()
    if not provider_payment_id:
        raise ApiError(400, "invalid_notification", "Уведомление без идентификатора платежа.")

    payment = await get_payment(db, provider_payment_id=provider_payment_id)
    # Authoritative state comes from the provider, never from the notification.
    remote = await get_provider().fetch_payment(provider_payment_id)
    if remote.currency != payment.currency or remote.amount_kopecks != payment.amount_kopecks:
        raise ApiError(409, "amount_mismatch", "Сумма или валюта платежа не совпадают.")
    if remote.status == "succeeded":
        await credit_payment(db, payment=payment)
    elif remote.status == "canceled":
        payment.status = "canceled"
        await db.flush()
    return payment


async def refund_payment(
    db: AsyncSession, *, payment: Payment, amount_kopecks: int | None = None
) -> tuple[Payment, str]:
    if payment.status not in ("succeeded", "partially_refunded"):
        raise ApiError(409, "payment_not_refundable", "Возврат возможен только по успешному платежу.")
    remaining = payment.amount_kopecks - payment.refunded_kopecks
    amount = remaining if amount_kopecks is None else amount_kopecks
    if amount <= 0 or amount > remaining:
        raise ApiError(400, "invalid_amount", "Некорректная сумма возврата.")

    refund_id = await get_provider().create_refund(
        provider_payment_id=payment.provider_payment_id,
        amount_kopecks=amount,
        idempotence_key=uuid.uuid4().hex,
    )
    wallet = await ledger_service.get_or_create_wallet(db, user_id=payment.user_id)
    await ledger_service.adjust(
        db,
        wallet=wallet,
        amount_kopecks=-amount,
        reference=f"refund:{refund_id or uuid.uuid4().hex}",
        memo="refund",
    )
    payment.refunded_kopecks += amount
    payment.status = "refunded" if payment.refunded_kopecks >= payment.amount_kopecks else "partially_refunded"
    await db.flush()
    return payment, refund_id
