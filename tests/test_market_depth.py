"""market_depth: listed sell volume up to the acceptable price, in tiers."""
from eve_trader.market_depth import SupplyTier, sell_price_tiers, supply_tiers


def _sell(price, volume):
    return {"price": price, "volume_remain": volume, "is_buy_order": False}


def test_only_orders_within_the_tolerance_count():
    orders = [_sell(100.0, 10), _sell(104.0, 5), _sell(106.0, 1000), {"price": 99.0, "volume_remain": 50,
                                                                         "is_buy_order": True}]
    tiers = sell_price_tiers(orders, 100.0, 0.05)
    assert sum(units for _, units in tiers) == 15


def test_tiers_are_cheapest_first_with_volume_weighted_prices():
    # Bands above the cheapest order (100): <=101, <=102, <=104, <=105.
    orders = [_sell(100.0, 10), _sell(101.0, 10), _sell(103.0, 5), _sell(104.9, 20)]
    tiers = sell_price_tiers(orders, 100.0, 0.05)
    assert tiers == [(100.5, 20), (103.0, 5), (104.9, 20)]


def test_a_wide_tolerance_keeps_cheap_orders_in_their_own_band():
    orders = [_sell(10.0, 100), _sell(50.0, 100)]
    tiers = sell_price_tiers(orders, 10.0, 10.0)
    assert tiers[0] == (10.0, 100)


def test_orders_below_the_reference_are_included():
    tiers = sell_price_tiers([_sell(90.0, 3), _sell(100.0, 7)], 100.0, 0.05)
    assert sum(units for _, units in tiers) == 10
    assert tiers[0][0] == 90.0


def test_no_reference_price_means_no_depth():
    assert sell_price_tiers([_sell(1.0, 1)], None, 0.05) == []


def test_supply_tiers_apply_the_landed_cost():
    tiers = supply_tiers([_sell(10.0, 4)], 10.0, 0.05, lambda price: price * 2 + 1, source="Jita", hub_region_id=1)
    assert tiers == [SupplyTier(unit_cost=21.0, max_units=4, source="Jita", hub_region_id=1)]
    assert tiers[0].depth_checked
