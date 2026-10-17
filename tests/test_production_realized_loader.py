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

_LEDGER_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "production_ledger_schema.sql"

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


@pytest.fixture(autouse=True)
def _wipe(_apply_production_ledger_schema):
    pg_helpers.wipe_tables(
        "ledger_wallet_transactions", "ledger_wallet_journal",
        "ledger_industry_jobs", "ledger_job_inputs", "ledger_blueprints",
        "ledger_market_orders", "ledger_contracts", "ledger_contract_items",
        "ledger_archive_gaps", "ledger_sync_state", "ledger_reference_prices",
        "ledger_manual_entries", "prod_cost_layers", "prod_realized_results",
        "prod_ledger_runs", "esi_sharing", "character_assets", "corp_assets",
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

    with patch.object(loader, "_esi_stock_excluding_blueprints", return_value={}):
        with patch.object(loader, "_ensure_reference_prices", side_effect=lambda needed: {
            (tid, d): 90.0 for tid, d in needed
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


def test_bulk_wallet_read_is_one_owner_set(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 1.0, 1, True, 1, None, None, None),
        ("character", ALICE, 0, 2, T1, TYPE_B, LOC, 1.0, 1, True, 2, None, None, None),
    ])
    rows = ledger_access.list_all_wallet_transactions_for_production()
    assert len(rows) == 2
    assert {r[5] for r in rows} == {TYPE_A, TYPE_B}
