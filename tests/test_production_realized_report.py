"""Phase 3 report: derived-table SQL against Postgres, flags kept, D11 gaps.

Price fetches are stubbed (they are HTTP). Costing, the loader's writes and
every report query run for real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from eve_trader import storage
from eve_trader.production import actions as production_actions
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
BOB = 910002
TYPE_A = 34
TYPE_B = 35
LOC = 60003760
LOC_SELL = 60003761
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
T_JOB = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
T_END = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
T_SALE = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def _apply_production_ledger_schema(_apply_phase1_schema):
    if not pg_helpers._postgres_available():
        return
    ledger_sql = _LEDGER_SCHEMA_SQL.read_text(encoding="utf-8")
    orders_sql = _SPECIAL_ORDERS_SCHEMA_SQL.read_text(encoding="utf-8")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(ledger_sql)
        conn.execute(ledger_sql)
        conn.execute(orders_sql)
        conn.execute(orders_sql)


@pytest.fixture(autouse=True)
def _wipe(_apply_production_ledger_schema):
    pg_helpers.wipe_tables(
        "ledger_wallet_transactions", "ledger_wallet_journal",
        "ledger_industry_jobs", "ledger_job_inputs", "ledger_blueprints",
        "ledger_market_orders", "ledger_contracts", "ledger_contract_items",
        "ledger_archive_gaps", "ledger_sync_state", "ledger_reference_prices",
        "ledger_manual_entries", "prod_cost_layers", "prod_realized_results",
        "prod_cost_draws", "prod_job_records", "prod_fee_lines", "prod_job_losses",
        "prod_capital_bpos", "prod_inventory_drift", "prod_ledger_runs",
        "esi_sharing", "character_assets", "corp_assets", "stock_targets",
    )
    yield


def _share(owner_id: int, data_kind: str):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, 'production') ON CONFLICT DO NOTHING",
            (owner_id, data_kind),
        )


def _rebuild(stock=None):
    # Opening stock and reference-price HTTP are stubbed. Costing, the
    # derived-table writes and every report query still run. Tenant
    # characters come from admin_schema, which these tests do not apply;
    # ALICE is the only installer they insert.
    with patch.object(loader, "_opening_stock", return_value=dict(stock or {})):
        with patch.object(loader, "_fetch_reference_prices", return_value={}):
            with patch.object(loader, "_tenant_character_ids", return_value=frozenset({ALICE})):
                return loader.rebuild_realized()


def _group(report: dict, kind: str) -> dict:
    return next(g for g in report["groups"] if g["kind"] == kind)


def test_summary_before_any_rebuild_asks_for_one(tenant):
    summary = production_actions.do_get_realized_summary(days=30)
    assert summary["status"]["needs_rebuild"] is True
    assert summary["status"]["empty"] is True
    assert summary["realized_profit"] == 0
    assert summary["flags"]["price_missing"] is False
    rebuilt = production_actions.do_rebuild_realized()
    assert rebuilt["cutover_t0"] is None
    summary = production_actions.do_get_realized_summary(days=30)
    assert summary["status"]["needs_rebuild"] is False
    assert "no wallet archive" in summary["status"]["hint"]


def test_chain_keeps_flags_cost_components_and_drift(tenant):
    _share(ALICE, "wallet")
    _share(ALICE, "industry_jobs")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 100.0, 10, True, 101,
         10000002, 50.0, 2.0),
        ("character", ALICE, 0, 2, T_SALE, TYPE_B, LOC_SELL, 500.0, 1, False, 102,
         None, None, None),
    ])
    ledger_db.upsert_ledger_industry_jobs([(
        "character", ALICE, 77, 1, 500, 9000, TYPE_B, 2, None, None,
        40.0, 12345, None, None, ALICE, T_JOB, T_END, T_END, "delivered", None,
    )])
    ledger_db.insert_ledger_job_inputs([(
        "character", ALICE, 77, TYPE_A, 4.0, 10, 0.9, None, None, None, None,
        "esi_blueprint", False,
    )])
    ledger_db.record_archive_gap("character", ALICE, "wallet_transactions", T0, T_SALE)
    ledger_db.record_archive_gap("character", BOB, "wallet_transactions", T0, T_SALE)

    summary_run = _rebuild({TYPE_A: 6, TYPE_B: 1})
    assert summary_run["realized_results"] == 1

    # The new statements actually hit Postgres (not a mocked writer).
    with storage.connect() as conn:
        draws = conn.execute("SELECT COUNT(*) FROM prod_cost_draws").fetchone()[0]
        jobs = conn.execute(
            "SELECT no_structure_bonus, facility_id FROM prod_job_records"
        ).fetchone()
        freight_label_amount = conn.execute(
            "SELECT cost_freight, price_missing, estimated, warnings "
            "FROM prod_realized_results"
        ).fetchone()
    assert draws >= 2
    assert jobs[0] is True
    assert jobs[1] == 12345
    # One of the job's two output units was sold, so the sale carries half
    # of the job's 40 ISK freight (4 units x 10 ISK/unit).
    assert freight_label_amount[0] == pytest.approx(20.0)
    assert freight_label_amount[1] is False
    assert freight_label_amount[2] is False
    assert "tax_missing" in freight_label_amount[3]

    summary = production_actions.do_get_realized_summary(days=30)
    assert summary["revenue"] == pytest.approx(500.0)
    assert summary["cost_of_goods_sold"] == pytest.approx(240.0)
    assert summary["realized_profit"] == pytest.approx(260.0)
    assert summary["coverage"] == pytest.approx(1.0)
    assert summary["estimated"] is False
    assert summary["tax_missing_sales"] == 1
    assert summary["flags"]["tax_missing"] is True
    assert summary["inventory_at_cost"] == pytest.approx(240.0)
    structure = {line["key"]: line for line in summary["cost_structure"]}
    assert structure["freight"]["label"] == "Freight (configured rate)"
    assert structure["freight"]["amount"] == pytest.approx(20.0)
    assert structure["materials_bought"]["amount"] == pytest.approx(200.0)
    assert structure["job"]["amount"] == pytest.approx(20.0)
    assert structure["sales_tax"]["amount"] == pytest.approx(0.0)

    products = production_actions.do_get_realized_products(days=30)
    assert products["rows"][0]["type_id"] == TYPE_B
    assert products["rows"][0]["flags"]["price_missing"] is False
    assert products["rows"][0]["flags"]["tax_missing"] is True
    assert "tax_missing" in products["rows"][0]["warnings"]

    detail = production_actions.do_get_realized_product(TYPE_B, days=30)
    sale_ref = detail["sales"][0]["sale_ref"]
    assert detail["sales"][0]["flags"]["tax_missing"] is True
    assert detail["sales"][0]["location_id"] == LOC_SELL

    chain = production_actions.do_get_realized_sale_chain(sale_ref)
    assert chain["tree"]["kind"] == "product"
    assert chain["tree"]["flags"]["tax_missing"] is True
    [job] = chain["tree"]["children"]
    assert job["kind"] == "job"
    assert job["job_id"] == 77
    assert job["facility_id"] == 12345
    assert job["runs"] == 2
    assert job["flags"]["no_structure_bonus"] is True
    [purchase] = job["children"]
    assert purchase["kind"] == "purchase"
    assert purchase["origin"] == "bought"
    assert purchase["type_id"] == TYPE_A
    assert purchase["quantity"] == pytest.approx(4.0)
    assert purchase["unit_price"] == pytest.approx(100.0)
    assert purchase["freight"] == pytest.approx(40.0)
    assert purchase["freight_label"] == "configured rate"
    assert purchase["location_id"] == LOC
    assert purchase["flags"]["price_missing"] is False

    job_rows = production_actions.do_get_realized_jobs()["rows"]
    assert job_rows[0]["sold_share"] == pytest.approx(0.5)
    assert job_rows[0]["flags"]["no_structure_bonus"] is True
    assert job_rows[0]["input_cost"] == pytest.approx(440.0)

    inventory = {row["type_id"]: row for row in production_actions.do_get_realized_inventory()["rows"]}
    assert inventory[TYPE_B]["quantity"] == pytest.approx(1.0)
    assert inventory[TYPE_B]["drift"] == pytest.approx(0.0)
    assert inventory[TYPE_B]["origin_mix"]["produced"] == pytest.approx(1.0)
    assert inventory[TYPE_A]["origin_mix"]["bought"] == pytest.approx(6.0)

    warnings = production_actions.do_get_realized_warnings()
    assert _group(warnings, "unresolved_region")["items"] == [{"location_id": LOC_SELL, "count": 1}]
    gaps = _group(warnings, "archive_gap")["items"]
    assert len(gaps) == 1
    assert gaps[0]["owner_id"] == ALICE
    assert _group(warnings, "assumed_me")["count"] == 0

    production_actions.do_add_ledger_entry(
        "revenue", 25.0, "2026-10-06T00:00:00+00:00", type_id=TYPE_B, note="manual",
    )
    summary = production_actions.do_get_realized_summary(days=30)
    assert summary["manual_revenues"] == pytest.approx(25.0)
    assert summary["profit_including_manual"] == pytest.approx(285.0)


def test_missing_price_stays_flagged_on_the_report(tenant):
    _share(ALICE, "wallet")
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name, backup_stock) VALUES (?, 'A', 1)",
            (TYPE_A,),
        )
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 9, T_SALE, TYPE_A, LOC, 10.0, 1, False, 109,
         None, None, None),
    ])
    _rebuild()
    summary = production_actions.do_get_realized_summary(since_t0=True)
    assert summary["price_missing"] is True
    assert summary["estimated"] is True
    assert summary["flags"]["estimated"] is True
    assert summary["flags"]["price_missing"] is True
    assert summary["cost_of_goods_sold"] == pytest.approx(0.0)
    sale = production_actions.do_get_realized_product(TYPE_A, since_t0=True)["sales"][0]
    assert sale["flags"]["price_missing"] is True
    assert "price_missing" in sale["warnings"]
    chain = production_actions.do_get_realized_sale_chain(sale["sale_ref"])
    [node] = chain["tree"]["children"]
    # No buy and ESI stock 0: the sale is covered by opening stock
    # (current stock minus net flows), valued at 0 because no reference price.
    assert node["kind"] == "estimate"
    assert node["origin"] == "opening"
    assert node["reference_price"] == pytest.approx(0.0)
    assert node["flags"]["price_missing"] is True


def test_opening_cost_override_and_freight_reapply(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_B, LOC, 1.0, 1, True, 1,
         10000002, 10.0, 3.0),
    ])
    stored = production_actions.do_set_opening_cost(TYPE_A, 12.5)
    assert stored["applied_on_next_rebuild"] is True
    _rebuild({TYPE_A: 5})
    with storage.connect() as conn:
        layer = conn.execute(
            "SELECT origin, unit_cost, cost_materials_bought, estimated_origin, price_missing "
            "FROM prod_cost_layers WHERE type_id = ?",
            (TYPE_A,),
        ).fetchone()
    assert layer[0] == "opening"
    assert layer[1] == pytest.approx(12.5)
    assert layer[2] == pytest.approx(12.5)
    assert layer[3] is False
    assert layer[4] is False

    updated = production_actions.do_reapply_freight_rate()
    assert updated["updated"] == 1
    assert updated["freight_label"] == "configured rate"
    assert updated["rebuild_required"] is True
    with storage.connect() as conn:
        rate = conn.execute(
            "SELECT freight_rate_per_m3 FROM ledger_wallet_transactions"
        ).fetchone()[0]
    assert rate == pytest.approx(updated["freight_rate_per_m3"])


def test_custom_range_and_bad_period_are_action_errors(tenant):
    with pytest.raises(production_actions.ActionError):
        production_actions.do_get_realized_summary(days=30, since_t0=True)
    with pytest.raises(production_actions.ActionError):
        production_actions.do_add_ledger_entry("gift", 1.0, "2026-10-01")
    with pytest.raises(production_actions.ActionError):
        production_actions.do_get_realized_sale_chain("missing")
