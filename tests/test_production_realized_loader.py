"""Phase 2 loader: D11-gated bulk reads, derived-table replace, T0 cutover.

Pure costing is covered by test_production_realized_costing.py. This file
hits Postgres (schema + RLS) the way Phase 1 archive tests do.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from eve_trader import storage
from eve_trader.production.realized import access as ledger_access
from eve_trader.production.realized import db as ledger_db
from eve_trader.production.realized import loader

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_DOCS = Path(__file__).resolve().parent.parent / "docs"
_LEDGER_SCHEMA_SQL = _DOCS / "production_ledger_schema.sql"
_SPECIAL_ORDERS_SCHEMA_SQL = _DOCS / "special_orders_schema.sql"

ALICE = 910001
TYPE_A = 34
TYPE_B = 35
LOC = 60003760
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def _apply_production_ledger_schema(_apply_phase1_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_LEDGER_SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(_SPECIAL_ORDERS_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _wipe(_apply_production_ledger_schema):
    pg_helpers.wipe_tables(
        "ledger_wallet_transactions", "ledger_wallet_journal",
        "ledger_industry_jobs", "ledger_job_inputs", "ledger_job_planned_costs",
        "ledger_blueprints",
        "ledger_market_orders", "ledger_contracts", "ledger_contract_items",
        "ledger_archive_gaps", "ledger_sync_state", "ledger_reference_prices",
        "ledger_manual_entries", "prod_cost_layers", "prod_realized_results",
        "prod_cost_draws", "prod_job_records", "prod_fee_lines", "prod_job_losses",
        "prod_capital_bpos", "prod_inventory_drift", "prod_opening_frozen",
        "prod_opening_lines", "prod_ledger_runs", "esi_sharing", "character_assets", "corp_assets",
        "stock_targets",
    )
    yield


def _share(owner_id: int, data_kind: str):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, 'production') ON CONFLICT DO NOTHING",
            (owner_id, data_kind),
        )


def _seed_buy_and_sell(tenant):
    _share(ALICE, "wallet")
    _share(ALICE, "industry_jobs")
    _share(ALICE, "market_orders")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 100.0, 10, True, 101,
         10000002, 50.0, 2.0),
        ("character", ALICE, 0, 2, T1, TYPE_A, LOC, 200.0, 4, False, 102,
         None, None, None),
    ])
    # Stock target makes TYPE_A a Production type so the sell is realized.
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name, backup_stock) "
            "VALUES (?, 'Test', 1) ON CONFLICT DO NOTHING",
            (TYPE_A,),
        )


def test_rebuild_without_archive_records_empty_run(tenant):
    summary = loader.rebuild_realized()
    assert summary["cutover_t0"] is None
    assert summary["layers"] == 0
    assert "no wallet archive" in summary["warnings"][0]
    run = ledger_db.latest_ledger_run()
    assert run is not None
    assert run[0] == summary["run_id"]


def test_rebuild_writes_layers_and_results(tenant):
    _seed_buy_and_sell(tenant)
    # Avoid network: seed reference prices for opening/unknown days.
    ledger_db.upsert_reference_prices([
        (TYPE_A, T0.date(), 90.0, "test"),
        (TYPE_A, T1.date(), 90.0, "test"),
    ])

    with patch.object(loader, "_opening_stock", return_value={}):
        with patch.object(loader, "_fetch_reference_prices", side_effect=lambda requests, today: {
            key: 90.0 for key in requests
        }):
            with patch.object(loader, "_tenant_character_ids", return_value=frozenset({ALICE})):
                with patch.object(loader, "_production_type_ids", return_value=frozenset({TYPE_A})):
                    with patch.object(loader, "_relic_type_ids", return_value=frozenset()):
                        summary = loader.rebuild_realized()

    assert summary["cutover_t0"] is not None
    assert summary["wallet_transactions"] == 2
    assert summary["realized_results"] >= 1
    run = ledger_db.latest_ledger_run()
    assert run is not None
    with storage.connect() as conn:
        n_results = conn.execute("SELECT COUNT(*) FROM prod_realized_results").fetchone()[0]
        n_layers = conn.execute("SELECT COUNT(*) FROM prod_cost_layers").fetchone()[0]
    assert n_results == summary["realized_results"]
    assert n_layers == summary["layers"]


def test_revoked_share_hides_wallet_from_loader(tenant):
    _seed_buy_and_sell(tenant)
    with storage.connect() as conn:
        conn.execute(
            "DELETE FROM esi_sharing WHERE owner_id = ? AND data_kind = 'wallet'",
            (ALICE,),
        )
    # No shared wallet owners -> no T0.
    assert ledger_access.cutover_t0_for_production() is None
    summary = loader.rebuild_realized()
    assert summary["cutover_t0"] is None


def _journal(journal_id, when, ref_type="brokers_fee"):
    return ("character", ALICE, 0, journal_id, when, ref_type, -1.0,
            None, None, None, None, None, None, None)


def test_t0_moves_to_the_journal_start_when_the_journal_was_truncated(tenant):
    """Transactions reach back further than the journal: sales before the
    journal start would have no sales tax, so T0 is the journal start."""
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 1.0, 1, True, 1, None, None, None),
        ("character", ALICE, 0, 2, T1, TYPE_A, LOC, 1.0, 1, False, 2, None, None, None),
    ])
    ledger_db.upsert_ledger_wallet_journal([_journal(10, T1)])
    assert ledger_access.cutover_t0_for_production() == T1


def test_t0_is_not_moved_by_a_quiet_wallet(tenant):
    """A journal that starts with the wallet's first activity is complete."""
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T1, TYPE_A, LOC, 1.0, 1, True, 1, None, None, None),
    ])
    ledger_db.upsert_ledger_wallet_journal([_journal(10, T0), _journal(11, T1)])
    assert ledger_access.cutover_t0_for_production() == T1


