"""Regression tests for a real audit finding (2026-08-28 full-system audit,
"F-04"): several ISK-denominated columns were REAL (single-precision
float32, ~7 significant digits) instead of DOUBLE PRECISION - silently
rounding any price/cost/profit above ~16.7M ISK. Confirmed live on
production before the fix: realized_trades rows above that threshold had
provably-rounded stored prices (e.g. an exact 535,100,000 ISK that couldn't
have been the real fill price). See docs/phase1_schema.sql's/
docs/doctrine_schema.sql's own comments on each migrated column.

Every value below is chosen specifically to fall past the float32 precision
cliff (>16,777,216, the largest integer float32 can represent exactly) -
each assertion would fail with an off-by-tens-to-hundreds-of-ISK error on
the old REAL columns and passes exactly on DOUBLE PRECISION.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.models import RealizedTrade

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_DOCTRINE_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "doctrine_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_doctrine_schema(_apply_phase1_schema, _apply_phase2_schema):
    """Same pattern as test_doctrine_storage.py's own fixture of this name -
    needed here too since doctrine_contracts/doctrine_contract_history's
    `price` column is one of the ones this file's tests cover."""
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_DOCTRINE_SCHEMA_SQL.read_text(encoding="utf-8"))

# Past the float32 exact-integer cliff (2^24) - not a round number in binary,
# so REAL is guaranteed to mangle it. Distinct cents-like fractional part
# rules out a coincidental round-trip too.
_PRICE_A = 535_123_456.78
_PRICE_B = 679_654_321.43
_PROFIT = 144_530_864.65


def test_realized_trades_price_round_trips_past_float32_precision(tenant):
    trade = RealizedTrade(
        type_id=34, item="Tritanium", buy_date="2026-08-01", buy_qty=1,
        buy_unit_price=_PRICE_A, sell_date="2026-08-02", sell_qty=1,
        sell_unit_price=_PRICE_B, matched_qty=1, realized_profit=_PROFIT, margin=0.27,
    )
    storage.save_realized_trades([trade], run_ts="2026-08-28T00:00:00")

    with storage.connect() as conn:
        row = conn.execute(
            "SELECT buy_unit_price, sell_unit_price, realized_profit FROM realized_trades WHERE type_id = ?",
            (34,),
        ).fetchone()

    assert row == (_PRICE_A, _PRICE_B, _PROFIT)


def test_manual_blueprint_copy_cost_round_trips_past_float32_precision(tenant):
    storage.upsert_manual_blueprint_copy_cost(34, "Tritanium", purchase_cost=_PRICE_A, runs=10)

    rows = storage.load_manual_blueprint_copy_costs()

    assert rows == [(34, "Tritanium", _PRICE_A, 10)]


def test_shortlist_snapshot_money_columns_round_trip_past_float32_precision(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO shortlist_snapshot (run_ts, item_id, item, category, landed_cost, net_sell, "
            "profit_per_unit, profit_per_m3, jita_sell, import_cost) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-28T00:00:00", 34, "Tritanium", "Minerals",
             _PRICE_A, _PRICE_B, _PROFIT, _PROFIT, _PRICE_A, _PRICE_B),
        )
        row = conn.execute(
            "SELECT landed_cost, net_sell, profit_per_unit, profit_per_m3, jita_sell, import_cost "
            "FROM shortlist_snapshot WHERE item_id = ?",
            (34,),
        ).fetchone()

    assert row == (_PRICE_A, _PRICE_B, _PROFIT, _PROFIT, _PRICE_A, _PRICE_B)


def test_new_candidates_avg_profit_m3_round_trips_past_float32_precision(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO new_candidates (run_ts, item, category, type_id, avg_profit_m3) VALUES (?,?,?,?,?)",
            ("2026-08-28T00:00:00", "Tritanium", "Minerals", 34, _PROFIT),
        )
        row = conn.execute(
            "SELECT avg_profit_m3 FROM new_candidates WHERE type_id = ?", (34,)
        ).fetchone()

    assert row == (_PROFIT,)


def test_doctrine_contract_price_round_trips_past_float32_precision(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO doctrine_contracts (contract_id, source_role, status, price, validation_status) "
            "VALUES (?,?,?,?,?)",
            (1001, "corp", "outstanding", _PRICE_A, "unmatched"),
        )
        row = conn.execute(
            "SELECT price FROM doctrine_contracts WHERE contract_id = ?", (1001,)
        ).fetchone()

    assert row == (_PRICE_A,)


def test_doctrine_contract_history_price_round_trips_past_float32_precision(tenant):
    storage.upsert_doctrine_contract_history([
        (1002, "corp", None, None, None, "Test Contract", _PRICE_B, None, None, None, None),
    ])

    rows = storage.load_doctrine_contract_history()

    assert rows[0][6] == _PRICE_B  # price is the 7th column (0-indexed 6) - see load_doctrine_contract_history
