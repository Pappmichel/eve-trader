"""fetch_character_market_orders (eve_trader/esi_data/fetchers.py) must
exclude corp-placed orders (is_corporation: true) from a character's own
sell-orders sync - T1-04 adjacent gap, independent challenge pass,
2026-09-26. No Postgres needed: storage.replace_sell_orders is
monkeypatched to capture what rows would be written, rather than actually
writing them."""
from eve_trader.esi_data import fetchers


class _FakeClient:
    def __init__(self, orders):
        self._orders = orders

    def character_orders(self, character_id, auth_role):
        return self._orders


def test_corp_placed_orders_are_excluded_from_character_sell_orders(monkeypatch):
    captured = {}

    def _fake_replace_sell_orders(rows, **kwargs):
        captured["rows"] = rows
        captured["kwargs"] = kwargs

    monkeypatch.setattr(fetchers.storage, "replace_sell_orders", _fake_replace_sell_orders)

    orders = [
        {"order_id": 1, "type_id": 34, "location_id": 100, "region_id": 10000002,
         "volume_remain": 5, "is_buy_order": False, "is_corporation": False},  # genuinely personal
        {"order_id": 2, "type_id": 35, "location_id": 100, "region_id": 10000002,
         "volume_remain": 3, "is_buy_order": False, "is_corporation": True},  # corp-funded via this character
        {"order_id": 3, "type_id": 36, "location_id": 100, "region_id": 10000002,
         "volume_remain": 1, "is_buy_order": True, "is_corporation": False},  # buy order, filtered by _sell_order_rows anyway
    ]
    client = _FakeClient(orders)

    result = fetchers.fetch_character_market_orders(client, 42, "seller:42", "Alice")

    order_ids_written = [row[0] for row in captured["rows"]]
    assert order_ids_written == [1]  # neither the corp order nor the buy order
    assert result["written"] == 1


def test_character_with_no_corp_orders_is_unaffected(monkeypatch):
    captured = {}
    monkeypatch.setattr(fetchers.storage, "replace_sell_orders",
                         lambda rows, **kw: captured.update(rows=rows))

    orders = [
        {"order_id": 1, "type_id": 34, "location_id": 100, "region_id": 10000002,
         "volume_remain": 5, "is_buy_order": False, "is_corporation": False},
    ]
    fetchers.fetch_character_market_orders(_FakeClient(orders), 42, "seller:42", "Alice")

    assert [row[0] for row in captured["rows"]] == [1]
