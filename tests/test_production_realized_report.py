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
        "ledger_industry_jobs", "ledger_job_inputs", "ledger_job_planned_costs", "ledger_valuation_prices",
        "ledger_blueprints",
        "ledger_market_orders", "ledger_contracts", "ledger_contract_items",
        "ledger_archive_gaps", "ledger_sync_state", "ledger_reference_prices",
        "ledger_manual_entries", "prod_cost_layers", "prod_realized_results",
        "prod_cost_draws", "prod_job_records", "prod_fee_lines", "prod_job_losses",
        "prod_capital_bpos", "prod_inventory_drift", "prod_opening_frozen",
        "prod_opening_lines", "prod_ledger_runs",
        "esi_sharing", "character_assets", "corp_assets", "stock_targets", "character_industry_jobs",
    )
    yield


@pytest.fixture(autouse=True)
def _no_planner_valuation(monkeypatch):
    """The planner valuation needs live market prices; tests that want one
    patch loader._planner_values themselves. Empty = The Forge fallback."""
    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {})


@pytest.fixture(autouse=True)
def _no_esi_names(monkeypatch):
    """Owner names fall back to ESI's public names endpoint; never call it here."""
    from eve_trader.production.realized import names
    monkeypatch.setattr(names, "_esi_names", lambda ids: {})


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
    # One of two output units was sold: half of the job's 4 inputs.
    assert purchase["quantity"] == pytest.approx(2.0)
    assert purchase["unit_price"] == pytest.approx(100.0)
    assert purchase["freight"] == pytest.approx(20.0)
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

    preview = production_actions.do_reapply_freight_rate(dry_run=True)
    updated = production_actions.do_reapply_freight_rate()
    assert updated["updated"] == preview["would_update"]
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


def _seed_job_and_sale():
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


def test_revoked_share_hides_the_run_until_rebuild(tenant):
    _seed_job_and_sale()
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    sale_ref = production_actions.do_get_realized_product(TYPE_B, days=30)["sales"][0]["sale_ref"]
    with storage.connect() as conn:
        conn.execute(
            "DELETE FROM esi_sharing WHERE owner_id = ? AND data_kind = 'industry_jobs'", (ALICE,),
        )

    summary = production_actions.do_get_realized_summary(days=30)
    assert summary["status"]["sharing_changed"] is True
    assert summary["status"]["needs_rebuild"] is True
    assert summary["revenue"] == 0
    assert production_actions.do_get_realized_products(days=30)["rows"] == []
    assert production_actions.do_get_realized_product(TYPE_B, days=30)["sales"] == []
    assert production_actions.do_get_realized_jobs()["rows"] == []
    assert production_actions.do_get_realized_inventory()["rows"] == []
    assert production_actions.do_get_realized_opening()["rows"] == []
    assert production_actions.do_get_realized_warnings()["needs_rebuild"] is True
    with pytest.raises(production_actions.ActionError):
        production_actions.do_get_realized_sale_chain(sale_ref)

    # Rebuilt without the job: TYPE_B is no longer a Production type.
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    summary = production_actions.do_get_realized_summary(days=30)
    assert summary["status"]["sharing_changed"] is False
    assert summary["revenue"] == pytest.approx(0.0)

    # Sharing again is a change too; the next rebuild brings the sale back.
    _share(ALICE, "industry_jobs")
    assert production_actions.do_get_realized_summary(days=30)["status"]["sharing_changed"] is True
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    assert production_actions.do_get_realized_summary(days=30)["revenue"] == pytest.approx(500.0)


def test_chain_scales_job_inputs_to_the_sold_share(tenant):
    _seed_job_and_sale()
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    sale_ref = production_actions.do_get_realized_product(TYPE_B, days=30)["sales"][0]["sale_ref"]
    chain = production_actions.do_get_realized_sale_chain(sale_ref)
    [job] = chain["tree"]["children"]
    # 1 of the job's 2 output units was sold: half of its inputs and costs.
    assert job["quantity"] == pytest.approx(1.0)
    assert job["share"] == pytest.approx(0.5)
    assert job["cost_share"] == pytest.approx(chain["sale"]["cost"])
    [purchase] = job["children"]
    assert purchase["quantity"] == pytest.approx(2.0)
    assert purchase["freight"] == pytest.approx(20.0)


