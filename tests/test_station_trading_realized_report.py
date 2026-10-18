"""Station Trading report against Postgres: station pool, D11 scope, turnover."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from eve_trader import storage
from eve_trader.production import actions as production_actions
from eve_trader.realized import db as ledger_db
from eve_trader.realized import loader
from eve_trader.station_trading import realized as station_realized
from eve_trader.trade_realized import actions as trade_actions

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

_DOCS = Path(__file__).resolve().parent.parent / "docs"

ALICE = 910001
BOB = 910002
CAROL = 910003
WIDGET = 88015
HULL = 88012
HOME_TYPE = 88016
BOB_TYPE = 88014
HOME = 60000042
JITA = 60003760
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
T_SALE = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def _apply_production_ledger_schema(_apply_phase1_schema, _apply_phase2_schema, _apply_esi_access_schema):
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
        "prod_ledger_runs", "esi_sharing", "stock_targets",
    )
    yield


@pytest.fixture(autouse=True)
def _no_remote(monkeypatch):
    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {})
    from eve_trader.realized import names
    monkeypatch.setattr(names, "_esi_names", lambda ids: {})


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


def _seed():
    _share(ALICE, "wallet", "production")
    _share(BOB, "wallet", "trading")
    _share(CAROL, "wallet", "station_trading")
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name, backup_stock) VALUES (?, 'Hull', 1)",
            (HULL,),
        )
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", CAROL, 0, 1, T0, WIDGET, JITA, 5.0, 2, True, 1, None, None, None),
        ("character", CAROL, 0, 2, T_SALE, WIDGET, JITA, 12.0, 1, False, 2, None, None, None),
        ("character", CAROL, 0, 3, T0, HULL, JITA, 20.0, 1, True, 3, None, None, None),
        ("character", CAROL, 0, 4, T_SALE, HULL, JITA, 30.0, 1, False, 4, None, None, None),
        ("character", CAROL, 0, 5, T0, HOME_TYPE, HOME, 4.0, 1, True, 5, None, None, None),
        ("character", CAROL, 0, 6, T_SALE, HOME_TYPE, HOME, 9.0, 1, False, 6, None, None, None),
        ("character", BOB, 0, 7, T0, BOB_TYPE, JITA, 3.0, 1, True, 7, None, None, None),
        ("character", BOB, 0, 8, T_SALE, BOB_TYPE, JITA, 7.0, 1, False, 8, None, None, None),
    ])


def test_station_report_keeps_the_station_pool_and_other_channels_out(tenant):
    _seed()
    _rebuild()

    summary = station_realized.do_get_station_realized_summary(days=30)
    assert summary["status"]["needs_rebuild"] is False
    assert summary["revenue"] == pytest.approx(42.0)
    assert summary["cost_of_goods_sold"] == pytest.approx(25.0)
    assert summary["realized_profit"] == pytest.approx(17.0)
    assert summary["units_sold"] == pytest.approx(2.0)
    assert summary["avg_buy"] == pytest.approx(12.5)
    assert summary["avg_sell_net"] == pytest.approx(21.0)
    assert summary["spread"] == pytest.approx(8.5)
    assert summary["turnover_days"] == pytest.approx(3.0)
    assert summary["open_position_units"] == pytest.approx(1.0)
    assert summary["open_position_cost"] == pytest.approx(5.0)
    assert summary["fees_by_day"]
    assert summary["fees_by_day"][0]["broker_fee"] == pytest.approx(0.0)
    assert summary["fees_by_day"][0]["sales_tax"] is None

    rows = {row["type_id"]: row for row in station_realized.do_get_station_realized_items(days=30)["rows"]}
    assert set(rows) == {WIDGET, HULL}
    assert rows[WIDGET]["avg_buy"] == pytest.approx(5.0)
    assert rows[WIDGET]["avg_sell_net"] == pytest.approx(12.0)
    assert rows[WIDGET]["spread"] == pytest.approx(7.0)
    assert rows[WIDGET]["turnover_days"] == pytest.approx(3.0)
    assert rows[HULL]["profit"] == pytest.approx(10.0)

    detail = station_realized.do_get_station_realized_item(WIDGET, days=30)
    assert len(detail["sales"]) == 1
    sale_ref = detail["sales"][0]["sale_ref"]
    lots = station_realized.do_get_station_realized_sale(sale_ref)["lots"]
    assert lots[0]["unit_price"] == pytest.approx(5.0)
    assert lots[0]["quantity"] == pytest.approx(1.0)
    assert lots[0]["freight"] == pytest.approx(0.0)

    position = station_realized.do_get_station_realized_position()
    assert position["needs_rebuild"] is False
    assert position["cost"] == pytest.approx(5.0)
    assert [row["type_id"] for row in position["rows"]] == [WIDGET]

    trading = trade_actions.do_get_trading_realized_summary(days=30)
    # Bob's Jita sale is trading. Carol's home sale is also channel trading,
    # but Carol shares wallet only with Station Trading, so the Trading page
    # does not show it (U3a).
    assert trading["revenue"] == pytest.approx(7.0)
    production = production_actions.do_get_realized_summary(days=30)
    assert production["revenue"] == pytest.approx(0.0)


def test_revoking_the_station_trading_share_hides_the_report(tenant):
    _seed()
    _rebuild()
    assert station_realized.do_get_station_realized_summary(days=30)["realized_profit"] == pytest.approx(17.0)

    with storage.connect() as conn:
        conn.execute("DELETE FROM esi_sharing WHERE tool_key = 'station_trading'")
    hidden = station_realized.do_get_station_realized_summary(days=30)
    assert hidden["status"]["needs_rebuild"] is True
    assert hidden["status"]["sharing_changed"] is True
    assert hidden["realized_profit"] == 0.0
    assert hidden["open_position_cost"] is None
    assert station_realized.do_get_station_realized_items(days=30)["rows"] == []
    assert station_realized.do_get_station_realized_position()["needs_rebuild"] is True
