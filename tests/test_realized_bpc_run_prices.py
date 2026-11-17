"""Blueprint-copy runs without a cost layer are valued at the per-run
copy/invention cost (Total Wealth's `portfolio.bpc_run_values_with_status`),
frozen per (blueprint type, day) in `ledger_bpc_run_prices`."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from eve_trader import storage
from eve_trader.realized import db as ledger_db
from eve_trader.realized import loader

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 931001
MINERAL = 34
PRODUCT = 931500
BLUEPRINT = 931501
LOC = 60003760
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

TABLES = (
    "ledger_wallet_transactions", "ledger_wallet_journal", "ledger_industry_jobs",
    "ledger_job_inputs", "ledger_job_planned_costs", "ledger_valuation_prices",
    "ledger_bpc_run_prices", "ledger_blueprints", "ledger_market_orders", "ledger_contracts",
    "ledger_contract_items", "ledger_archive_gaps", "ledger_sync_state", "ledger_reference_prices",
    "prod_cost_layers", "prod_realized_results", "prod_cost_draws", "prod_job_records",
    "prod_fee_lines", "prod_job_losses", "prod_capital_bpos", "prod_inventory_drift",
    "prod_opening_frozen", "prod_opening_lines", "prod_ledger_runs",
    "esi_sharing", "esi_character_capabilities", "esi_removed_characters", "tenant_tokens",
)


@pytest.fixture(autouse=True)
def _wipe(_apply_phase1_schema):
    pg_helpers.wipe_tables(*TABLES)
    yield
    pg_helpers.wipe_tables(*TABLES)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    from eve_trader.realized import names

    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {})
    monkeypatch.setattr(names, "_esi_names", lambda ids: {})
    monkeypatch.setattr(storage, "list_tenant_registry_entries",
                        lambda tenant_id: [("character", ALICE)])
    monkeypatch.setattr(loader, "_bpc_run_value_source", lambda type_id: "copy")
    # The job ran on a copy that has no layer (copied before T0).
    monkeypatch.setattr(loader, "_uses_bpc", lambda *args: True)


def _seed():
    for kind in ("wallet", "industry_jobs"):
        storage.upsert_esi_sharing("character", ALICE, kind, "production")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, MINERAL, LOC, 10.0, 5, True, 101, None, None, None),
    ])
    ledger_db.upsert_ledger_industry_jobs([
        ("character", ALICE, 77, 1, 555, BLUEPRINT, PRODUCT, 5, None, None,
         50.0, LOC, LOC, LOC, ALICE, T1, T1, T1, "delivered", None),
    ])


def _rebuild(values, complete=True):
    calls = []

    def fake(type_ids):
        calls.append(set(type_ids))
        return {tid: values.get(tid) for tid in type_ids}, complete

    with patch.object(loader, "_opening_stock", return_value={}), \
            patch.object(loader, "_fetch_reference_prices", return_value={}), \
            patch.object(loader, "_production_type_ids", return_value=frozenset({PRODUCT})), \
            patch.object(loader, "_relic_type_ids", return_value=frozenset()), \
            patch.object(loader, "_bpc_run_values_with_status", side_effect=fake):
        summary = loader.rebuild_realized()
    return summary, calls


def _product_layer():
    """(unit_cost, cost_materials_estimated, price_missing) of the built units.
    The fully drawn copy-run layer itself is not stored."""
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT unit_cost, cost_materials_estimated, price_missing FROM prod_cost_layers "
            "WHERE unit = 'item' AND type_id = ?", (PRODUCT,),
        ).fetchone()
    return float(row[0]), float(row[1]), bool(row[2])


def test_bpc_job_without_layer_costs_frozen_run_value(tenant):
    _seed()
    summary, calls = _rebuild({BLUEPRINT: 20.0})
    assert calls == [{BLUEPRINT}]
    # 5 runs x 20 (estimated) + job 50 = 150 for 5 units.
    assert _product_layer() == (pytest.approx(30.0), pytest.approx(20.0), False)
    assert "bpc_run_estimated: type 931501 5 runs valued at copy/invention cost" in summary["warnings"]
    assert not any(w.startswith("missing_reference_price") for w in summary["warnings"])
    assert ledger_db.list_bpc_run_prices() == {(BLUEPRINT, T1.date()): (20.0, "copy")}

    # The second rebuild reads the frozen value, even though the per-run
    # value has changed since, and asks for nothing.
    _summary, calls = _rebuild({BLUEPRINT: 99.0})
    assert calls == []
    assert _product_layer() == (pytest.approx(30.0), pytest.approx(20.0), False)


def test_bpc_without_a_run_value_is_frozen_unavailable_and_stays_price_missing(tenant):
    _seed()
    summary, _calls = _rebuild({})
    assert _product_layer() == (pytest.approx(10.0), 0.0, True)
    assert not any(w.startswith("bpc_run_estimated") for w in summary["warnings"])
    assert ledger_db.list_bpc_run_prices() == {(BLUEPRINT, T1.date()): (None, "unavailable")}
    _summary, calls = _rebuild({BLUEPRINT: 20.0})
    assert calls == []
    assert _product_layer()[2]


def test_incomplete_valuation_freezes_nothing_and_retries(tenant):
    _seed()
    _summary, calls = _rebuild({BLUEPRINT: 20.0}, complete=False)
    assert calls == [{BLUEPRINT}]
    assert ledger_db.list_bpc_run_prices() == {}
    assert _product_layer()[2]

    _summary, calls = _rebuild({BLUEPRINT: 20.0})
    assert calls == [{BLUEPRINT}]
    assert ledger_db.list_bpc_run_prices() == {(BLUEPRINT, T1.date()): (20.0, "copy")}
    assert _product_layer() == (pytest.approx(30.0), pytest.approx(20.0), False)


def test_valuation_error_does_not_fail_the_rebuild(tenant):
    _seed()

    def boom(type_ids):
        raise RuntimeError("quotes down")

    with patch.object(loader, "_opening_stock", return_value={}),             patch.object(loader, "_fetch_reference_prices", return_value={}),             patch.object(loader, "_production_type_ids", return_value=frozenset({PRODUCT})),             patch.object(loader, "_relic_type_ids", return_value=frozenset()),             patch.object(loader, "_bpc_run_values_with_status", side_effect=boom):
        loader.rebuild_realized()
    assert ledger_db.list_bpc_run_prices() == {}
    assert _product_layer()[2]
