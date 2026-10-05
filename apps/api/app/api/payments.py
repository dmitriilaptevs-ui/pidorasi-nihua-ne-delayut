"""Payment endpoints: create a prepayment, receive provider notifications."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_db, require_origin
from ..errors import ApiError, api_error_handler
from ..models import Payment, User
from ..schemas import PaymentCreateRequest
from ..services import payments as payment_service
from ..settings import settings

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _view(payment: Payment) -> dict[str, object]:
    return {
        "id": str(payment.id),
        "status": payment.status,
        "amount_kopecks": payment.amount_kopecks,
        "currency": payment.currency,
        "refunded_kopecks": payment.refunded_kopecks,
        "confirmation_url": payment.confirmation_url,
        "provider": payment.provider,
        "created_at": payment.created_at.isoformat(),
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
    }


@router.post("", status_code=201)
async def create_payment(
    payload: PaymentCreateRequest,
    request: Request,
    _: None = Depends(require_origin),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    # The return URL is derived from the configured public origin, never from
    # the request body, so it cannot be pointed at another host.
    return_url = f"{settings.public_origin}/account#payments"
    payment = await payment_service.create_payment(
        db, user=user, amount_kopecks=payload.amount_kopecks, return_url=return_url
    )
    await db.commit()
    return {"payment": _view(payment)}


@router.get("")
async def list_payments(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    payments = (
        await db.execute(
            select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(50)
        )
    ).scalars().all()
    return {"items": [_view(payment) for payment in payments]}


@router.post("/webhook")
async def webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """Provider notification. The body is verified against the provider API."""
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid_notification", "Некорректное уведомление.") from None
    payment = await payment_service.handle_notification(db, payload=payload)
    await db.commit()
    return {"ok": True, "payment_id": str(payment.id), "status": payment.status}
