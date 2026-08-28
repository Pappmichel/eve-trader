from eve_trader.esi_client import OrderStats
from eve_trader.goonmetrics_client import CurrentPrice, HistoryPoint
from eve_trader.station_trading.candidate_discovery import confirm_live, discover_candidates
from eve_trader.station_trading.config import StationTradingConfig


class FakeGoonmetricsClient:
    def __init__(self, prices, history):
        self._prices = prices
        self._history = history

    def current_prices(self, market):
        return self._prices

    def price_history_chunked(self, region_id, type_ids):
        return [p for p in self._history if p.type_id in type_ids]


def _price(type_id, buy, sell):
    return CurrentPrice(type_id=type_id, updated="", buy=buy, sell=sell)


def _history(type_id, movement):
    return HistoryPoint(region_id=10000002, type_id=type_id, date="2026-08-27",
                         min_price=0.0, max_price=0.0, avg_price=0.0, movement=movement, num_orders=1)


def _cfg(**overrides):
    cfg = StationTradingConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def test_filters_by_min_spread_threshold():
    cfg = _cfg(min_spread_threshold=0.10, min_daily_volume=0.0)
    prices = [_price(100, buy=90.0, sell=100.0),  # 10% spread - passes
              _price(200, buy=95.0, sell=100.0)]  # 5% spread - filtered out
    history = [_history(100, 5.0)]
    client = FakeGoonmetricsClient(prices, history)

    result = discover_candidates(cfg, client=client)

    assert [r["type_id"] for r in result] == [100]
    assert result[0]["spread_pct"] == 0.10
    assert result[0]["avg_daily_volume"] == 5.0


def test_filters_by_min_daily_volume():
    cfg = _cfg(min_spread_threshold=0.0, min_daily_volume=10.0)
    prices = [_price(100, buy=90.0, sell=100.0)]
    history = [_history(100, 5.0)]  # below min_daily_volume
    client = FakeGoonmetricsClient(prices, history)

    assert discover_candidates(cfg, client=client) == []


def test_zero_or_missing_history_means_zero_volume_and_gets_filtered():
    cfg = _cfg(min_spread_threshold=0.0, min_daily_volume=1.0)
    prices = [_price(100, buy=90.0, sell=100.0)]
    client = FakeGoonmetricsClient(prices, history=[])  # no history points at all

    assert discover_candidates(cfg, client=client) == []


def test_ignores_items_with_no_real_two_sided_market():
    cfg = _cfg(min_spread_threshold=0.0, min_daily_volume=0.0)
    prices = [_price(100, buy=0.0, sell=100.0),   # no real buy side
              _price(200, buy=100.0, sell=0.0),   # no real sell side
              _price(300, buy=100.0, sell=90.0)]  # inverted/crossed - not a real spread
    client = FakeGoonmetricsClient(prices, history=[])

    assert discover_candidates(cfg, client=client) == []


def test_caps_results_to_top_n():
    cfg = _cfg(min_spread_threshold=0.0, min_daily_volume=0.0)
    prices = [_price(i, buy=90.0, sell=100.0) for i in range(5)]
    history = [_history(i, float(i + 1)) for i in range(5)]
    client = FakeGoonmetricsClient(prices, history)

    result = discover_candidates(cfg, client=client, top_n=2)

    assert len(result) == 2
    assert [r["type_id"] for r in result] == [4, 3]  # highest volume (= highest spread*volume) first


def test_sorts_by_spread_times_volume_richest_first():
    cfg = _cfg(min_spread_threshold=0.0, min_daily_volume=0.0)
    prices = [_price(100, buy=50.0, sell=100.0),  # 50% spread, low volume
              _price(200, buy=90.0, sell=100.0)]  # 10% spread, high volume
    history = [_history(100, 2.0), _history(200, 1000.0)]
    client = FakeGoonmetricsClient(prices, history)

    result = discover_candidates(cfg, client=client)

    assert [r["type_id"] for r in result] == [200, 100]


class FakeESIClient:
    def __init__(self, stats_by_type):
        self._stats_by_type = stats_by_type

    def region_order_stats_bulk(self, region_id, type_ids):
        return {tid: self._stats_by_type[tid] for tid in type_ids}


def test_confirm_live_bounded_to_given_type_ids():
    stats = {100: OrderStats(sell_percentile=99.0, sell_volume=10.0, buy_percentile=91.0, buy_volume=5.0)}
    client = FakeESIClient(stats)

    result = confirm_live([100], client=client)

    assert result == stats


def test_confirm_live_empty_type_ids_makes_no_call():
    result = confirm_live([], client=FakeESIClient({}))
    assert result == {}
