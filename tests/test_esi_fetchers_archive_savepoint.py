"""Archive failures must not abort the owner batch transaction.

A swallowed SQL error inside an archive step used to leave the batch
connection in an aborted state, so every later kind for that owner failed
too. Archive bodies now run under storage.savepoint().
"""
from __future__ import annotations

import logging

import pytest

from eve_trader import storage
from eve_trader.esi_data import fetchers

from . import pg_helpers
from .pg_helpers import tenant  # noqa: F401

pytestmark = pg_helpers.postgres_required()


def test_archive_sql_error_inside_batch_leaves_snapshot_and_later_writes(tenant, monkeypatch, caplog):
    def _boom(**_kwargs):
        with storage.connect() as conn:
            conn.execute("SELECT 1 FROM definitely_not_a_table")

    monkeypatch.setattr(fetchers, "_archive_for_production", lambda *a, **k: True)
    monkeypatch.setattr(
        "eve_trader.realized.ingest.archive_blueprints", _boom, raising=False,
    )

    with caplog.at_level(logging.ERROR):
        with storage.batch_session():
            storage.replace_assets(
                "character_assets",
                [(1, 34, 60003760, "Hangar", 5, 0, "Alice")],
                owner_character_id=7, owner_name="Alice",
            )
            fetchers._archive_blueprints("character", 7, [{"item_id": 1}])
            with storage.connect() as conn:
                conn.execute(
                    "INSERT INTO shortlist (item_id, item, category, volume_m3, active) "
                    "VALUES (993, 'later-kind', 'y', 1.0, 1)"
                )

    with storage.connect() as conn:
        asset = conn.execute(
            "SELECT quantity FROM character_assets WHERE item_id = 1"
        ).fetchone()
        later = conn.execute(
            "SELECT item FROM shortlist WHERE item_id = 993"
        ).fetchone()
    assert asset == (5,)
    assert later == ("later-kind",)
    assert any("ledger blueprints archive failed" in r.message for r in caplog.records)
