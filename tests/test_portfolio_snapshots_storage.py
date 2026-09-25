"""storage.py's portfolio_snapshots functions (PORTFOLIO_REWORK_PLAN.md
section 5) - real Postgres, since these are plain SQL upsert/read wrappers
with no business logic worth mocking."""
from datetime import date

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_portfolio_schema, tenant, tenant_pair  # noqa: F401

pytestmark = pg_helpers.postgres_required()

_VALUES = {
    "trading_realized_profit": 1000.0,
    "trading_average_margin": 0.2,
    "trading_daily_profit_volatility": None,
    "trading_trade_count": 5,
    "production_stock_value": 2000.0,
    "production_stock_targets_configured": True,
    "combined_value": 3000.0,
    "total_wealth": None,
    "wealth_assets_value": None,
    "wealth_wallet_balance": None,
}


def test_upsert_and_load_snapshot(tenant):
    storage.upsert_portfolio_snapshot(date(2026, 9, 1), _VALUES)
    rows = storage.load_portfolio_snapshots()
    assert len(rows) == 1
    assert rows[0][0] == date(2026, 9, 1)
    assert rows[0][1] == 1000.0  # trading_realized_profit


def test_upsert_same_day_twice_overwrites_not_duplicates(tenant):
    storage.upsert_portfolio_snapshot(date(2026, 9, 1), _VALUES)
    updated = {**_VALUES, "combined_value": 9999.0}
    storage.upsert_portfolio_snapshot(date(2026, 9, 1), updated)

    rows = storage.load_portfolio_snapshots()
    assert len(rows) == 1
    assert rows[0][7] == 9999.0  # combined_value - latest values win


def test_load_portfolio_snapshots_ordering_and_since_filter(tenant):
    storage.upsert_portfolio_snapshot(date(2026, 9, 1), _VALUES)
    storage.upsert_portfolio_snapshot(date(2026, 9, 3), _VALUES)
    storage.upsert_portfolio_snapshot(date(2026, 9, 2), _VALUES)

    all_rows = storage.load_portfolio_snapshots()
    assert [r[0] for r in all_rows] == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]

    since_rows = storage.load_portfolio_snapshots(since=date(2026, 9, 2))
    assert [r[0] for r in since_rows] == [date(2026, 9, 2), date(2026, 9, 3)]


def test_latest_portfolio_snapshot_date_none_when_empty(tenant):
    assert storage.latest_portfolio_snapshot_date() is None


def test_latest_portfolio_snapshot_date_returns_newest(tenant):
    storage.upsert_portfolio_snapshot(date(2026, 9, 1), _VALUES)
    storage.upsert_portfolio_snapshot(date(2026, 9, 5), _VALUES)
    assert storage.latest_portfolio_snapshot_date() == date(2026, 9, 5)


def test_snapshots_isolated_between_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.upsert_portfolio_snapshot(date(2026, 9, 1), _VALUES)
    with storage.tenant_context(tenant_b):
        assert storage.load_portfolio_snapshots() == []
        assert storage.latest_portfolio_snapshot_date() is None