def test_opening_is_frozen_so_a_later_loss_shows_as_drift(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 100.0, 10, True, 101,
         None, None, None),
    ])
    first = _rebuild({TYPE_A: 15})
    assert first["opening_frozen"] is False
    [line] = production_actions.do_get_realized_opening()["rows"]
    assert line["type_id"] == TYPE_A
    assert line["quantity"] == pytest.approx(5.0)
    assert line["frozen_at"] is not None

    # 3 units disappear without an ESI event: the opening stays 5, the
    # ledger still holds 15 and the gap is drift, not a smaller opening.
    second = _rebuild({TYPE_A: 12})
    assert second["opening_frozen"] is True
    [line] = production_actions.do_get_realized_opening()["rows"]
    assert line["quantity"] == pytest.approx(5.0)
    inventory = {row["type_id"]: row for row in production_actions.do_get_realized_inventory()["rows"]}
    assert inventory[TYPE_A]["quantity"] == pytest.approx(15.0)
    assert inventory[TYPE_A]["drift"] == pytest.approx(-3.0)
    drift = _group(production_actions.do_get_realized_warnings(), "inventory_drift")["items"]
    assert drift == [{"type_id": TYPE_A, "ledger_qty": 15.0, "esi_qty": 12.0, "drift": -3.0}]

    # An explicit reset derives the opening again from today's stock.
    with patch.object(loader, "_opening_stock", return_value={TYPE_A: 12}):
        with patch.object(loader, "_fetch_reference_prices", return_value={}):
            with patch.object(loader, "_tenant_character_ids", return_value=frozenset({ALICE})):
                production_actions.do_rebuild_realized(reset_opening=True)
    [line] = production_actions.do_get_realized_opening()["rows"]
    assert line["quantity"] == pytest.approx(2.0)
    inventory = {row["type_id"]: row for row in production_actions.do_get_realized_inventory()["rows"]}
    assert inventory[TYPE_A]["drift"] == pytest.approx(0.0)


def test_status_is_stale_after_a_newer_archive_sync(tenant):
    _seed_job_and_sale()
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    assert production_actions.do_get_realized_summary(days=30)["status"]["stale"] is False
    ledger_db.mark_archive_synced(
        "character", ALICE, "wallet_transactions", datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert production_actions.do_get_realized_summary(days=30)["status"]["stale"] is True


def test_ledger_tab_reads_entries_freight_preview_and_archive_status(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_B, LOC, 1.0, 1, True, 1,
         10000002, 10.0, 3.0),
    ])
    ledger_db.mark_archive_synced("character", ALICE, "wallet_transactions", T_SALE)
    ledger_db.mark_archive_synced("character", BOB, "industry_jobs", T_SALE)
    ledger_db.record_archive_gap("character", ALICE, "wallet_transactions", T0, T_SALE)

    entry = production_actions.do_add_ledger_entry("cost", 7.0, "2026-10-05", note="hauling")
    production_actions.do_set_opening_cost(TYPE_A, 3.0)
    kinds = sorted(row["entry_kind"] for row in production_actions.do_list_ledger_entries()["rows"])
    assert kinds == ["cost", "opening_cost"]
    production_actions.do_delete_ledger_entry(entry["entry_id"])
    kinds = [row["entry_kind"] for row in production_actions.do_list_ledger_entries()["rows"]]
    assert kinds == ["opening_cost"]
    with pytest.raises(production_actions.ActionError):
        production_actions.do_delete_ledger_entry(entry["entry_id"])

    preview = production_actions.do_reapply_freight_rate(dry_run=True)
    expected = 0 if preview["freight_rate_per_m3"] == pytest.approx(10.0) else 1
    assert preview["would_update"] == expected
    with storage.connect() as conn:
        rate = conn.execute("SELECT freight_rate_per_m3 FROM ledger_wallet_transactions").fetchone()[0]
    assert rate == pytest.approx(10.0)
    assert production_actions.do_reapply_freight_rate()["updated"] == expected
    assert production_actions.do_reapply_freight_rate(dry_run=True)["would_update"] == 0

    rows = {row["owner_id"]: row for row in production_actions.do_get_realized_archive_status()["rows"]}
    assert rows[ALICE]["shared"]["wallet"] is True
    assert rows[ALICE]["hidden_kinds"] == []
    assert rows[ALICE]["gaps"] == {"wallet_transactions": 1}
    assert rows[ALICE]["last_sync"]["wallet_transactions"] is not None
    # BOB was archived but shares nothing with Production any more.
    assert rows[BOB]["shared"]["industry_jobs"] is False
    assert rows[BOB]["hidden_kinds"] == ["industry_jobs"]


