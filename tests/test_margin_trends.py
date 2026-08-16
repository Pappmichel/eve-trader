import pandas as pd

from eve_trader.config import TradingConfig
from eve_trader.history_backtest import compute_margin_trends


def _history_df(rows):
    return pd.DataFrame(rows, columns=["region_id", "type_id", "date", "min_price", "max_price", "avg_price", "movement", "num_orders"])


def _row(region_id, type_id, date, avg_price):
    return (region_id, type_id, date, avg_price, avg_price, avg_price, 0.0, 1)


def test_no_history_returns_empty():
    cfg = TradingConfig()
    assert compute_margin_trends(_history_df([]), {100: 1.0}, cfg) == {}


def test_too_little_history_is_omitted():
    cfg = TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, structure_sell_haircut=1.0)
    rows = [_row(cfg.jita_region_id, 100, f"2026-01-0{d}", 100.0) for d in range(1, 4)] + \
           [_row(cfg.reference_region_id, 100, f"2026-01-0{d}", 200.0) for d in range(1, 4)]
    # Only 3 paired days - below MIN_TREND_HISTORY_DAYS (6).
    assert compute_margin_trends(_history_df(rows), {100: 1.0}, cfg) == {}


def test_rising_margin_gives_positive_trend_pct():
    cfg = TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, structure_sell_haircut=1.0)
    # jita (buy) price stays flat at 100 the whole time; reference (sell)
    # price ramps up over 10 days from 150 to 240 - margin should clearly be
    # rising, so the recent 3-day average margin must beat the 30-day
    # (here: all 10 days) average.
    jita_rows = [_row(cfg.jita_region_id, 100, f"2026-01-{d:02d}", 100.0) for d in range(1, 11)]
    ref_prices = [150, 160, 170, 180, 190, 200, 210, 220, 230, 240]
    ref_rows = [_row(cfg.reference_region_id, 100, f"2026-01-{d:02d}", p) for d, p in zip(range(1, 11), ref_prices)]
    df = _history_df(jita_rows + ref_rows)

    result = compute_margin_trends(df, {100: 1.0}, cfg)

    assert 100 in result
    assert result[100]["trend_pct"] > 0
    assert result[100]["recent_avg_margin"] > result[100]["baseline_avg_margin"]


def test_falling_margin_gives_negative_trend_pct():
    cfg = TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, structure_sell_haircut=1.0)
    jita_rows = [_row(cfg.jita_region_id, 100, f"2026-01-{d:02d}", 100.0) for d in range(1, 11)]
    ref_prices = [240, 230, 220, 210, 200, 190, 180, 170, 160, 150]
    ref_rows = [_row(cfg.reference_region_id, 100, f"2026-01-{d:02d}", p) for d, p in zip(range(1, 11), ref_prices)]
    df = _history_df(jita_rows + ref_rows)

    result = compute_margin_trends(df, {100: 1.0}, cfg)

    assert result[100]["trend_pct"] < 0


def test_omits_type_id_when_baseline_margin_is_near_zero():
    # Reproduces a real live case (2026-07-15): a week of mild losses
    # (~-6.5% margin) followed by 3 recent days of a perfectly normal +15%
    # margin averages out to a baseline within a hair of 0 (-0.05%) even
    # though neither individual figure is unusual - dividing by that near-zero
    # baseline used to produce a trend_pct in the hundreds of percent
    # (observed live: up to +26,486% for a real shortlist item). Must now be
    # omitted entirely rather than reported as a wild, meaningless swing.
    cfg = TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, structure_sell_haircut=1.0)
    jita_rows = [_row(cfg.jita_region_id, 100, f"2026-01-{d:02d}", 100.0) for d in range(1, 11)]
    ref_prices = [93.5] * 7 + [115.0] * 3  # margins: -0.065 * 7, +0.15 * 3 -> mean ~ -0.0005
    ref_rows = [_row(cfg.reference_region_id, 100, f"2026-01-{d:02d}", p) for d, p in zip(range(1, 11), ref_prices)]
    df = _history_df(jita_rows + ref_rows)

    assert compute_margin_trends(df, {100: 1.0}, cfg) == {}


def test_ignores_type_ids_not_in_volumes():
    cfg = TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, structure_sell_haircut=1.0)
    jita_rows = [_row(cfg.jita_region_id, 999, f"2026-01-{d:02d}", 100.0) for d in range(1, 11)]
    ref_rows = [_row(cfg.reference_region_id, 999, f"2026-01-{d:02d}", 200.0) for d in range(1, 11)]
    df = _history_df(jita_rows + ref_rows)

    # 999 has plenty of history, but isn't one of the shortlist's volumes -
    # must not appear in the result (no volume_m3 to compute landed cost with).
    assert compute_margin_trends(df, {100: 1.0}, cfg) == {}
