"""Sales that belong to a channel but sit outside that tool's report scope are
counted on the Portfolio cards ("in no tool")."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eve_trader import portfolio, storage

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

_DOCS = Path(__file__).resolve().parent.parent / "docs"
ALICE = 930001
NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="session")
def _apply_ledger_schema(_apply_phase1_schema, _apply_phase2_schema, _apply_esi_access_schema):
    if not pg_helpers._postgres_available():
        return
    ledger_sql = (_DOCS / "production_ledger_schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(ledger_sql)
        conn.execute(ledger_sql)


@pytest.fixture(autouse=True)
def _wipe(_apply_ledger_schema):
    pg_helpers.wipe_tables("prod_realized_results", "prod_ledger_runs", "esi_sharing")
    yield


def _share(data_kind: str, tool_key: str):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (ALICE, data_kind, tool_key),
        )


def _sale(channel: str, profit: float, days_ago: int = 3):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO prod_realized_results (sale_ref, type_id, quantity, revenue, cost, profit, "
            "coverage_share, sale_date, channel, source, owner_type, owner_id) "
            "VALUES (?, 34, 1, 100, ?, ?, 1, ?, ?, 'market', 'character', ?)",
            (f"{channel}-{days_ago}-{profit}", 100 - profit, profit,
             NOW - timedelta(days=days_ago), channel, ALICE),
        )


def _unreported(tools=("production", "trading")):
    return portfolio._unreported_sales(tools, portfolio._realized_window("30"))


def test_trading_sale_of_a_production_only_wallet_is_in_no_tool(tenant):
    _share("wallet", "production")
    _sale("production", 10.0)
    _sale("trading", -6.0)
    assert _unreported() == (1, 100.0, -6.0)


def test_sharing_the_wallet_with_trading_leaves_nothing_unreported(tenant):
    _share("wallet", "production")
    _share("wallet", "trading")
    _sale("production", 10.0)
    _sale("trading", -6.0)
    assert _unreported() == (0, 0.0, 0.0)


def test_outside_the_period_and_without_a_card_is_not_counted(tenant):
    _share("wallet", "production")
    _sale("trading", -6.0, days_ago=60)
    assert _unreported() == (0, 0.0, 0.0)
    _sale("trading", -6.0, days_ago=2)
    assert _unreported(("production",)) == (0, 0.0, 0.0)
    assert _unreported(("production", "trading"))[0] == 1
