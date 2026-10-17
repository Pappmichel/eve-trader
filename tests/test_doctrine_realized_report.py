"""Doctrine report against Postgres: origin split, D11 scope, fitting resolution."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from eve_trader import storage
from eve_trader.doctrine import realized as doctrine_realized
from eve_trader.doctrine.config import DoctrineConfig, _doctrine_config_var
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

ALICE = 910001
BOB = 910002
CAROL = 910003
DAVE = 910004
MINERAL = 88011
HULL = 88012
MODULE = 88013
TRADED = 88016
STATION_TYPE = 88015
OTHER = 88014
HOME = 60000042
JITA = 60003760
DOC = 1049588174021
FIT = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
T_JOB = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
T_END = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
T_SALE = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def _apply_production_ledger_schema(_apply_phase1_schema, _apply_phase2_schema, _apply_esi_access_schema):
    if not pg_helpers._postgres_available():
        return
    ledger_sql = (_DOCS / "production_ledger_schema.sql").read_text(encoding="utf-8")
    orders_sql = (_DOCS / "special_orders_schema.sql").read_text(encoding="utf-8")
    doctrine_sql = (_DOCS / "doctrine_schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(ledger_sql)
        conn.execute(ledger_sql)
        conn.execute(orders_sql)
        conn.execute(orders_sql)
        conn.execute(doctrine_sql)
        conn.execute(doctrine_sql)


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
        "doctrine_contract_history",
    )
    yield


@pytest.fixture(autouse=True)
def _no_remote(monkeypatch):
    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {})
    from eve_trader.realized import names
    monkeypatch.setattr(names, "_esi_names", lambda ids: {})


@pytest.fixture
def _doctrine_structure():
    token = _doctrine_config_var.set(DoctrineConfig(doctrine_structure_id=DOC))
    try:
        yield
    finally:
        _doctrine_config_var.reset(token)


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
                              return_value=frozenset({ALICE, BOB, CAROL, DAVE})):
                return loader.rebuild_realized()


def _seed_doctrine_sale():
    _share(ALICE, "wallet", "production")
    _share(ALICE, "industry_jobs", "production")
    _share(ALICE, "contracts", "doctrine")
    _share(BOB, "wallet", "trading")
    _share(CAROL, "wallet", "station_trading")
    _share(DAVE, "contracts", "production")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, MINERAL, HOME, 10.0, 1, True, 1, None, None, None),
        ("character", ALICE, 0, 2, T0, MODULE, HOME, 20.0, 1, True, 2, None, None, None),
        ("character", BOB, 0, 3, T0, TRADED, HOME, 5.0, 1, True, 3, None, None, None),
        ("character", BOB, 0, 4, T_SALE, TRADED, HOME, 9.0, 1, False, 4, None, None, None),
        ("character", CAROL, 0, 5, T0, STATION_TYPE, JITA, 5.0, 1, True, 5, None, None, None),
        ("character", CAROL, 0, 6, T_SALE, STATION_TYPE, JITA, 8.0, 1, False, 6, None, None, None),
    ])
    ledger_db.upsert_ledger_industry_jobs([(
        "character", ALICE, 77, 1, 500, 9000, HULL, 1, None, None,
        5.0, 12345, None, None, ALICE, T_JOB, T_END, T_END, "delivered", None,
    )])
    ledger_db.insert_ledger_job_inputs([(
        "character", ALICE, 77, MINERAL, 1.0, 10, 1.0, None, None, None, None,
        "esi_blueprint", False,
    )])
    ledger_db.upsert_ledger_contracts([
        ("character", ALICE, 70, ALICE, None, 999, DOC, None,
         "item_exchange", "finished", "Stabber", 100.0, 0.0, T0, T_SALE, None),
        ("character", DAVE, 71, DAVE, None, 998, DOC, None,
         "item_exchange", "finished", "other", 40.0, 0.0, T0, T_SALE, None),
    ])
    ledger_db.upsert_ledger_contract_items([
        ("character", ALICE, 70, 1, HULL, 1, True, False),
        ("character", ALICE, 70, 2, MODULE, 1, True, False),
        ("character", DAVE, 71, 1, OTHER, 1, True, False),
    ])
    ledger_db.upsert_valuation_prices([(HULL, T_SALE.date(), 80.0, "buy_hub")])
    storage.upsert_doctrine_contract_history([(
        70, "doctrine:1", FIT, "Stabber Fleet", HULL, "Stabber", 100.0,
        999, "Buyer", T0, T_SALE,
    )])
    storage.upsert_doctrine_contract_history([(
        80, "doctrine:1", FIT, "Old Fit", HULL, "before", 10.0,
        997, "Old Buyer", T0, T_SALE,
    )])


def test_doctrine_report_splits_origin_and_keeps_other_channels_out(tenant, _doctrine_structure):
    _seed_doctrine_sale()
    _rebuild()

    summary = doctrine_realized.do_get_doctrine_realized_summary(days=30)
    assert summary["status"]["needs_rebuild"] is False
    assert summary["contracts"] == 1
    assert summary["revenue"] == pytest.approx(100.0)
    assert summary["cost_of_goods_sold"] == pytest.approx(35.0)
    assert summary["realized_profit"] == pytest.approx(65.0)
    assert summary["production_saving"] == pytest.approx(65.0)
    origin_cost = sum(row["cost"] for row in summary["cost_by_origin"])
    assert origin_cost == pytest.approx(summary["cost_of_goods_sold"])
    produced = next(row for row in summary["cost_by_origin"] if row["kind"] == "produced")
    market = next(row for row in summary["cost_by_origin"] if row["kind"] == "market")
    assert produced["cost"] == pytest.approx(15.0)
    assert market["cost"] == pytest.approx(20.0)

    fits = doctrine_realized.do_get_doctrine_realized_fittings(days=30)["rows"]
    assert len(fits) == 1
    assert fits[0]["fitting_id"] == FIT
    assert fits[0]["fitting_name"] == "Stabber Fleet"
    assert fits[0]["contracts"] == 1
    assert fits[0]["produced_cost"] == pytest.approx(15.0)
    assert fits[0]["bought_cost"] == pytest.approx(20.0)
    assert fits[0]["produced_cost"] + fits[0]["bought_cost"] == pytest.approx(fits[0]["cost"])

    listing = doctrine_realized.do_get_doctrine_realized_contracts(days=30)
    assert [row["contract_id"] for row in listing["rows"]] == [70]
    assert listing["rows"][0]["buyer_name"] == "Buyer"
    assert listing["rows"][0]["issuer_id"] == ALICE
    assert [row["contract_id"] for row in listing["before_archive"]] == [80]
    assert listing["before_archive"][0]["profit"] is None

    detail = doctrine_realized.do_get_doctrine_realized_contract("contract:70")
    by_type = {line["type_id"]: line for line in detail["lines"]}
    assert by_type[HULL]["origin"] == "produced"
    assert by_type[HULL]["cost"] == pytest.approx(15.0)
    assert by_type[MODULE]["origin"] == "bought"
    assert by_type[MODULE]["cost"] == pytest.approx(20.0)
    assert by_type[HULL]["chain"]
    assert sum(line["cost"] for line in detail["lines"]) == pytest.approx(35.0)
    assert detail["production_saving"] == pytest.approx(65.0)

    production = production_actions.do_get_realized_summary(days=30)
    assert production["revenue"] == pytest.approx(0.0)
    assert production["sold_through_doctrine"]["contracts"] == 1
    assert production["sold_through_doctrine"]["units"] == pytest.approx(1.0)
    assert production["sold_through_doctrine"]["cost"] == pytest.approx(15.0)

    trading = trade_actions.do_get_trading_realized_summary(days=30)
    assert trading["revenue"] == pytest.approx(9.0)


def test_revoking_the_doctrine_share_hides_the_report(tenant, _doctrine_structure):
    _seed_doctrine_sale()
    _rebuild()
    assert doctrine_realized.do_get_doctrine_realized_summary(days=30)["realized_profit"] == pytest.approx(65.0)

    with storage.connect() as conn:
        conn.execute(
            "DELETE FROM esi_sharing WHERE tool_key = 'doctrine'",
        )
    hidden = doctrine_realized.do_get_doctrine_realized_summary(days=30)
    assert hidden["status"]["needs_rebuild"] is True
    assert hidden["status"]["sharing_changed"] is True
    assert hidden["realized_profit"] == 0.0
    assert doctrine_realized.do_get_doctrine_realized_contracts(days=30)["rows"] == []


def test_matcher_fills_a_doctrine_fitting_when_history_has_none(tenant, _doctrine_structure):
    _share(ALICE, "contracts", "doctrine")
    _share(ALICE, "wallet", "production")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, MODULE, HOME, 20.0, 1, True, 1, None, None, None),
    ])
    ledger_db.upsert_ledger_contracts([(
        "character", ALICE, 90, ALICE, None, 999, DOC, None,
        "item_exchange", "finished", "named fit", 30.0, 0.0, T0, T_SALE, None,
    )])
    ledger_db.upsert_ledger_contract_items([
        ("character", ALICE, 90, 1, MODULE, 1, True, False),
    ])

    def _match(contract_id, title, items, candidates, cfg=None):
        assert contract_id == 90
        assert title == "named fit"
        assert items and items[0].type_id == MODULE
        return ("22222222-2222-2222-2222-222222222222", 1.0, [], "exact")

    with patch("eve_trader.doctrine.engine.load_match_candidates", return_value=[object()]):
        with patch("eve_trader.doctrine.engine.match_and_validate_contract", side_effect=_match):
            _rebuild()

    with storage.connect() as conn:
        row = conn.execute(
            "SELECT fitting_id, warnings FROM prod_realized_results WHERE sale_ref = 'contract:90'",
        ).fetchone()
    assert row[0] == "22222222-2222-2222-2222-222222222222"
    assert "unmatched_doctrine" not in (row[1] or [])
