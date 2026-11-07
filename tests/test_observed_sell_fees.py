"""Observed sell fees: the wallet's real fee rate on recent sales, per channel.

The number is shown beside a setting and applied by the user's click through
the normal settings save; nothing here may change a setting by itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.config import TRADING_CONFIG, apply_config_overrides
from eve_trader.production import actions as production_actions
from eve_trader.production.config import PRODUCTION_CONFIG
from eve_trader.production.realized import report as production_report
from eve_trader.realized import access, sell_fees
from eve_trader.trade_realized import actions as trade_actions
from eve_trader.trade_realized import report as trade_report

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

_DOCS = Path(__file__).resolve().parent.parent / "docs"

ALICE = 920001
BOB = 920002
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


def _share(owner_id: int, data_kind: str, tool_key: str):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (owner_id, data_kind, tool_key),
        )


def _run(*, fingerprint: str | None = None):
    """A ledger run whose owner fingerprint matches the current sharing unless
    `fingerprint` says otherwise."""
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO prod_ledger_runs (cutover_t0, owner_scope_hash) VALUES (?, ?)",
            (NOW - timedelta(days=90), fingerprint or access.owner_scope_hash()),
        )


def _sale(n: int, *, gross=1000.0, tax=36.0, broker=17.7, channel="production",
          source="market", owner=ALICE, days_ago=5, tax_known=True):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO prod_realized_results (sale_ref, type_id, quantity, revenue, cost, profit, "
            "coverage_share, sale_date, gross, sales_tax, broker_fee, channel, source, "
            "owner_type, owner_id) VALUES (?, 34, 1, ?, 0, 0, 1, ?, ?, ?, ?, ?, ?, 'character', ?)",
            (f"{channel}-{source}-{owner}-{n}-{days_ago}", gross - tax, NOW - timedelta(days=days_ago),
             gross, tax if tax_known else None, broker, channel, source, owner),
        )


def _sales(count: int, **kwargs):
    for n in range(count):
        _sale(n, **kwargs)


def _prod_ready(count=25, **kwargs):
    _share(ALICE, "wallet", "production")
    _run()
    _sales(count, **kwargs)


def _trade_ready(count=25, **kwargs):
    _share(ALICE, "wallet", "trading")
    _run()
    _sales(count, channel="trading", **kwargs)


# ------------------------------------------------------------------ Production
def test_production_quote_is_tax_plus_broker_over_gross(tenant):
    _prod_ready(25)
    out = production_report.observed_sell_fees()
    assert out["sales"] == 25
    assert out["quote"] == pytest.approx(0.0537)
    assert out["sales_tax_rate"] == pytest.approx(0.036)
    assert out["broker_fee_rate"] == pytest.approx(0.0177)
    assert out["setting"] == "market_fees"
    assert out["suggested"] == pytest.approx(0.0537)
    assert out["reason"] is None
    assert out["relist_note"]


def test_quote_weights_by_gross_not_by_sale(tenant):
    _share(ALICE, "wallet", "production")
    _run()
    _sales(10, gross=100.0, tax=4.0, broker=2.0)       # 6 %
    for n in range(10, 20):
        _sale(n, gross=10000.0, tax=300.0, broker=100.0)  # 4 %
    out = production_report.observed_sell_fees()
    # (10*6 + 10*400) / (10*100 + 10*10000)
    assert out["quote"] == pytest.approx(4060.0 / 101000.0)


def test_exactly_twenty_sales_is_enough_nineteen_is_not(tenant):
    _prod_ready(19)
    short = production_report.observed_sell_fees()
    assert short["quote"] is None and short["suggested"] is None
    assert short["reason"] == sell_fees.REASON_TOO_FEW_SALES
    assert short["sales"] == 19
    assert short["message"]
    _sale(99)
    assert production_report.observed_sell_fees()["sales"] == 20
    assert production_report.observed_sell_fees()["quote"] == pytest.approx(0.0537)


def test_only_the_last_30_days_count(tenant):
    _prod_ready(20, days_ago=5)
    _sales(30, days_ago=45, tax=500.0, broker=400.0)    # would skew the quote
    out = production_report.observed_sell_fees()
    assert out["sales"] == 20
    assert out["quote"] == pytest.approx(0.0537)


def test_missing_tax_contract_and_other_channel_rows_do_not_count(tenant):
    _prod_ready(20)
    _sales(10, tax_known=False)                         # unknown tax is not zero tax
    _sales(10, source="contract")                       # no market fee by design
    _sales(10, channel="trading")                       # other tool's channel
    out = production_report.observed_sell_fees()
    assert out["sales"] == 20
    assert out["quote"] == pytest.approx(0.0537)


def test_unshared_wallet_owner_is_ignored(tenant):
    _prod_ready(20)
    _sales(30, owner=BOB, tax=500.0, broker=400.0)      # BOB shares nothing
    out = production_report.observed_sell_fees()
    assert out["sales"] == 20
    assert out["quote"] == pytest.approx(0.0537)


def test_no_ledger_run_gives_no_value(tenant):
    _share(ALICE, "wallet", "production")
    _sales(30)
    out = production_report.observed_sell_fees()
    assert out["quote"] is None
    assert out["reason"] == sell_fees.REASON_NO_RUN


def test_needs_rebuild_gives_no_value_even_with_many_sales(tenant):
    _prod_ready(30)
    _share(BOB, "wallet", "production")                 # sharing changed after the run
    out = production_report.observed_sell_fees()
    assert out["quote"] is None and out["suggested"] is None
    assert out["reason"] == sell_fees.REASON_NEEDS_REBUILD


def test_implausible_rate_gives_no_value(tenant):
    _prod_ready(25, gross=100.0, tax=70.0, broker=40.0)  # 110 % cannot be a fee rate
    out = production_report.observed_sell_fees()
    assert out["quote"] is None
    assert out["reason"] == sell_fees.REASON_NO_BASIS


def test_reading_the_quote_changes_no_setting(tenant):
    _prod_ready(25)
    before = PRODUCTION_CONFIG.market_fees
    production_actions.do_get_realized_observed_fees()
    production_actions.do_get_realized_observed_fees()
    assert PRODUCTION_CONFIG.market_fees == before
    assert "market_fees" not in storage.load_tenant_settings("production")


def test_applying_the_value_goes_through_the_normal_settings_save(tenant):
    _prod_ready(25)
    original = PRODUCTION_CONFIG.market_fees
    try:
        suggested = production_actions.do_get_realized_observed_fees()["suggested"]
        production_actions.do_update_settings({"market_fees": suggested})
        assert PRODUCTION_CONFIG.market_fees == pytest.approx(0.0537)
        assert storage.load_tenant_settings("production")["market_fees"] == pytest.approx(0.0537)
        with pytest.raises(production_actions.ActionError, match="market_fees"):
            production_actions.do_update_settings({"market_fees": 1.5})   # validation stays
    finally:
        apply_config_overrides(PRODUCTION_CONFIG, {"market_fees": original})


# --------------------------------------------------------------------- Trading
def test_trading_suggests_the_kept_share(tenant):
    _trade_ready(25)
    out = trade_actions.do_get_trading_realized_observed_fees()
    assert out["quote"] == pytest.approx(0.0537)
    assert out["setting"] == "structure_sell_haircut"
    assert out["suggested"] == pytest.approx(0.9463)
    assert out["sales"] == 25


def test_trading_and_production_channels_stay_separate(tenant):
    _share(ALICE, "wallet", "production")
    _share(ALICE, "wallet", "trading")
    _run()
    _sales(20, tax=36.0, broker=17.7)                               # production: 5.37 %
    _sales(20, channel="trading", tax=20.0, broker=10.0)            # trading: 3 %
    assert production_report.observed_sell_fees()["quote"] == pytest.approx(0.0537)
    assert trade_report.observed_sell_fees()["quote"] == pytest.approx(0.03)
    assert trade_report.observed_sell_fees()["suggested"] == pytest.approx(0.97)


def test_trading_needs_rebuild_and_minimum_count(tenant):
    _trade_ready(19)
    short = trade_report.observed_sell_fees()
    assert short["quote"] is None and short["suggested"] is None
    assert short["reason"] == sell_fees.REASON_TOO_FEW_SALES
    _sale(50, channel="trading")                                    # the 20th sale
    assert trade_report.observed_sell_fees()["quote"] == pytest.approx(0.0537)
    _share(BOB, "wallet", "trading")
    assert trade_report.observed_sell_fees()["reason"] == sell_fees.REASON_NEEDS_REBUILD


def test_applying_the_trading_value_goes_through_the_normal_settings_save(tenant):
    from eve_trader import actions

    _trade_ready(25)
    original = TRADING_CONFIG.structure_sell_haircut
    try:
        suggested = trade_actions.do_get_trading_realized_observed_fees()["suggested"]
        assert TRADING_CONFIG.structure_sell_haircut == original      # reading changes nothing
        actions.do_update_settings({"structure_sell_haircut": suggested})
        assert TRADING_CONFIG.structure_sell_haircut == pytest.approx(0.9463)
        assert storage.load_tenant_settings("trading")["structure_sell_haircut"] == pytest.approx(0.9463)
    finally:
        apply_config_overrides(TRADING_CONFIG, {"structure_sell_haircut": original})
