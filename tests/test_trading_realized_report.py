"""Trading report against Postgres: channel filter and the D11 owner scope."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from eve_trader import storage
from eve_trader.production import actions as production_actions
from eve_trader.realized import db as ledger_db
from eve_trader.realized import loader
from eve_trader.trade_realized import actions as trade_actions

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

_DOCS = Path(__file__).resolve().parent.parent / "docs"


@pytest.fixture(scope="session")
def _apply_production_ledger_schema(_apply_phase1_schema):
    if not pg_helpers._postgres_available():
        return
    ledger_sql = (_DOCS / "production_ledger_schema.sql").read_text(encoding="utf-8")
    orders_sql = (_DOCS / "special_orders_schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(ledger_sql)
        conn.execute(ledger_sql)
        conn.execute(orders_sql)
        conn.execute(orders_sql)


@pytest.fixture(autouse=True)
def _wipe(_apply_production_ledger_schema):
    pg_helpers.wipe_tables(
        "ledger_wallet_transactions", "ledger_wallet_journal",
        "ledger_industry_jobs", "ledger_job_inputs", "ledger_job_planned_costs",
        "ledger_valuation_prices", "ledger_blueprints", "ledger_market_orders",
        "ledger_contracts", "ledger_contract_items", "ledger_archive_gaps",
        "ledger_sync_state", "ledger_reference_prices", "ledger_manual_entries",
        "prod_cost_layers", "prod_realized_results", "prod_cost_draws",
        "prod_job_records", "prod_fee_lines", "prod_job_losses", "prod_capital_bpos",
        "prod_inventory_drift", "prod_opening_frozen", "prod_opening_lines",
        "prod_ledger_runs", "esi_sharing", "stock_targets", "realized_trades",
    )
    yield


@pytest.fixture(autouse=True)
def _no_remote(monkeypatch):
    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {})
    from eve_trader.realized import names
    monkeypatch.setattr(names, "_esi_names", lambda ids: {})


ALICE = 910001
BOB = 910002
CAROL = 910003
WIDGET = 88001
STATION_WIDGET = 88002
CONTRACT_WIDGET = 88003
HOME = 60000042
JITA = 60003760
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
T_SALE = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _share(owner_id: int, data_kind: str, tool_key: str):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (owner_id, data_kind, tool_key),
        )


def _rebuild():
    with patch.object(loader, "_opening_stock", return_value={}):
        with patch.object(loader, "_fetch_reference_prices", return_value={}):
            with patch.object(loader, "_tenant_character_ids",
                              return_value=frozenset({ALICE, BOB, CAROL})):
                return loader.rebuild_realized()


def _channels() -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT channel, type_id, owner_id, revenue, cost "
            "FROM prod_realized_results ORDER BY type_id",
        ).fetchall()


def test_trading_report_keeps_only_trading_wallet_owners(tenant):
    _share(ALICE, "wallet", "trading")
    _share(BOB, "wallet", "station_trading")
    _share(CAROL, "contracts", "doctrine")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, WIDGET, HOME, 10.0, 2, True, 1,
         10000002, None, None),
        ("character", ALICE, 0, 2, T_SALE, WIDGET, HOME, 40.0, 1, False, 2,
         10000002, None, None),
        ("character", BOB, 0, 3, T0, STATION_WIDGET, JITA, 10.0, 2, True, 3,
         10000002, None, None),
        ("character", BOB, 0, 4, T_SALE, STATION_WIDGET, JITA, 40.0, 1, False, 4,
         10000002, None, None),
    ])
    ledger_db.upsert_ledger_contracts([
        ("character", CAROL, 70, CAROL, None, 999, HOME, None,
         "item_exchange", "finished", "sell", 50.0, 0.0, T0, T_SALE, None),
    ])
    ledger_db.upsert_ledger_contract_items([
        ("character", CAROL, 70, 1, CONTRACT_WIDGET, 1, True, False),
    ])

    _rebuild()
    channels = {int(row[1]): row[0] for row in _channels()}
    assert channels[WIDGET] == "trading"
    assert channels[STATION_WIDGET] == "station_trading"
    assert channels[CONTRACT_WIDGET] == "trading"

    summary = trade_actions.do_get_trading_realized_summary(days=30)
    assert summary["status"]["needs_rebuild"] is False
    assert summary["revenue"] == pytest.approx(40.0)
    assert summary["realized_profit"] == pytest.approx(30.0)
    assert summary["units_sold"] == pytest.approx(1.0)
    assert summary["margin"] == pytest.approx(30.0 / 40.0)

    rows = trade_actions.do_get_trading_realized_items(days=30)["rows"]
    assert [row["type_id"] for row in rows] == [WIDGET]
    assert rows[0]["avg_buy_landed"] == pytest.approx(10.0)
    assert rows[0]["avg_net_sell"] == pytest.approx(40.0)

    detail = trade_actions.do_get_trading_realized_item(WIDGET, days=30)
    assert len(detail["sales"]) == 1
    sale = trade_actions.do_get_trading_realized_sale(detail["sales"][0]["sale_ref"])
    assert sale["lots"]
    assert sale["lots"][0]["unit_price"] == pytest.approx(10.0)
    assert sale["sale"]["profit"] == pytest.approx(30.0)

    production = production_actions.do_get_realized_summary(days=30)
    assert production["realized_profit"] == pytest.approx(0.0)
    assert production["revenue"] == pytest.approx(0.0)

    inventory = trade_actions.do_get_trading_realized_inventory()
    assert [row["type_id"] for row in inventory["rows"]] == [WIDGET]
    assert inventory["rows"][0]["quantity"] == pytest.approx(1.0)
    assert inventory["rows"][0]["pool"] == "global"


def test_revoking_the_trading_share_hides_the_report(tenant):
    _share(ALICE, "wallet", "trading")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, WIDGET, HOME, 10.0, 1, True, 1, None, None, None),
        ("character", ALICE, 0, 2, T_SALE, WIDGET, HOME, 40.0, 1, False, 2, None, None, None),
    ])
    _rebuild()
    assert trade_actions.do_get_trading_realized_summary(days=30)["realized_profit"] == pytest.approx(30.0)

    with storage.connect() as conn:
        conn.execute("DELETE FROM esi_sharing")
    hidden = trade_actions.do_get_trading_realized_summary(days=30)
    assert hidden["status"]["needs_rebuild"] is True
    assert hidden["status"]["sharing_changed"] is True
    assert hidden["realized_profit"] == 0.0
    assert trade_actions.do_get_trading_realized_items(days=30)["rows"] == []


def test_compare_trading_reads_both_sides_without_writing(tenant):
    _share(ALICE, "wallet", "trading")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, WIDGET, HOME, 10.0, 1, True, 1, None, None, None),
        ("character", ALICE, 0, 2, T_SALE, WIDGET, HOME, 40.0, 1, False, 2, None, None, None),
    ])
    _rebuild()
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO realized_trades (run_ts, type_id, item, sell_date, matched_qty, realized_profit) "
            "VALUES ('run', ?, 'Widget', ?, 1, 12)",
            (WIDGET, T_SALE.isoformat()),
        )
        before = conn.execute("SELECT COUNT(*) FROM realized_trades").fetchone()[0]
        results_before = conn.execute("SELECT COUNT(*) FROM prod_realized_results").fetchone()[0]

    compared = trade_actions.do_compare_trading(30)
    assert compared["needs_rebuild"] is False
    assert compared["old_profit"] == pytest.approx(12.0)
    assert compared["new_profit"] == pytest.approx(30.0)
    match = next(row for row in compared["rows"] if row["type_id"] == WIDGET)
    assert match["old_profit"] == pytest.approx(12.0)
    assert match["new_profit"] == pytest.approx(30.0)

    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM realized_trades").fetchone()[0] == before
        assert conn.execute("SELECT COUNT(*) FROM prod_realized_results").fetchone()[0] == results_before
