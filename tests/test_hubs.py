"""eve_trader/hubs.py: best-hub pricing and the shared freight table (#222)."""
import pytest

from eve_trader import hubs
from eve_trader.config import ConfigError, TradingConfig, validate_trading_overrides
from eve_trader.esi_client import OrderStats

JITA, AMARR, DODIXIE, RENS = 10000002, 10000043, 10000032, 10000030


def _stats(sell):
    return OrderStats(sell, 10.0, None, 0.0)


class FakeClient:
    def __init__(self, prices):  # {region_id: {type_id: sell or None}}
        self.prices = prices
        self.calls = []

    def region_order_stats_bulk(self, region_id, type_ids):
        self.calls.append(region_id)
        return {t: _stats(self.prices.get(region_id, {}).get(t)) for t in type_ids}


def test_single_hub_uses_that_hub_and_the_tools_own_freight():
    client = FakeClient({AMARR: {1: 100.0}})
    p = hubs.hub_pricing(client, AMARR, [1], {1: 2.0}, broker_fee=0.01, freight_fallback=500.0,
                         cfg=TradingConfig(hub_freight_cost_per_m3={str(AMARR): 1.0}))
    assert client.calls == [AMARR]
    assert p.hub_by_type == {1: AMARR}
    assert p.freight_by_type == {1: 500.0}  # the table only applies in All hubs mode
    assert p.landed(1, 2.0, 0.01) == pytest.approx(100.0 * 1.01 + 500.0 * 2.0)


def test_all_hubs_picks_lowest_landed_cost_including_freight():
    # Amarr's price is lower, but its freight for this bulky item makes Jita cheaper.
    client = FakeClient({JITA: {1: 1000.0, 2: 50.0}, AMARR: {1: 900.0, 2: 40.0}})
    cfg = TradingConfig(hub_freight_cost_per_m3={str(JITA): 100.0, str(AMARR): 200.0})
    p = hubs.hub_pricing(client, hubs.ALL_HUBS, [1, 2], {1: 10.0, 2: 0.01},
                         broker_fee=0.0, freight_fallback=999.0, cfg=cfg)
    assert sorted(client.calls) == sorted(hubs.TRADE_HUBS)
    assert p.hub_by_type == {1: JITA, 2: AMARR}  # 1: 2000 vs 2900; 2: 51 vs 42
    assert p.freight_by_type == {1: 100.0, 2: 200.0}
    assert p.landed(1, 10.0, 0.0) == pytest.approx(2000.0)


def test_all_hubs_falls_back_to_tool_freight_and_keeps_unpriced_items():
    client = FakeClient({DODIXIE: {1: 10.0}})
    p = hubs.hub_pricing(client, hubs.ALL_HUBS, [1, 2], {1: 1.0, 2: 1.0},
                         broker_fee=0.0, freight_fallback=7.0, cfg=TradingConfig())
    assert p.hub_by_type[1] == DODIXIE and p.freight_by_type[1] == 7.0
    assert p.stats[2].sell_percentile is None and p.landed(2, 1.0, 0.0) is None


def test_freight_table_validation():
    validate_trading_overrides({"hub_freight_cost_per_m3": {str(JITA): 800, str(AMARR): 1200.5}})
    for bad in ({"jita": 1.0}, {str(JITA): -1.0}, {str(JITA): True}, [1, 2]):
        with pytest.raises(ConfigError):
            validate_trading_overrides({"hub_freight_cost_per_m3": bad})


def test_get_hub_freight_lists_every_hub():
    rows = hubs.do_get_hub_freight(TradingConfig(hub_freight_cost_per_m3={str(RENS): 300.0}))
    assert [r["region_id"] for r in rows] == list(hubs.TRADE_HUBS)
    assert {r["region_id"]: r["freight_cost_per_m3"] for r in rows}[RENS] == 300.0
    assert {r["region_id"]: r["freight_cost_per_m3"] for r in rows}[JITA] is None