def test_null_costing_version_resets_opening_once(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 100.0, 10, True, 101,
         None, None, None),
    ])
    first = _rebuild({TYPE_A: 15})
    assert first["opening_frozen"] is False
    assert production_actions.do_get_realized_opening()["rows"][0]["quantity"] == pytest.approx(5.0)
    with storage.connect() as conn:
        conn.execute("UPDATE prod_ledger_runs SET costing_version = NULL")

    # Stock dropped, but the stored version is from before Phase 5, so this
    # rebuild re-derives the opening instead of keeping 5.
    second = _rebuild({TYPE_A: 12})
    assert second["opening_frozen"] is False
    assert production_actions.do_get_realized_opening()["rows"][0]["quantity"] == pytest.approx(2.0)

    third = _rebuild({TYPE_A: 9})
    assert third["opening_frozen"] is True
    assert production_actions.do_get_realized_opening()["rows"][0]["quantity"] == pytest.approx(2.0)


def test_contract_buy_and_sale_reach_the_report(tenant):
    _share(ALICE, "wallet")
    _share(ALICE, "contracts")
    storage.upsert_stock_target(TYPE_A, "Trit")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, 1, LOC, 1.0, 1, True, 1, None, None, None),
    ])
    # Acceptor buys 4; issuer later sells 2 of the same type.
    ledger_db.upsert_ledger_contracts([
        ("character", ALICE, 501, 999, None, ALICE, None, None,
         "item_exchange", "finished", "buy", 40.0, 0.0, T0, T_JOB, None),
        ("character", ALICE, 502, ALICE, None, 999, None, None,
         "item_exchange", "finished", "sell", 80.0, 0.0, T0, T_SALE, None),
        ("character", ALICE, 503, ALICE, None, ALICE, None, None,
         "item_exchange", "finished", "internal", 999.0, 0.0, T0, T_SALE, None),
    ])
    ledger_db.upsert_ledger_contract_items([
        ("character", ALICE, 501, 1, TYPE_A, 4, True, False),
        ("character", ALICE, 502, 1, TYPE_A, 2, True, False),
        ("character", ALICE, 503, 1, TYPE_A, 9, True, False),
    ])
    _rebuild({})
    sales = production_actions.do_get_realized_product(TYPE_A, days=30)["sales"]
    assert len(sales) == 1
    assert sales[0]["source"] == "contract"
    assert sales[0]["revenue"] == pytest.approx(80.0)
    assert sales[0]["sales_tax"] is None
    assert "tax_missing" not in sales[0]["warnings"]
    chain = production_actions.do_get_realized_sale_chain(sales[0]["sale_ref"])
    [purchase] = chain["tree"]["children"]
    assert purchase["source"] == "contract"
    assert purchase["origin"] == "bought"
    inventory = {row["type_id"]: row for row in production_actions.do_get_realized_inventory()["rows"]}
    assert inventory[TYPE_A]["quantity"] == pytest.approx(2.0)
    summary = production_actions.do_get_realized_summary(days=30)
    assert summary["tax_missing_sales"] == 0
    assert summary["flags"]["tax_missing"] is False


def test_planned_cost_survives_rebuild_and_weights_sold_units(tenant):
    _seed_job_and_sale()
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    [row] = production_actions.do_get_realized_products(days=30)["rows"]
    assert row["type_id"] == TYPE_B
    assert row["planned_cost_per_unit"] is None
    assert row["planned_coverage"] == pytest.approx(0.0)

    ledger_db.insert_planned_costs([
        ("character", ALICE, 77, TYPE_B, 12.5, "discover_cache"),
    ])
    [row] = production_actions.do_get_realized_products(days=30)["rows"]
    assert row["planned_cost_per_unit"] == pytest.approx(12.5)
    assert row["planned_coverage"] == pytest.approx(1.0)
    # Every sold unit has a planned value, so the difference is per unit.
    assert row["planned_difference_per_unit"] == pytest.approx(row["cost_per_unit"] - 12.5)

    _rebuild({TYPE_A: 6, TYPE_B: 1})
    [row] = production_actions.do_get_realized_products(days=30)["rows"]
    assert row["planned_cost_per_unit"] == pytest.approx(12.5)


