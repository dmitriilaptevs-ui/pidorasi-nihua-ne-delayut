"""Decimal pricing math: conversion, markup and kopeck rounding."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.pricing import (
    as_decimal,
    cost_rub,
    kopecks_to_rub,
    rub_per_mtok,
    to_kopecks,
    usd_per_mtok,
)


def test_usd_per_token_is_scaled_to_millions() -> None:
    assert usd_per_mtok("0.0000015") == Decimal("1.500000")
    assert usd_per_mtok("0") == Decimal("0.000000")


def test_rub_conversion_applies_fx_and_markup() -> None:
    # 1.5 USD/Mtok * 100 RUB/USD * 1.2 markup = 180 RUB/Mtok
    assert rub_per_mtok(Decimal("1.5"), Decimal("100"), Decimal("1.2")) == Decimal("180.000000")


def test_rub_conversion_rounds_half_up_at_six_decimals() -> None:
    value = rub_per_mtok(Decimal("0.0000005"), Decimal("100"), Decimal("1.3333"))
    assert value == Decimal("0.000067")


def test_fractional_rub_survives_quantization() -> None:
    value = rub_per_mtok(Decimal("1.23456789"), Decimal("97.5"), Decimal("1.15"))
    assert value == Decimal("138.425925")  # 1.23456789*97.5*1.15 = 138.42592466625


def test_cost_for_tokens_is_exact() -> None:
    assert cost_rub(tokens=1000, rub_per_mtok_value=Decimal("180.000000")) == Decimal("0.18")
    assert cost_rub(tokens=1, rub_per_mtok_value=Decimal("180.000000")) == Decimal("0.00018")


def test_kopeck_rounding_is_half_up() -> None:
    assert to_kopecks(Decimal("0.005")) == 1
    assert to_kopecks(Decimal("0.0049")) == 0
    assert to_kopecks(Decimal("1.235")) == 124
    assert kopecks_to_rub(124) == Decimal("1.24")


def test_negative_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        cost_rub(tokens=-1, rub_per_mtok_value=Decimal("1"))
    with pytest.raises(ValueError):
        rub_per_mtok(Decimal("1"), Decimal("0"), Decimal("1.2"))
    with pytest.raises(ValueError):
        as_decimal("not-a-number")
