from eve_trader.station_trading.config import StationTradingConfig
from eve_trader.station_trading.undercut import check_buy_undercut_pooled, check_undercut_pooled

OTHER_JITA_STATION = 60003757  # Jita 4 - Moon 5 - Caldari Navy Assembly Plant - a real, different Jita station


class FakeClient:
    def __init__(self, orders_by_character, region_orders):
        self._orders_by_character = orders_by_character
        self._region_orders = region_orders

    def character_orders(self, character_id, auth_role):
        return self._orders_by_character[character_id]

    def region_orders_raw(self, region_id, type_id):
        return [o for o in self._region_orders if o["type_id"] == type_id]


def _order(order_id, type_id, price, location_id, is_buy_order=False):
    return {"order_id": order_id, "type_id": type_id, "price": price,
            "location_id": location_id, "is_buy_order": is_buy_order}


def _cfg():
    return StationTradingConfig()


def test_sell_side_flags_order_beaten_by_a_cheaper_competitor():
    cfg = _cfg()
    my_orders = [_order(1, 100, 500.0, cfg.station_id)]
    book = [_order(1, 100, 500.0, cfg.station_id), _order(2, 100, 450.0, cfg.station_id)]
    client = FakeClient({1: my_orders}, book)

    result = check_undercut_pooled([(1, "trader")], client, cfg)

    assert result == [{"type_id": 100, "my_price": 500.0, "competitor_price": 450.0, "difference": 50.0}]


def test_sell_side_not_flagged_when_my_price_is_still_best():
    cfg = _cfg()
    my_orders = [_order(1, 100, 400.0, cfg.station_id)]
    book = [_order(1, 100, 400.0, cfg.station_id), _order(2, 100, 450.0, cfg.station_id)]
    client = FakeClient({1: my_orders}, book)

    assert check_undercut_pooled([(1, "trader")], client, cfg) == []


def test_buy_side_flags_order_beaten_by_a_higher_competitor_bid():
    cfg = _cfg()
    my_orders = [_order(1, 100, 400.0, cfg.station_id, is_buy_order=True)]
    book = [_order(1, 100, 400.0, cfg.station_id, is_buy_order=True),
            _order(2, 100, 450.0, cfg.station_id, is_buy_order=True)]
    client = FakeClient({1: my_orders}, book)

    result = check_buy_undercut_pooled([(1, "trader")], client, cfg)

    assert result == [{"type_id": 100, "my_price": 400.0, "competitor_price": 450.0, "difference": 50.0}]


def test_buy_side_not_flagged_when_my_bid_is_still_best():
    cfg = _cfg()
    my_orders = [_order(1, 100, 450.0, cfg.station_id, is_buy_order=True)]
    book = [_order(1, 100, 450.0, cfg.station_id, is_buy_order=True),
            _order(2, 100, 400.0, cfg.station_id, is_buy_order=True)]
    client = FakeClient({1: my_orders}, book)

    assert check_buy_undercut_pooled([(1, "trader")], client, cfg) == []


def test_ignores_opposite_side_orders_in_the_competitor_book():
    # A cheap buy order for the same type_id must never count as "competing"
    # with a sell order - different side of the market entirely.
    cfg = _cfg()
    my_orders = [_order(1, 100, 400.0, cfg.station_id)]
    book = [_order(1, 100, 400.0, cfg.station_id), _order(2, 100, 1.0, cfg.station_id, is_buy_order=True)]
    client = FakeClient({1: my_orders}, book)

    assert check_undercut_pooled([(1, "trader")], client, cfg) == []


def test_ignores_orders_at_a_different_jita_station():
    cfg = _cfg()
    my_orders = [_order(1, 100, 400.0, cfg.station_id)]
    book = [_order(1, 100, 400.0, cfg.station_id), _order(2, 100, 350.0, OTHER_JITA_STATION)]
    client = FakeClient({1: my_orders}, book)

    assert check_undercut_pooled([(1, "trader")], client, cfg) == []


def test_pools_own_orders_across_multiple_trader_characters():
    cfg = _cfg()
    orders_by_character = {
        1: [_order(1, 100, 500.0, cfg.station_id)],
        2: [_order(2, 100, 480.0, cfg.station_id)],
    }
    book = [_order(1, 100, 500.0, cfg.station_id), _order(2, 100, 480.0, cfg.station_id),
            _order(3, 100, 490.0, cfg.station_id)]
    client = FakeClient(orders_by_character, book)

    # My best (lowest) own price across both characters is 480 - a
    # competitor at 490 doesn't undercut that.
    assert check_undercut_pooled([(1, "trader"), (2, "trader")], client, cfg) == []


def test_no_own_orders_returns_empty():
    cfg = _cfg()
    client = FakeClient({1: []}, [])
    assert check_undercut_pooled([(1, "trader")], client, cfg) == []
    assert check_buy_undercut_pooled([(1, "trader")], client, cfg) == []
