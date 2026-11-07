import pytest

from eve_trader.esi_client import ESIError, OrderStats
from eve_trader.goonmetrics_client import CurrentPrice, HistoryPoint
from eve_trader.station_trading import actions
from eve_trader.station_trading import esi_sync as st_sync
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


STATION = 60003760
ELSEWHERE = 60008494


def _order(order_id, price, is_buy, location_id=STATION, volume=10):
    return {"order_id": order_id, "price": price, "is_buy_order": is_buy,
            "location_id": location_id, "volume_remain": volume}


class FakeESIClient:
    """Raw order books per type; records every region/type request."""

    def __init__(self, books):
        self._books = books
        self.raw_calls = []

    def region_orders_raw_bulk(self, region_id, type_ids, max_workers=10):
        self.raw_calls.append((region_id, list(type_ids)))
        return {tid: self._books.get(tid, []) for tid in type_ids}


def test_confirm_live_bounded_to_given_type_ids():
    client = FakeESIClient({100: [_order(1, 99.0, False), _order(2, 91.0, True)],
                            200: [_order(3, 5.0, False)]})

    result = confirm_live([100], STATION, client=client, own_order_ids=set())

    assert list(result) == [100]
    assert client.raw_calls == [(10000002, [100])]
    assert result[100] == OrderStats(sell_percentile=99.0, sell_volume=10, buy_percentile=91.0, buy_volume=10)


def test_confirm_live_empty_type_ids_makes_no_call():
    client = FakeESIClient({})
    assert confirm_live([], STATION, client=client) == {}
    assert client.raw_calls == []


def test_confirm_live_reads_the_given_hub_region():
    client = FakeESIClient({})
    confirm_live([1], STATION, hub_region_id=10000043, client=client, own_order_ids=set())
    assert client.raw_calls == [(10000043, [1])]


def test_confirm_live_prices_the_station_not_the_region():
    # Station: sell 100 / buy 70. Elsewhere in the region: sell 90 / buy 85,
    # which used to flip the margin negative.
    client = FakeESIClient({100: [
        _order(1, 100.0, False), _order(2, 70.0, True),
        _order(3, 90.0, False, ELSEWHERE), _order(4, 85.0, True, ELSEWHERE),
    ]})

    stats = confirm_live([100], STATION, client=client, own_order_ids=set())[100]
    assert (stats.buy_percentile, stats.sell_percentile) == (70.0, 100.0)

    cfg = StationTradingConfig()
    cfg.broker_fee_rate = 0.0147
    cfg.sales_tax_rate = 0.0337
    profit, margin = actions._profit(stats.buy_percentile, stats.sell_percentile, cfg)
    assert profit == pytest.approx(24.131, abs=1e-6)
    assert round(profit, 2) == 24.13
    assert round(margin * 100, 1) == 34.0


def test_confirm_live_excludes_own_orders():
    client = FakeESIClient({100: [
        _order(1, 100.0, False), _order(10, 95.0, False),  # 10 is mine
        _order(2, 70.0, True), _order(20, 75.0, True),  # 20 is mine
    ]})

    stats = confirm_live([100], STATION, client=client, own_order_ids={10, 20})[100]

    assert (stats.buy_percentile, stats.sell_percentile) == (70.0, 100.0)


def test_confirm_live_reads_own_orders_when_not_given(monkeypatch):
    class _Client(FakeESIClient):
        tokens = object()

        def character_orders(self, character_id, auth_role):
            return [_order(10, 95.0, False), _order(99, 1.0, False, ELSEWHERE)]

    monkeypatch.setattr(st_sync, "list_shared_trader_characters", lambda tm=None: [("trader:1", 1, "Me")])
    client = _Client({100: [_order(1, 100.0, False), _order(10, 95.0, False), _order(2, 70.0, True)]})

    stats = confirm_live([100], STATION, client=client)[100]

    assert stats.sell_percentile == 100.0  # own order 10 (95.0) is gone


def test_confirm_live_without_share_or_token_does_not_error(monkeypatch):
    class _Client(FakeESIClient):
        tokens = object()

    def _no_tenant(tm=None):
        raise RuntimeError("no tenant")
    monkeypatch.setattr(st_sync, "list_shared_trader_characters", _no_tenant)
    client = _Client({100: [_order(1, 100.0, False), _order(2, 70.0, True)]})

    stats = confirm_live([100], STATION, client=client)[100]

    assert (stats.buy_percentile, stats.sell_percentile) == (70.0, 100.0)


def test_confirm_live_failed_own_order_read_continues(monkeypatch):
    class _Client(FakeESIClient):
        tokens = object()

        def character_orders(self, character_id, auth_role):
            raise ESIError("boom")

    monkeypatch.setattr(st_sync, "list_shared_trader_characters", lambda tm=None: [("trader:1", 1, "Me")])
    client = _Client({100: [_order(1, 100.0, False)]})

    stats = confirm_live([100], STATION, client=client)[100]

    assert stats.sell_percentile == 100.0


def test_confirm_live_empty_station_book_is_none_not_zero():
    client = FakeESIClient({
        100: [_order(3, 90.0, False, ELSEWHERE), _order(4, 85.0, True, ELSEWHERE)],  # nothing at station
        200: [_order(5, 50.0, False)],  # sell side only
        # 300: no orders at all
    })

    result = confirm_live([100, 200, 300], STATION, client=client, own_order_ids=set())

    assert (result[100].buy_percentile, result[100].sell_percentile) == (None, None)
    assert (result[200].buy_percentile, result[200].sell_percentile) == (None, 50.0)
    assert (result[300].buy_percentile, result[300].sell_percentile) == (None, None)
    cfg = StationTradingConfig()
    for tid in (100, 200, 300):
        assert actions._profit(result[tid].buy_percentile, result[tid].sell_percentile, cfg) == (None, None)


def test_discovery_skips_goonmetrics_for_a_non_jita_hub():
    class _Boom:
        def current_prices(self, market):
            raise AssertionError("Goonmetrics' Jita dump must not be used for another hub")
    cfg = _cfg(hub_region_id=10000043)
    assert discover_candidates(cfg, client=_Boom()) == []


def test_discovery_uses_the_hub_region_for_history():
    seen = []

    class _Client(FakeGoonmetricsClient):
        def price_history_chunked(self, region_id, type_ids):
            seen.append(region_id)
            return super().price_history_chunked(region_id, type_ids)
    cfg = _cfg(min_spread_threshold=0.10, min_daily_volume=0.0)
    discover_candidates(cfg, client=_Client([_price(100, buy=90.0, sell=100.0)], [_history(100, 5.0)]))
    assert seen == [cfg.hub_region_id] == [10000002]