def test_courier_comparison_is_forge_to_home_only(tenant, monkeypatch):
    from eve_trader.production.config import PRODUCTION_CONFIG

    _share(ALICE, "contracts")
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_location_id", 42)
    monkeypatch.setattr(PRODUCTION_CONFIG, "hub_region_id", 10000002)
    monkeypatch.setattr(PRODUCTION_CONFIG, "haul_cost_per_m3", 80.0)
    ledger_db.upsert_ledger_contracts([
        ("character", ALICE, 11, ALICE, None, 5, 60003760, 42,
         "courier", "finished", "jita", 0, 500.0, T0, T_SALE, None,
         10.0, 1000.0, 10000002),
        ("character", ALICE, 12, ALICE, None, 5, 1, 42,
         "courier", "finished", "elsewhere", 0, 900.0, T0, T_SALE, None,
         10.0, 1.0, 10000043),
        ("character", ALICE, 13, ALICE, None, 5, 60003760, 42,
         "courier", "outstanding", "open", 0, 50.0, T0, None, None,
         1.0, 1.0, 10000002),
    ])
    report = production_actions.do_get_realized_courier()
    assert report["home_location_id"] == 42
    assert report["configured_rate_per_m3"] == pytest.approx(80.0)
    assert report["months"] == [{
        "month": "2026-10",
        "count": 1,
        "volume_m3": 10.0,
        "reward": 500.0,
        "paid_isk_per_m3": 50.0,
        "configured_rate_per_m3": 80.0,
    }]


def test_hidden_archive_delete_refuses_while_shared(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 1.0, 1, True, 1, None, None, None),
    ])
    ledger_db.mark_archive_synced("character", BOB, "industry_jobs", T0)
    ledger_db.upsert_ledger_industry_jobs([(
        "character", BOB, 3, 1, 1, 1, TYPE_B, 1, None, None,
        1.0, None, None, None, BOB, T_JOB, T_END, T_END, "delivered", None,
    )])
    ledger_db.record_archive_gap("character", BOB, "industry_jobs", T0, T_SALE)

    with pytest.raises(production_actions.ActionError):
        production_actions.do_count_hidden_archive_rows("character", ALICE, "wallet")
    counted = production_actions.do_count_hidden_archive_rows("character", BOB, "industry_jobs")
    assert counted["count"] == 1
    deleted = production_actions.do_delete_hidden_archive_rows("character", BOB, "industry_jobs")
    assert deleted["deleted"] == 1
    assert production_actions.do_count_hidden_archive_rows("character", BOB, "industry_jobs")["count"] == 0
    with storage.connect() as conn:
        gaps = conn.execute(
            "SELECT COUNT(*) FROM ledger_archive_gaps WHERE owner_id = ?", (BOB,),
        ).fetchone()[0]
        syncs = conn.execute(
            "SELECT COUNT(*) FROM ledger_sync_state WHERE owner_id = ?", (BOB,),
        ).fetchone()[0]
    assert gaps == 0
    assert syncs == 0


def test_opening_is_valued_at_the_planner_price(tenant, monkeypatch):
    """D5/D8: stock without a purchase is valued at what the planner would
    pay (cheaper of buying and building), not The Forge daily average."""
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 100.0, 10, True, 101,
         None, None, None),
    ])
    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {TYPE_A: (7.0, "buy_home")})
    _rebuild({TYPE_A: 15})
    [line] = production_actions.do_get_realized_opening()["rows"]
    assert line["type_id"] == TYPE_A
    assert line["quantity"] == pytest.approx(5.0)
    assert line["unit_cost"] == pytest.approx(7.0)
    assert line["reference_price"] == pytest.approx(7.0)
    assert line["valued_as"] == "buy_home"


def test_owner_names_come_from_stored_data_before_esi(tenant, monkeypatch):
    """Corp names from corp_assets, job installers from synced jobs; ESI is
    asked only for ids found nowhere."""
    from eve_trader.production.realized import names

    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO corp_assets (item_id, type_id, location_id, quantity, is_blueprint_copy, "
            "owner_name, owner_corporation_id) VALUES (1, 34, ?, 1, 0, 'RichlTech (corp)', 98723738)",
            (LOC,),
        )
        conn.execute(
            "INSERT INTO character_industry_jobs (job_id, installer_id, installer_name) "
            "VALUES (5, 2112543378, 'Corpmate')",
        )
    asked = []
    monkeypatch.setattr(names, "_esi_names", lambda ids: asked.append(list(ids)) or {424242: "Stranger"})
    found = names.owner_names([98723738, 2112543378, 424242])
    assert found == {98723738: "RichlTech", 2112543378: "Corpmate", 424242: "Stranger"}
    assert asked == [[424242]]


