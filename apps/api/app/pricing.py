"""Exact decimal pricing math.

All money values are Decimal; never float. USD prices coming from the provider
are per token and are converted to RUB per million tokens with the configured
FX rate and markup, then costs are computed from actual token usage.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TOKENS_PER_MTOK = Decimal("1000000")
RUB_PER_MTOK_QUANTUM = Decimal("0.000001")  # six decimal places
KOPECK_QUANTUM = Decimal("1")


def as_decimal(value: object) -> Decimal:
    """Convert a provider value to Decimal without float artifacts."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("not a decimal value") from None


def usd_per_mtok(usd_per_token: object) -> Decimal:
    """Provider quotes USD per token; store USD per million tokens.

    Negative values are provider sentinels (for example ``-1`` for
    auto-routing models), never real prices: they are rejected so a negative
    price can never reach the catalog or a charge.
    """
    value = as_decimal(usd_per_token)
    if value < 0:
        raise ValueError("negative provider price")
    return (value * TOKENS_PER_MTOK).quantize(RUB_PER_MTOK_QUANTUM)


def rub_per_mtok(usd_per_mtok_value: Decimal, fx_rate: Decimal, markup: Decimal) -> Decimal:
    """USD/Mtok -> RUB/Mtok with FX and markup, rounded half-up to 6 decimals."""
    if usd_per_mtok_value < 0:
        raise ValueError("negative USD price")
    if fx_rate <= 0 or markup <= 0:
        raise ValueError("fx rate and markup must be positive")
    return (usd_per_mtok_value * fx_rate * markup).quantize(RUB_PER_MTOK_QUANTUM, rounding=ROUND_HALF_UP)


def cost_rub(*, tokens: int, rub_per_mtok_value: Decimal) -> Decimal:
    """Exact cost in RUB for a token count at a per-million-token price."""
    if tokens < 0:
        raise ValueError("token count must not be negative")
    return (Decimal(tokens) * rub_per_mtok_value / TOKENS_PER_MTOK)


def to_kopecks(amount_rub: Decimal) -> int:
    """Round a RUB amount to whole kopecks, half-up (no banker's rounding)."""
    return int((amount_rub * 100).quantize(KOPECK_QUANTUM, rounding=ROUND_HALF_UP))


def kopecks_to_rub(kopecks: int) -> Decimal:
    return (Decimal(kopecks) / 100).quantize(Decimal("0.01"))
