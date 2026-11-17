"""Volume-weighted 5th percentile of an order-book side (esi_client._summarize_orders)."""
import pytest

from eve_trader import esi_client, hubs, market_depth
from eve_trader.config import TradingConfig
from eve_trader.esi_client import _summarize_orders, _summarize_orders_by_count

JITA, AMARR = 10000002, 10000043
HAIRCUT = 0.9463


def _sell(price, vol):
    return {"price": float(price), "volume_remain": vol, "is_buy_order": False}


def _buy(price, vol):
    return {"price": float(price), "volume_remain": vol, "is_buy_order": True}


def test_single_order_keeps_its_price():
    # Case 1: one unit at 10,000 and nothing else. Only listed quantity can warn about it.
    s = _summarize_orders([_sell(10_000, 1)])
    assert s.sell_percentile == 10_000.0
    assert s.sell_volume == 1
    assert s.sell_percentile * HAIRCUT == pytest.approx(9463.0)


def test_two_baits_among_19_fair_orders_do_not_set_the_price():
    # Case 2: 19 orders of 10 at 130 plus two 1-unit baits at 1. Total 192, 5% = 9.6:
    # cumulative 1, 2, then 12 at the first 130 order -> 130 (was 1.0).
    orders = [_sell(130, 10) for _ in range(19)] + [_sell(1, 1), _sell(1, 1)]
    new = _summarize_orders(orders)
    old = _summarize_orders_by_count(orders)
    assert new.sell_percentile == 130.0
    assert new.sell_percentile * HAIRCUT == pytest.approx(123.019, abs=1e-3)
    assert old.sell_percentile == 1.0
    assert new.sell_volume == old.sell_volume == 192


def test_three_baits_among_40_fair_orders():
    # Case 3: 40 x 10 at 130 plus three baits. Total 403, 5% = 20.15 -> 130 (the count rule gave 1.0).
    orders = [_sell(130, 10) for _ in range(40)] + [_sell(1, 1)] * 3
    assert _summarize_orders(orders).sell_percentile == 130.0
    assert _summarize_orders_by_count(orders).sell_percentile == 1.0


class _BookClient:
    def __init__(self, books):  # {region: [orders]}
        self.books = books

    def region_order_stats_bulk(self, region_id, type_ids):
        return {t: _summarize_orders(self.books.get(region_id, [])) for t in type_ids}


def test_hub_with_a_bait_order_keeps_the_real_landed_price():
    # Case 4: ten orders of 10 at 100 plus one unit at 1. Total 101, 5% = 5.05: cumulative 1, then
    # 11 at the first 100 order -> 100.
    client = _BookClient({AMARR: [_sell(100, 10) for _ in range(10)] + [_sell(1, 1)]})
    p = hubs.hub_pricing(client, AMARR, [1], {1: 1.0}, broker_fee=0.0, freight_fallback=0.0,
                         cfg=TradingConfig())
    assert p.landed(1, 1.0, 0.0) == pytest.approx(100.0)


def test_market_depth_tiers_still_hold_the_real_depth():
    # Case 5: one unit at 1 plus 100,000 at 100. Reference was 1.0 (tiers held 1 unit); now 100.
    orders = [_sell(1, 1), _sell(100, 100_000)]
    ref = _summarize_orders(orders).sell_percentile
    assert ref == 100.0
    tiers = market_depth.sell_price_tiers(orders, ref, 0.05)
    assert sum(units for _price, units in tiers) == 100_001
    deep = [(price, units) for price, units in tiers if units == 100_000]
    assert deep and deep[0][0] == pytest.approx(100.0)
    # The old reference made the tiers contain the bait only.
    old_ref = _summarize_orders_by_count(orders).sell_percentile
    assert sum(u for _p, u in market_depth.sell_price_tiers(orders, old_ref, 0.05)) == 1


def test_all_hubs_does_not_assign_the_item_to_the_bait_hub():
    # Case 6: Jita ten orders at 100, Amarr the same plus a 1-unit bait at 1. Same real price and
    # freight, so the first hub (Jita) keeps the item; the bait used to win Amarr with 1.0.
    fair = [_sell(100, 10) for _ in range(10)]
    client = _BookClient({JITA: list(fair), AMARR: fair + [_sell(1, 1)]})
    p = hubs.hub_pricing(client, hubs.ALL_HUBS, [1], {1: 1.0}, broker_fee=0.0, freight_fallback=0.0,
                         cfg=TradingConfig())
    assert p.hub_by_type[1] == JITA
    assert p.stats[1].sell_percentile == 100.0


def test_even_book_is_unchanged():
    orders = [_sell(100 + i, 10) for i in range(40)] + [_buy(90 - i, 10) for i in range(40)]
    new, old = _summarize_orders(orders), _summarize_orders_by_count(orders)
    # 40 equal-volume orders: 5% of the volume is order 2 (index 1); the count rule takes index 2.
    assert new.sell_percentile == 101.0 and old.sell_percentile == 102.0
    assert new.sell_volume == old.sell_volume == 400
    # A 20-order even book: 5% of the volume is the first order; the count rule takes the second.
    even = [_sell(100 + i, 10) for i in range(20)]
    assert _summarize_orders(even).sell_percentile == 100.0
    assert _summarize_orders_by_count(even).sell_percentile == 101.0


def test_buy_side_mirrors_the_sell_side():
    # Highest bid first. Two 1-unit baits at 500 on top of 19 fair bids of 10 at 100.
    orders = [_buy(500, 1), _buy(500, 1)] + [_buy(100, 10) for _ in range(19)]
    assert _summarize_orders(orders).buy_percentile == 100.0
    assert _summarize_orders_by_count(orders).buy_percentile == 500.0


def test_empty_side_is_none_and_zero_volume_falls_back():
    s = _summarize_orders([_buy(5, 3)])
    assert s.sell_percentile is None and s.sell_volume == 0
    z = _summarize_orders([_sell(7, 0), _sell(9, 0)])
    assert z.sell_percentile == 7.0


def test_module_switch_selects_the_count_rule(monkeypatch):
    orders = [_sell(130, 10) for _ in range(19)] + [_sell(1, 1), _sell(1, 1)]
    monkeypatch.setattr(esi_client, "PERCENTILE_BY_VOLUME", False)
    assert _summarize_orders(orders).sell_percentile == 1.0
    monkeypatch.setattr(esi_client, "PERCENTILE_BY_VOLUME", True)
    assert _summarize_orders(orders).sell_percentile == 130.0