def test_jobs_filter_by_state_and_period_and_group_per_product(tenant):
    _seed_job_and_sale()
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    all_jobs = production_actions.do_get_realized_jobs()
    assert len(all_jobs["rows"]) == 1
    assert production_actions.do_get_realized_jobs(state="running")["rows"] == []
    assert len(production_actions.do_get_realized_jobs(state="finished")["rows"]) == 1
    # The job ended at T_END: a period after it lists nothing finished.
    after = (T_END + timedelta(days=1)).date().isoformat()
    later = (T_END + timedelta(days=2)).date().isoformat()
    assert production_actions.do_get_realized_jobs(start=after, end=later, state="finished")["rows"] == []
    with pytest.raises(production_actions.ActionError):
        production_actions.do_get_realized_jobs(state="done")

    [group] = all_jobs["groups"]
    row = all_jobs["rows"][0]
    assert group["type_id"] == TYPE_B and group["jobs"] == 1
    assert group["output_quantity"] == pytest.approx(2.0)
    assert group["total_cost"] == pytest.approx(row["input_cost"] + row["job_cost"])
    assert group["unit_cost"] == pytest.approx(group["total_cost"] / 2.0)
    assert group["sold_share"] == pytest.approx(0.5)
    assert group["planned_unit_cost"] is None
    assert all_jobs["totals"]["jobs"] == 1
    assert all_jobs["totals"]["total_cost"] == pytest.approx(group["total_cost"])

    ledger_db.insert_planned_costs([("character", ALICE, 77, TYPE_B, 12.5, "discover_cache")])
    listed = production_actions.do_get_realized_jobs()
    assert listed["rows"][0]["planned_unit_cost"] == pytest.approx(12.5)
    [group] = listed["groups"]
    assert group["planned_unit_cost"] == pytest.approx(12.5)
    assert group["planned_difference_per_unit"] == pytest.approx(group["unit_cost"] - 12.5)


def test_invention_group_cost_is_per_bpc_run_produced():
    from eve_trader.production.realized import report

    def row(state, cost, output):
        return {
            "activity_id": 8, "product_type_id": 999, "blueprint_type_id": 998,
            "product_name": "T2 BPC", "blueprint_name": None, "output_unit": "bpc_run",
            "runs": 1, "successful_runs": 1 if state == "produced" else 0, "state": state,
            "input_cost": cost, "job_cost": 0.0, "output_quantity": output,
            "output_quantity_left": output, "planned_unit_cost": None,
            "flags": {"estimated": False, "assumed_me": False, "assumed_decryptor": False},
        }

    # Two attempts at 100 each; one succeeded with a 10-run copy.
    [group] = report._job_groups([row("produced", 100.0, 10.0), row("failed", 100.0, 0.0)])
    assert group["failed"] == 1
    assert group["cost_per_bpc_run"] == pytest.approx(20.0)


def test_summary_spending_shows_job_fees_apart_from_cost_of_goods_sold(tenant):
    _seed_job_and_sale()
    _rebuild({TYPE_A: 6, TYPE_B: 1})
    summary = production_actions.do_get_realized_summary(days=30)
    spending = summary["spending"]
    [fees] = spending["job_fees"]
    assert fees["label"] == "Manufacturing" and fees["jobs"] == 1
    assert fees["amount"] == pytest.approx(40.0)
    assert spending["job_fees_total"] == pytest.approx(40.0)
    assert spending["invention_total"] == pytest.approx(0.0)
    # One of the two produced units is still in stock, at half the job's cost.
    [job] = production_actions.do_get_realized_jobs()["rows"]
    assert spending["produced_in_stock"] == pytest.approx((job["input_cost"] + job["job_cost"]) / 2)
    assert spending["running_jobs"] == 0 and spending["in_running_jobs"] == pytest.approx(0.0)
    labels = {line["key"]: line["label"] for line in summary["cost_structure"]}
    assert labels["materials_estimated"] == "Opening / unknown stock (planner value)"

