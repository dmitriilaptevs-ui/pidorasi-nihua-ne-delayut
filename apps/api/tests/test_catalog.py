"""Catalog sync: immutable pricing versions and RUB exposure."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

FEED = [
    {
        "id": "acme/chat-1",
        "name": "Acme Chat 1",
        "context_length": 128000,
        "supported_parameters": ["tools", "temperature"],
        "architecture": {"input_modalities": ["text"]},
        "pricing": {"prompt": "0.0000015", "completion": "0.000002"},
    }
]


def feed(*entries: dict):
    async def fetch() -> list[dict]:
        return [dict(entry) for entry in entries]

    return fetch


async def sync(entries: list[dict]):
    from app.db import session_factory
    from app.services import catalog as catalog_service

    async with session_factory()() as db:
        report = await catalog_service.sync_catalog(db, fetch=feed(*entries))
        await db.commit()
    return report


async def pricing_rows() -> list:
    from app.db import session_factory
    from app.models import CatalogPricing

    async with session_factory()() as db:
        result = await db.execute(select(CatalogPricing).order_by(CatalogPricing.version))
        return list(result.scalars().all())


async def test_sync_creates_model_with_rub_pricing() -> None:
    report = await sync(FEED)
    assert (report.seen, report.created, report.repriced) == (1, 1, 0)
    rows = await pricing_rows()
    assert len(rows) == 1
    row = rows[0]
    # 1.5 USD/Mtok * 100 * 1.2 = 180 RUB/Mtok
    assert row.input_rub_per_mtok == Decimal("180.000000")
    assert row.output_rub_per_mtok == Decimal("240.000000")
    assert row.version == 1
    assert row.retired_at is None


async def test_repricing_adds_a_version_and_never_edits_history() -> None:
    await sync(FEED)
    changed = [{**FEED[0], "pricing": {"prompt": "0.000003", "completion": "0.000002"}}]
    report = await sync(changed)
    assert report.repriced == 1

    rows = await pricing_rows()
    assert [row.version for row in rows] == [1, 2]
    old, new = rows
    assert old.retired_at is not None
    assert old.input_rub_per_mtok == Decimal("180.000000")  # historical value untouched
    assert new.input_rub_per_mtok == Decimal("360.000000")
    assert new.retired_at is None


async def test_same_price_creates_no_new_version() -> None:
    await sync(FEED)
    report = await sync(FEED)
    assert report.unchanged == 1
    assert len(await pricing_rows()) == 1


async def test_disappeared_model_becomes_unavailable_but_keeps_history() -> None:
    await sync(FEED)
    report = await sync([{**FEED[0], "id": "acme/other"}])
    assert report.unavailable == 1

    from app.db import session_factory
    from app.models import CatalogModel

    async with session_factory()() as db:
        rows = (await db.execute(select(CatalogModel).order_by(CatalogModel.openrouter_id))).scalars().all()
        by_id = {row.openrouter_id: row for row in rows}
        assert by_id["acme/chat-1"].available is False
        assert by_id["acme/other"].available is True


async def test_catalog_endpoint_exposes_rub_prices(client) -> None:
    await sync(FEED)
    response = await client.get("/api/catalog")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "acme/chat-1"
    assert item["supports_tools"] is True
    assert item["pricing"]["input_rub_per_mtok"] == "180.000000"
    assert item["pricing"]["output_rub_per_mtok"] == "240.000000"
    assert item["pricing"]["fx_rate"] == "100.00000000"
    assert item["pricing"]["markup"] == "1.2000"
