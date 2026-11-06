"""Stock observations require every assets-scope owner to be fresh enough.

Writing a tenant-wide quantity while another owner's assets are missing or
past the clear window would invent false reprocessing deficits.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.esi_data import fetchers

from . import pg_helpers
from .pg_helpers import tenant  # noqa: F401

pytestmark = pg_helpers.postgres_required()

ALICE = 930001
BOB = 930002


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("esi_sharing", "esi_freshness", "character_assets")
    yield


def _share(*owner_ids: int) -> None:
    with storage.connect() as conn:
        for oid in owner_ids:
            conn.execute(
                "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                "VALUES ('character', ?, 'assets', 'production') ON CONFLICT DO NOTHING",
                (oid,),
            )


def _fresh(owner_id: int, *, hours_ago: float) -> None:
    at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    storage.upsert_esi_freshness(
        "character", owner_id, "assets", success=True, now=at.isoformat(),
    )


def test_observation_blocked_when_another_owner_has_no_freshness_row(tenant, monkeypatch, caplog):
    _share(ALICE, BOB)
    _fresh(ALICE, hours_ago=1)
    called = []
    monkeypatch.setattr(
        "eve_trader.realized.ingest.archive_stock_observation",
        lambda: called.append(1) or 0,
    )
    with caplog.at_level(logging.WARNING):
        fetchers._archive_stock_observation("character", ALICE)
    assert called == []
    assert any("stock observation skipped" in r.message and "930002" in r.message
               for r in caplog.records)


def test_observation_blocked_when_another_owner_past_clear_window(tenant, monkeypatch, caplog):
    _share(ALICE, BOB)
    _fresh(ALICE, hours_ago=1)
    # assets normal tier 6h × multiples 3 = 18h
    _fresh(BOB, hours_ago=20)
    called = []
    monkeypatch.setattr(
        "eve_trader.realized.ingest.archive_stock_observation",
        lambda: called.append(1) or 0,
    )
    with caplog.at_level(logging.WARNING):
        fetchers._archive_stock_observation("character", ALICE)
    assert called == []
    assert any("stock observation skipped" in r.message for r in caplog.records)


def test_sync_owner_itself_does_not_block(tenant, monkeypatch):
    """The success stamp is written after the fetcher; exclude this owner."""
    _share(ALICE, BOB)
    _fresh(BOB, hours_ago=1)
    # Alice has no freshness row yet — she is the sync owner.
    called = []
    monkeypatch.setattr(
        "eve_trader.realized.ingest.archive_stock_observation",
        lambda: called.append(1) or 0,
    )
    monkeypatch.setattr(fetchers, "_archive_for_production", lambda *a, **k: True)
    fetchers._archive_stock_observation("character", ALICE)
    assert called == [1]


def test_observation_runs_when_every_other_owner_is_fresh(tenant, monkeypatch):
    _share(ALICE, BOB)
    _fresh(ALICE, hours_ago=1)
    _fresh(BOB, hours_ago=1)
    called = []
    monkeypatch.setattr(
        "eve_trader.realized.ingest.archive_stock_observation",
        lambda: called.append(1) or 0,
    )
    monkeypatch.setattr(fetchers, "_archive_for_production", lambda *a, **k: True)
    fetchers._archive_stock_observation("character", ALICE)
    assert called == [1]
