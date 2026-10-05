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
    payload = response.json()
    assert payload["total"] == 1
    items = payload["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "acme/chat-1"
    assert item["supports_tools"] is True
    assert item["pricing"]["input_rub_per_mtok"] == "180.000000"
    assert item["pricing"]["output_rub_per_mtok"] == "240.000000"
    assert item["pricing"]["fx_rate"] == "100.00000000"
    assert item["pricing"]["markup"] == "1.2000"


def _many_models(count: int) -> list[dict]:
    models = []
    for index in range(count):
        models.append(
            {
                "id": f"acme/model-{index:02d}",
                "name": f"Model {index:02d}",
                "context_length": 8192,
                "supported_parameters": [],
                "architecture": {"input_modalities": ["text"]},
                "pricing": {"prompt": str(0.000001 * (index + 1)), "completion": "0.000002"},
            }
        )
    return models


async def test_catalog_pagination_search_and_sort(client) -> None:
    await sync(_many_models(5))

    first = (await client.get("/api/catalog?limit=2&offset=0")).json()
    assert first["total"] == 5
    assert len(first["items"]) == 2
    assert first["items"][0]["id"] == "acme/model-00"

    second = (await client.get("/api/catalog?limit=2&offset=2")).json()
    assert [item["id"] for item in second["items"]] == ["acme/model-02", "acme/model-03"]

    last = (await client.get("/api/catalog?limit=2&offset=4")).json()
    assert len(last["items"]) == 1

    searched = (await client.get("/api/catalog?q=model-03")).json()
    assert searched["total"] == 1
    assert searched["items"][0]["id"] == "acme/model-03"

    cheap = (await client.get("/api/catalog?sort=price&limit=3")).json()
    prices = [float(item["pricing"]["input_rub_per_mtok"]) for item in cheap["items"]]
    assert prices == sorted(prices)

    capped = (await client.get("/api/catalog?limit=999"))
    assert capped.status_code == 422  # limit is bounded to protect the API


async def test_catalog_response_is_cached(client) -> None:
    await sync(FEED)
    first = (await client.get("/api/catalog")).json()
    second = (await client.get("/api/catalog")).json()
    assert first == second


SENTINEL_FEED = [
    {
        "id": "openrouter/auto",
        "name": "Auto Router",
        "context_length": 200000,
        "supported_parameters": ["tools"],
        "architecture": {"input_modalities": ["text"]},
        "pricing": {"prompt": "-1", "completion": "-1"},
    }
]


async def test_sentinel_prices_are_never_quoted() -> None:
    report = await sync(SENTINEL_FEED)
    assert report.seen == 0
    assert report.unavailable == 0
    assert await pricing_rows() == []

    from app.db import session_factory
    from app.models import CatalogModel

    async with session_factory()() as db:
        models = (await db.execute(select(CatalogModel))).scalars().all()
    assert models == []


async def test_sentinel_price_retires_a_previously_valid_version() -> None:
    await sync(FEED)
    assert len(await pricing_rows()) == 1

    # Feed switches to the -1 sentinel: the model must stop being quoted.
    report = await sync([{**FEED[0], "pricing": {"prompt": "-1", "completion": "-1"}}])
    assert report.unavailable == 1
    assert report.repriced == 1
    rows = await pricing_rows()
    assert rows[0].retired_at is not None  # no active negative quote remains
    assert rows[0].input_rub_per_mtok == Decimal("180.000000")  # history intact
