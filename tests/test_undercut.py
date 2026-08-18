from eve_trader.config import TradingConfig
from eve_trader.own_orders import check_undercut


class FakeClient:
    def __init__(self, orders, structure_orders):
        self._orders = orders
        self._structure_orders = structure_orders

    def character_orders(self, character_id, auth_role):
        return self._orders

    def structure_orders_raw(self, structure_id, auth_role):
        return self._structure_orders


def _my_order(order_id, type_id, price, location_id):
    return {"order_id": order_id, "type_id": type_id, "price": price,
            "location_id": location_id, "is_buy_order": False}


def _book_order(order_id, type_id, price, is_buy_order=False):
    return {"order_id": order_id, "type_id": type_id, "price": price, "is_buy_order": is_buy_order}


def test_flags_order_beaten_by_a_cheaper_competitor():
    cfg = TradingConfig()
    my_orders = [_my_order(1, 100, 500.0, cfg.structure_id)]
    book = [_book_order(1, 100, 500.0), _book_order(2, 100, 450.0)]  # order_id 1 is mine, 2 is a competitor
    client = FakeClient(my_orders, book)

    result = check_undercut(1, "seller", client, cfg)

    assert result == [{"type_id": 100, "my_price": 500.0, "competitor_price": 450.0, "difference": 50.0}]


def test_not_flagged_when_my_price_is_still_best():
    cfg = TradingConfig()
    my_orders = [_my_order(1, 100, 400.0, cfg.structure_id)]
    book = [_book_order(1, 100, 400.0), _book_order(2, 100, 450.0)]
    client = FakeClient(my_orders, book)

    assert check_undercut(1, "seller", client, cfg) == []


def test_not_flagged_when_no_competing_orders_exist():
    cfg = TradingConfig()
    my_orders = [_my_order(1, 100, 400.0, cfg.structure_id)]
    book = [_book_order(1, 100, 400.0)]  # only my own order in the book
    client = FakeClient(my_orders, book)

    assert check_undercut(1, "seller", client, cfg) == []


def test_ignores_buy_orders_in_the_competitor_book():
    # A cheap buy order for the same type_id must never count as "competing"
    # with a sell order - different side of the market entirely.
    cfg = TradingConfig()
    my_orders = [_my_order(1, 100, 400.0, cfg.structure_id)]
    book = [_book_order(1, 100, 400.0), _book_order(2, 100, 1.0, is_buy_order=True)]
    client = FakeClient(my_orders, book)

    assert check_undercut(1, "seller", client, cfg) == []


def test_ignores_orders_at_other_locations():
    cfg = TradingConfig()
    my_orders = [_my_order(1, 100, 400.0, location_id=999999)]
    book = [_book_order(1, 100, 400.0), _book_order(2, 100, 350.0)]
    client = FakeClient(my_orders, book)

    assert check_undercut(1, "seller", client, cfg) == []


def test_uses_my_best_price_when_i_have_multiple_orders_for_the_same_item():
    cfg = TradingConfig()
    my_orders = [
        _my_order(1, 100, 500.0, cfg.structure_id),
        _my_order(2, 100, 480.0, cfg.structure_id),
    ]
    book = [_book_order(1, 100, 500.0), _book_order(2, 100, 480.0), _book_order(3, 100, 490.0)]
    client = FakeClient(my_orders, book)

    # My best (lowest) own price is 480 - a competitor at 490 doesn't undercut that.
    assert check_undercut(1, "seller", client, cfg) == []
