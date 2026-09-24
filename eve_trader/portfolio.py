"""Cross-cutting Trading + Production portfolio/risk overview.

The two tools stay fully independent everywhere else (separate config,
separate ESI scopes, separate UI sections/layouts) - this is the one place
that looks at both together, and it's deliberately additive: a combined
summary alongside each tool's own dedicated views, never a replacement for
either (confirmed with the user).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from . import storage
from .config import TRADING_CONFIG, TradingConfig


def portfolio_overview(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Combines Trading's realized P&L (latest reconciliation run) with
    Production's stock value into one read-only summary, plus a simple
    day-to-day profit volatility signal. Each half degrades independently to
    zero/None if that tool has no data yet, rather than failing the whole
    overview - e.g. a fresh install with Trading data but no Production stock
    targets configured still shows what it has.

    daily_profit_volatility is a plain population standard deviation of
    per-day realized profit (not a formal Value-at-Risk model - this app has
    no options-pricing-grade statistics infrastructure, and a plain stdev of
    realized daily P&L is the standard, well-understood starting point real
    trading tools use for an at-a-glance risk indicator). None below 2 days
    of data - a single day has no meaningful spread.
    """
    trades_df = storage.read_table("realized_trades")
    if trades_df.empty:
        trading_realized_profit = 0.0
        trading_average_margin = 0.0
        daily_profit_volatility: Optional[float] = None
        trade_count = 0
    else:
        latest_run = trades_df["run_ts"].max()
        latest = trades_df[trades_df["run_ts"] == latest_run]
        trading_realized_profit = float(latest["realized_profit"].sum())
        weighted_denom = float((latest["buy_unit_price"] * latest["matched_qty"]).sum())
        trading_average_margin = (trading_realized_profit / weighted_denom) if weighted_denom else 0.0
        daily = latest.assign(day=latest["sell_date"].str.slice(0, 10)).groupby("day")["realized_profit"].sum()
        daily_profit_volatility = float(daily.std(ddof=0)) if len(daily) >= 2 else None
        trade_count = int(len(latest))

    production_stock_value = 0.0
    stock_targets_configured = bool(storage.load_stock_targets())
    if stock_targets_configured:
        from .production.config import PRODUCTION_CONFIG
        from .production.engine import stock_value
        production_stock_value = stock_value(PRODUCTION_CONFIG)["total_value"]

    return {
        "trading_realized_profit": trading_realized_profit,
        "trading_average_margin": trading_average_margin,
        "trading_daily_profit_volatility": daily_profit_volatility,
        "trading_trade_count": trade_count,
        "production_stock_value": production_stock_value,
        "production_stock_targets_configured": stock_targets_configured,
        "combined_value": trading_realized_profit + production_stock_value,
    }


def take_portfolio_snapshot(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Computes portfolio_overview() and upserts today's row into
    portfolio_snapshots - idempotent, safe to call more than once on the
    same day (see storage.upsert_portfolio_snapshot). The two triggers
    (scheduler job + lazy fallback on page load) both call this same
    function, never portfolio_overview() directly, so there is one write
    path, not two implementations that could drift.

    total_wealth/wealth_assets_value/wealth_wallet_balance stay None here -
    total_wealth() (PORTFOLIO_REWORK_PLAN.md section 6) wires into this
    function once Portfolio becomes a real ESI-sharing participant; until
    then every snapshot correctly records "no Total Wealth data yet" rather
    than a wrong zero.
    """
    overview = portfolio_overview(cfg)
    wealth = {"total_wealth": None, "wealth_assets_value": None, "wealth_wallet_balance": None}
    storage.upsert_portfolio_snapshot(date.today(), {**overview, **wealth})
    return {**overview, **wealth}


def do_get_portfolio_overview(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """`GET /api/portfolio/overview`'s own action - a real decision (has a
    snapshot been taken today yet?), not a bare passthrough, so it lives
    here rather than directly in the router. Takes today's snapshot lazily
    on the first overview read of the day - the fallback path for when the
    scheduler is disabled (the default), so history still fills in one row
    per day purely from normal page usage. Every later read that same day
    is the cheap, plain live read - take_portfolio_snapshot() is never
    called more than once per day from here."""
    if storage.latest_portfolio_snapshot_date() != date.today():
        return take_portfolio_snapshot(cfg)
    return portfolio_overview(cfg)


def do_get_portfolio_history(days: Optional[int] = None) -> list[dict]:
    """`GET /api/portfolio/history`'s own action. `days=None` returns every
    snapshot this tenant has ever taken (unbounded retention); otherwise
    only the last `days` days, inclusive of today."""
    since = date.today() - timedelta(days=days - 1) if days is not None else None
    rows = storage.load_portfolio_snapshots(since=since)
    columns = ("snapshot_date",) + storage.PORTFOLIO_SNAPSHOT_COLUMNS
    return [dict(zip(columns, row)) for row in rows]
