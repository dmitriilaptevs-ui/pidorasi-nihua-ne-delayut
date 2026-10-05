"""Gateway orchestration helpers: auth, validation, estimates, costs.

The order is fixed: authenticate the platform key, resolve the catalog model,
estimate a bounded maximum cost, commit a reserve, and only then call the
provider. Usage is turned into an exact Decimal cost and settled against the
reserve; anything ambiguous becomes a reconciliation item.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import ApiKey, CatalogModel, CatalogPricing, Reserve, User
from ..pricing import cost_rub
from ..services import keys as key_service
from ..services import ledger as ledger_service
from ..settings import settings

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4


class GatewayError(ApiError):
    """OpenAI-shaped error carrying an error type."""

    def __init__(self, status_code: int, message: str, code: str = "invalid_request", error_type: str = "invalid_request_error") -> None:
        super().__init__(status_code, code, message)
        self.error_type = error_type

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": self.error_type, "code": self.code}}


def oai_payload(exc: ApiError) -> dict:
    if isinstance(exc, GatewayError):
        return exc.body()
    error_type = "invalid_request_error" if exc.status_code < 500 else "server_error"
    return {"error": {"message": exc.message, "type": error_type, "code": exc.code}}


async def authenticate(db: AsyncSession, *, authorization: str | None) -> tuple[User, ApiKey]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise GatewayError(401, "Missing bearer API key.", "missing_api_key", "authentication_error")
    raw_key = authorization.split(" ", 1)[1].strip()
    resolved = await key_service.authenticate_api_key(db, raw_key=raw_key)
    if resolved is None:
        raise GatewayError(401, "Invalid, revoked or disabled API key.", "invalid_api_key", "authentication_error")
    return resolved


def validate_chat_request(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise GatewayError(400, "Request body must be a JSON object.")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise GatewayError(400, "A catalog model id is required.")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise GatewayError(400, "messages must be a non-empty array.")
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict) or "role" not in message:
            raise GatewayError(400, "Each message needs a role.")
        content = message.get("content")
        if content is None:
            continue
        if not isinstance(content, (str, list)):
            raise GatewayError(400, "Message content must be a string or an array.")
        total_chars += len(content if isinstance(content, str) else json.dumps(content))
    if total_chars > settings.gateway_max_input_chars:
        raise GatewayError(413, "Input is too large for this gateway.", "input_too_large")

    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise GatewayError(400, "stream must be a boolean.")

    max_tokens = payload.get("max_tokens")
    if max_tokens is None:
        max_tokens = settings.gateway_default_max_tokens
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > settings.gateway_max_output_tokens:
        raise GatewayError(
            400,
            f"max_tokens must be an integer between 1 and {settings.gateway_max_output_tokens}.",
            "invalid_max_tokens",
        )
    return {"model": model.strip(), "stream": stream, "max_tokens": max_tokens, "raw": payload}


async def resolve_model(db: AsyncSession, *, model_id: str) -> tuple[CatalogModel, CatalogPricing]:
    model = (
        await db.execute(select(CatalogModel).where(CatalogModel.openrouter_id == model_id))
    ).scalar_one_or_none()
    if model is None or not model.available:
        raise GatewayError(404, f"Model '{model_id}' is not available.", "model_not_found")
    pricing = (
        await db.execute(
            select(CatalogPricing).where(
                CatalogPricing.model_id == model.id, CatalogPricing.retired_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if pricing is None:
        raise GatewayError(404, f"Model '{model_id}' has no active pricing.", "model_not_priced")
    return model, pricing


def estimate_input_tokens(messages: list) -> int:
    total_chars = 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += len(json.dumps(content))
    return total_chars // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS * max(1, len(messages))


@dataclass(frozen=True)
class PricingSnapshot:
    """Detached copy of a pricing row, safe to use after the request session."""

    input_rub_per_mtok: Decimal
    output_rub_per_mtok: Decimal
    cached_rub_per_mtok: Decimal | None = None


def snapshot_of(pricing: CatalogPricing) -> PricingSnapshot:
    return PricingSnapshot(
        input_rub_per_mtok=pricing.input_rub_per_mtok,
        output_rub_per_mtok=pricing.output_rub_per_mtok,
        cached_rub_per_mtok=pricing.cached_rub_per_mtok,
    )


def estimate_reserve_kopecks(pricing: PricingSnapshot | CatalogPricing, *, messages: list, max_tokens: int) -> int:
    """Conservative upper bound in kopecks; rounded up, never below one kopeck."""
    input_tokens = estimate_input_tokens(messages)
    estimated_rub = cost_rub(tokens=input_tokens, rub_per_mtok_value=pricing.input_rub_per_mtok) + cost_rub(
        tokens=max_tokens, rub_per_mtok_value=pricing.output_rub_per_mtok
    )
    if estimated_rub <= 0:
        return 0
    return max(1, int((estimated_rub * 100).to_integral_value(rounding=ROUND_CEILING)))


def compute_cost_rub(pricing: PricingSnapshot | CatalogPricing, usage: dict) -> Decimal:
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total = cost_rub(tokens=max(0, prompt_tokens), rub_per_mtok_value=pricing.input_rub_per_mtok) + cost_rub(
        tokens=max(0, completion_tokens), rub_per_mtok_value=pricing.output_rub_per_mtok
    )
    details = usage.get("prompt_tokens_details")
    cached = 0
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    if cached and pricing.cached_rub_per_mtok is not None:
        billed_full = max(0, prompt_tokens - cached)
        total = cost_rub(tokens=billed_full, rub_per_mtok_value=pricing.input_rub_per_mtok) + cost_rub(
            tokens=cached, rub_per_mtok_value=pricing.cached_rub_per_mtok
        ) + cost_rub(tokens=max(0, completion_tokens), rub_per_mtok_value=pricing.output_rub_per_mtok)
    return total


async def monthly_spend_kopecks(db: AsyncSession, *, api_key_id) -> int:
    """Settled spend of this key in the current calendar month."""
    month_start = func.date_trunc("month", func.now())
    settled = (
        await db.execute(
            select(func.coalesce(func.sum(Reserve.settled_kopecks), 0)).where(
                Reserve.api_key_id == api_key_id,
                Reserve.status == "settled",
                Reserve.released_at >= month_start,
            )
        )
    ).scalar_one()
    held = (
        await db.execute(
            select(func.coalesce(func.sum(Reserve.amount_kopecks), 0)).where(
                Reserve.api_key_id == api_key_id,
                Reserve.status.in_(("held", "reconciliation")),
            )
        )
    ).scalar_one()
    return int(settled) + int(held)


async def check_monthly_limit(
    db: AsyncSession, *, api_key: ApiKey, wallet, estimated_kopecks: int
) -> None:
    state = ledger_service.wallet_state(wallet)
    if estimated_kopecks > state["available_kopecks"]:
        raise GatewayError(
            402,
            "Insufficient balance for this request.",
            "insufficient_funds",
            "insufficient_quota",
        )
    if api_key.monthly_limit_kopecks is None:
        return
    spent = await monthly_spend_kopecks(db, api_key_id=api_key.id)
    if spent + estimated_kopecks > api_key.monthly_limit_kopecks:
        raise GatewayError(
            429,
            "Monthly key limit would be exceeded.",
            "key_limit_exceeded",
            "rate_limit_error",
        )


class UsageScanner:
    """Extract the final `usage` object from an SSE byte stream.

    Keeps a bounded tail so a long stream never accumulates in memory.
    """

    def __init__(self, limit: int = 512 * 1024) -> None:
        self._buffer = b""
        self._limit = limit
        self.usage: dict | None = None

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        if len(self._buffer) > self._limit:
            self._buffer = self._buffer[-self._limit :]
        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = rest
            self._scan(line)

    def _scan(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="ignore").strip()
        if not text.startswith("data:"):
            return
        data = text[5:].strip()
        if not data or data == "[DONE]":
            return
        try:
            parsed = json.loads(data)
        except ValueError:
            return
        if isinstance(parsed, dict) and isinstance(parsed.get("usage"), dict):
            self.usage = parsed["usage"]


def tokens_from_usage(usage: dict) -> dict[str, int]:
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def ceil_kopecks(rub: Decimal) -> int:
    if rub <= 0:
        return 0
    return int((rub * 100).to_integral_value(rounding=ROUND_CEILING))
