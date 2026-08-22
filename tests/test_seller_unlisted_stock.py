from eve_trader import actions, storage
from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.config import TradingConfig
from eve_trader.models import ShortlistItem
from eve_trader.own_orders import fetch_seller_stock_without_order


class FakeClient:
    def __init__(self, assets, orders):
        self._assets = assets
        self._orders = orders

    def character_assets(self, character_id, auth_role):
        return self._assets

    def character_orders(self, character_id, auth_role):
        return self._orders


def _asset(type_id, quantity, location_id, location_flag="Hangar"):
    return {"type_id": type_id, "quantity": quantity, "location_id": location_id, "location_flag": location_flag}


def _sell_order(type_id, volume_remain, location_id):
    return {"type_id": type_id, "volume_remain": volume_remain, "location_id": location_id, "is_buy_order": False}


def test_finds_shortlist_stock_with_no_sell_order_at_all():
    cfg = TradingConfig()
    assets = [_asset(1, 100, cfg.structure_id)]
    client = FakeClient(assets, orders=[])

    rows = fetch_seller_stock_without_order(1, "seller", client, {1}, cfg)

    assert rows == [{"type_id": 1, "asset_quantity": 100, "sell_order_remaining": 0.0, "unlisted_quantity": 100.0}]


def test_partially_listed_stock_not_flagged():
    # Confirmed scope: only a *complete* absence of a sell order counts -
    # some coverage, even if less than the full stock, isn't flagged.
    cfg = TradingConfig()
    assets = [_asset(1, 100, cfg.structure_id)]
    orders = [_sell_order(1, 30, cfg.structure_id)]
    client = FakeClient(assets, orders)

    assert fetch_seller_stock_without_order(1, "seller", client, {1}, cfg) == []


def test_fully_listed_stock_not_flagged():
    cfg = TradingConfig()
    assets = [_asset(1, 100, cfg.structure_id)]
    orders = [_sell_order(1, 100, cfg.structure_id)]
    client = FakeClient(assets, orders)

    assert fetch_seller_stock_without_order(1, "seller", client, {1}, cfg) == []


def test_ignores_assets_at_other_locations():
    cfg = TradingConfig()
    assets = [_asset(1, 100, location_id=999999)]
    client = FakeClient(assets, orders=[])

    assert fetch_seller_stock_without_order(1, "seller", client, {1}, cfg) == []


def test_ignores_assets_not_on_shortlist():
    # Confirmed scope: only shortlist items are considered, not every asset
    # sitting at the structure.
    cfg = TradingConfig()
    assets = [_asset(1, 100, cfg.structure_id), _asset(2, 50, cfg.structure_id)]
    client = FakeClient(assets, orders=[])

    rows = fetch_seller_stock_without_order(1, "seller", client, {1}, cfg)

    assert rows == [{"type_id": 1, "asset_quantity": 100, "sell_order_remaining": 0.0, "unlisted_quantity": 100.0}]


def test_excludes_non_stock_location_flags():
    cfg = TradingConfig()
    assets = [
        _asset(1, 50, cfg.structure_id, location_flag="Hangar"),
        _asset(1, 500, cfg.structure_id, location_flag="AssetSafety"),
    ]
    client = FakeClient(assets, orders=[])

    rows = fetch_seller_stock_without_order(1, "seller", client, {1}, cfg)

    assert rows == [{"type_id": 1, "asset_quantity": 50, "sell_order_remaining": 0.0, "unlisted_quantity": 50.0}]


def test_do_check_seller_unlisted_stock_includes_deactivated_shortlist_items(monkeypatch):
    # Confirmed with the user: an item deactivated by the Skip-grace-period
    # prune or the 300-item cap should still be checked here - "not actively
    # tracked for re-pricing" isn't the same as "don't bother selling what
    # you already physically have".
    monkeypatch.setattr(storage, "load_shortlist", lambda: [
        ShortlistItem(item="Active Item", item_id=1, category="Material", volume_m3=1.0, active=True),
        ShortlistItem(item="Deactivated Item", item_id=2, category="Material", volume_m3=1.0, active=False),
    ])
    seller_record = TokenRecord(
        role="seller:99", character_id=99, character_name="Seller", access_token="x",
        refresh_token="y", expires_at=0.0, scopes="",
    )
    monkeypatch.setattr(TokenManager, "__init__", lambda self, cfg=None: setattr(self, "cfg", cfg))
    monkeypatch.setattr(TokenManager, "has_token", lambda self, role: True)
    monkeypatch.setattr(TokenManager, "list_roles", lambda self, prefix: ["seller:99"] if prefix == "seller" else [])
    monkeypatch.setattr(TokenManager, "get_record", lambda self, role: seller_record if role == "seller:99" else None)
    monkeypatch.setattr(TokenManager, "get_token", lambda self, role: seller_record)

    captured = {}

    def fake_fetch(sellers, client, shortlist_item_ids, cfg):
        captured["ids"] = shortlist_item_ids
        return []
    monkeypatch.setattr(actions.own_orders, "fetch_seller_stock_without_order_pooled", fake_fetch)

    actions.do_check_seller_unlisted_stock()

    assert captured["ids"] == {1, 2}
