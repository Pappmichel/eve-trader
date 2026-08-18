"""Cross-cutting Trading + Production portfolio/risk overview.

The two tools stay fully independent everywhere else (separate config,
separate ESI scopes, separate UI sections/layouts) - this is the one place
that looks at both together, and it's deliberately additive: a combined
summary alongside each tool's own dedicated views, never a replacement for
either (confirmed with the user).
"""
from __future__ import annotations

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