class _Point:
    def __init__(self, type_id, day, avg):
        self.type_id, self.date, self.avg_price = type_id, day, avg


def test_reference_prices_fall_back_to_esi_and_nearest_day_and_cache_misses(tenant, monkeypatch):
    from datetime import date

    calls = {"goon": 0, "esi": []}

    class FakeGoon:
        def price_history_chunked(self, region_id, type_ids):
            calls["goon"] += 1
            # Goonmetrics has TYPE_A only for Oct 3 (an untraded day is 0).
            return [_Point(TYPE_A, date(2026, 10, 3), 50.0), _Point(TYPE_A, date(2026, 10, 2), 0.0)]

    def fake_esi(type_ids):
        calls["esi"].append(sorted(type_ids))
        # ESI knows TYPE_B on Oct 1 only.
        return {TYPE_B: {date(2026, 10, 1): (7.0, "esi")}}

    monkeypatch.setattr(loader, "GoonmetricsClient", FakeGoon)
    monkeypatch.setattr(loader, "_esi_history", fake_esi)
    today = date(2026, 10, 17)
    requests = {(TYPE_A, date(2026, 10, 1)), (TYPE_B, date(2026, 10, 1)), (999, date(2026, 10, 1))}
    found = loader._fetch_reference_prices(requests, today)
    # TYPE_A: no Oct 1 / Oct 2 trade -> nearest traded day (Oct 3, +2 days).
    assert found == {(TYPE_A, date(2026, 10, 1)): 50.0, (TYPE_B, date(2026, 10, 1)): 7.0}
    assert calls["esi"] == [[TYPE_B, 999]]
    prices, misses = loader._cached_prices({TYPE_A, TYPE_B, 999})
    assert prices == found
    assert misses == {(999, date(2026, 10, 1))}   # old enough -> cached as a miss
    cached = ledger_db.list_cached_reference_prices([TYPE_A])
    assert [src for (_t, _d), (_p, src) in cached.items()] == ["goonmetrics_next_day"]
    # A miss for yesterday is never cached (may still be published).
    loader._fetch_reference_prices({(999, date(2026, 10, 16))}, today)
    _prices, misses = loader._cached_prices({999})
    assert (999, date(2026, 10, 16)) not in misses


def test_opening_stock_adds_listed_and_contracted_items_from_shared_owners(tenant, monkeypatch):
    _share(ALICE, "assets")
    _share(ALICE, "market_orders")
    _share(ALICE, "contracts")
    seen = {}

    def fake_stock(type_ids, location_id, **kw):
        seen.update(kw)
        return {TYPE_A: 5.0}

    monkeypatch.setattr(loader.storage, "esi_stock_at_location_bulk", fake_stock)
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_sell_orders (order_id, type_id, location_id, region_id, volume_remain, "
            "character_name, owner_character_id) VALUES (1, ?, ?, 1, 3, 'a', ?), (2, ?, ?, 1, 9, 'b', 999)",
            (TYPE_A, LOC, ALICE, TYPE_A, LOC),
        )
    ledger_db.upsert_ledger_contracts([
        ("character", ALICE, 77, ALICE, None, None, LOC, LOC, "item_exchange", "outstanding",
         None, 1.0, None, T0, None, None),
        ("character", ALICE, 78, ALICE, None, None, LOC, LOC, "item_exchange", "finished",
         None, 1.0, None, T0, T1, None),
    ])
    ledger_db.upsert_ledger_contract_items([
        ("character", ALICE, 77, 1, TYPE_B, 4, True, False),
        ("character", ALICE, 78, 1, TYPE_B, 100, True, False),
    ])
    try:
        stock = loader._opening_stock({TYPE_A, TYPE_B})
    finally:
        with storage.connect() as conn:
            conn.execute("DELETE FROM character_sell_orders WHERE order_id IN (1, 2)")
    # 5 in hangars + 3 listed by ALICE (not the other owner's 9); 4 in the
    # outstanding contract, the finished one is gone.
    assert stock == {TYPE_A: 8.0, TYPE_B: 4.0}
    assert seen == {"owner_character_ids": [ALICE], "owner_corporation_ids": []}


def test_jobs_know_whether_they_ran_on_a_bpc(tenant, monkeypatch):
    _share(ALICE, "wallet")
    _share(ALICE, "industry_jobs")
    _share(ALICE, "blueprints")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 1.0, 1, True, 1, None, None, None),
    ])
    ledger_db.upsert_ledger_blueprints([
        ("character", ALICE, 501, 1000, LOC, 10, 20, -1, -1),   # BPO
        ("character", ALICE, 502, 1000, LOC, 10, 20, 50, -2),   # BPC
    ])
    rows = []
    for job_id, blueprint_id, bp_type in ((1, 501, 1000), (2, 502, 1000), (3, None, 2000), (4, None, 1000)):
        rows.append(("character", ALICE, job_id, 1, blueprint_id, bp_type, 3000, 1, None, None,
                     1.0, None, None, None, ALICE, T1, T1, None, "active", None))
    ledger_db.upsert_ledger_industry_jobs(rows)
    monkeypatch.setattr(loader.db, "invented_blueprint_type_ids", lambda: frozenset({2000}))
    monkeypatch.setattr(loader, "_opening_stock", lambda type_ids: {})
    monkeypatch.setattr(loader, "_tenant_character_ids", lambda: frozenset({ALICE}))
    monkeypatch.setattr(loader, "_relic_type_ids", lambda: frozenset())
    snap = loader.load_ledger_snapshot()
    assert {j.job_id: j.uses_bpc for j in snap.industry_jobs} == {1: False, 2: True, 3: True, 4: None}


def test_bulk_wallet_read_is_one_owner_set(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 1.0, 1, True, 1, None, None, None),
        ("character", ALICE, 0, 2, T1, TYPE_B, LOC, 1.0, 1, True, 2, None, None, None),
    ])
    rows = ledger_access.list_all_wallet_transactions_for_production()
    assert len(rows) == 2
    assert {r[5] for r in rows} == {TYPE_A, TYPE_B}


def test_tenant_characters_include_every_token_character(tenant, monkeypatch):
    """The registry holds only the login character; the tenant's other
    characters come from its tokens (corp jobs by installer, internal
    contracts)."""
    from types import SimpleNamespace

    from eve_trader import auth

    monkeypatch.setattr(storage, "list_tenant_registry_entries",
                        lambda tenant_id: [("character", ALICE)])
    monkeypatch.setattr(auth.TokenManager, "list_records", lambda self: [
        SimpleNamespace(character_id=ALICE, role=f"producer:{ALICE}"),
        SimpleNamespace(character_id=910002, role="producer:910002"),
        SimpleNamespace(character_id=910003, role="buyer:910003"),
    ])
    assert loader._tenant_character_ids() == frozenset({ALICE, 910002, 910003})

