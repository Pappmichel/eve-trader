"""Total Wealth completeness and breakdown (PORTFOLIO_OVERVIEW_PLAN.md 3.6)."""
from eve_trader import portfolio
from eve_trader.config import TradingConfig
from eve_trader.portfolio_wealth import JITA_44, assemble


def _item(component, type_id, quantity, price, owner_id=1, location_id=10):
    return {
        "component": component, "owner_type": "character", "owner_id": owner_id,
        "location_id": location_id, "type_id": type_id, "quantity": quantity, "price": price,
    }


def test_assets_and_listed_orders_of_the_same_type_both_count():
    # ESI assets do not include listed items. 10 in the hangar plus 4 listed
    # is 14 units, not 10 and not 18.
    built = assemble(
        [_item("assets", 34, 10, 2.0), _item("listed", 34, 4, 2.0, location_id=JITA_44)],
        [], [],
    )
    assert built["components"]["assets"] == 20.0
    assert built["components"]["listed"] == 8.0
    assert built["total"] == 28.0
    assert sum(row["value"] for row in built["by_owner"]) == built["total"]


def test_unknown_escrow_is_left_out_of_the_total():
    built = assemble([_item("assets", 34, 10, 2.0)], [{"owner_type": "character", "owner_id": 1, "amount": 5.0}], None)
    assert built["components"]["escrow"] is None
    assert built["total"] == 25.0
    assert "Wallet" in {row["label"] for row in built["by_location"]}


def test_known_escrow_is_added_once():
    built = assemble(
        [], [],
        [{"owner_type": "character", "owner_id": 1, "amount": 40.0}],
    )
    assert built["components"]["escrow"] == 40.0
    assert built["total"] == 40.0


def test_breakdown_rows_sum_to_the_total_and_top_is_a_slice():
    items = [_item("assets", i, 1, float(100 - i), location_id=1000 + i) for i in range(30)]
    items.append(_item("contracts", 34, 2, 10.0, location_id=None))
    built = assemble(
        items,
        [{"owner_type": "character", "owner_id": 1, "amount": 50.0}],
        [{"owner_type": "character", "owner_id": 1, "amount": 25.0}],
        structure_id=60008494,
        category_names={i: "Material" for i in range(30)},
    )
    assert built["total"] == sum(item["quantity"] * item["price"] for item in items) + 75.0
    assert sum(row["value"] for row in built["by_owner"]) == built["total"]
    assert sum(row["value"] for row in built["by_location"]) == built["total"]
    assert sum(row["value"] for row in built["by_category"]) == built["total"]
    assert len(built["top"]) == 20
    assert sum(row["value"] for row in built["top"]) < built["total"]
    assert {row["label"] for row in built["by_location"]} >= {"Contracts", "Wallet"}


def test_cj_and_jita_stay_named_when_other_locations_are_grouped():
    built = assemble(
        [
            _item("assets", 34, 1, 1000.0, location_id=60008494),
            _item("listed", 35, 1, 1000.0, location_id=JITA_44),
            _item("assets", 36, 1, 10.0, location_id=99),
            _item("assets", 37, 1, 10.0, location_id=98),
        ],
        [], [],
        structure_id=60008494,
        location_names={99: "Amarr", 98: "Dodixie"},
    )
    labels = {row["label"] for row in built["by_location"]}
    assert "C-J" in labels
    assert "Jita" in labels
    assert "Other" in labels
    assert "Amarr" not in labels
    assert "Dodixie" not in labels


def test_unpriced_rows_are_excluded_and_listed():
    built = assemble(
        [_item("assets", 34, 5, None, location_id=JITA_44), _item("assets", 35, 1, 3.0)],
        [], [],
        type_names={34: "Tritanium"},
        owner_names={1: "Alice"},
    )
    assert built["total"] == 3.0
    assert built["unpriced_items"] == 1
    assert built["unpriced"] == [{
        "type_id": 34, "type_name": "Tritanium", "quantity": 5.0, "positions": 1,
        "owner_name": "Alice", "location_name": "Jita", "component": "assets",
    }]
    assert built["unpriced_types"] == 1
    assert sum(row["value"] for row in built["by_category"]) == 3.0


def test_unpriced_rows_are_one_per_type_and_capped():
    from eve_trader.portfolio_wealth import UNPRICED_LIMIT

    items = [
        _item("assets", 34, 5, None, location_id=JITA_44),
        _item("listed", 34, 2, None, location_id=99),
    ] + [_item("assets", 1000 + n, 1, None) for n in range(UNPRICED_LIMIT + 5)]
    built = assemble(items, [], [], type_names={34: "Tritanium"}, owner_names={1: "Alice"})
    assert built["unpriced_items"] == UNPRICED_LIMIT + 7
    assert built["unpriced_types"] == UNPRICED_LIMIT + 6
    assert len(built["unpriced"]) == UNPRICED_LIMIT
    first = built["unpriced"][0]
    assert (first["type_id"], first["quantity"], first["positions"]) == (34, 7.0, 2)
    assert first["location_name"] == "2 locations"
    assert first["component"] == "2 components"
    assert first["owner_name"] == "Alice"


def test_orders_not_shared_with_portfolio_add_nothing(monkeypatch):
    from tests.test_portfolio import _FakeTokenManager, _FakeTokenRecord, _stub_sharing
    import eve_trader.auth as auth_module
    import eve_trader.storage as storage

    _stub_sharing(monkeypatch, assets_character=[1])
    monkeypatch.setattr(storage, "load_assets_grouped", lambda c, o: [("character", 1, 10, 34, 10)])
    monkeypatch.setattr(storage, "load_blueprints_grouped", lambda c, o: [])
    monkeypatch.setattr(storage, "wallet_balances_by_owner", lambda c, o: [])
    monkeypatch.setattr(portfolio, "_priced", lambda ids: {34: 2.0})

    def _forbidden(*_a, **_kw):
        raise AssertionError("orders and contracts are not shared with portfolio")

    monkeypatch.setattr(storage, "load_sell_orders_grouped", _forbidden)
    monkeypatch.setattr(auth_module, "TokenManager", _FakeTokenManager([
        _FakeTokenRecord(character_id=1, character_name="Alice", scopes="esi-wallet.read_character_wallet.v1"),
    ]))

    result = portfolio.total_wealth(TradingConfig())

    assert result["wealth_assets_value"] == 20.0
    assert result["wealth_listed_value"] == 0.0
    assert result["wealth_contract_value"] == 0.0
    assert result["wealth_escrow"] == 0.0
    assert result["total_wealth"] == 20.0


def test_listed_quantity_is_added_to_assets_for_portfolio_owners_only(monkeypatch):
    from tests.test_portfolio import _FakeTokenManager, _FakeTokenRecord, _stub_sharing
    import eve_trader.auth as auth_module
    import eve_trader.storage as storage

    _stub_sharing(monkeypatch, assets_character=[1], market_orders_character=[1])
    monkeypatch.setattr(storage, "load_assets_grouped", lambda c, o: [("character", 1, 10, 34, 10)])
    monkeypatch.setattr(storage, "load_blueprints_grouped", lambda c, o: [])
    monkeypatch.setattr(storage, "wallet_balances_by_owner", lambda c, o: [])
    seen = {}

    def _listed(chars, corps):
        seen["owners"] = (chars, corps)
        return [("character", 1, JITA_44, 34, 4)]

    monkeypatch.setattr(storage, "load_sell_orders_grouped", _listed)
    monkeypatch.setattr("eve_trader.realized.db.open_buy_escrow_by_owner", lambda owners: [])
    monkeypatch.setattr(portfolio, "_priced", lambda ids: {34: 2.0})
    monkeypatch.setattr(auth_module, "TokenManager", _FakeTokenManager([
        _FakeTokenRecord(character_id=1, character_name="Alice", scopes="esi-wallet.read_character_wallet.v1"),
    ]))

    result = portfolio.total_wealth(TradingConfig())

    assert seen["owners"] == ([1], [])
    assert result["wealth_assets_value"] == 20.0
    assert result["wealth_listed_value"] == 8.0
    assert result["total_wealth"] == 28.0


def test_open_buys_without_escrow_leave_the_component_empty(monkeypatch):
    from tests.test_portfolio import _FakeTokenManager, _FakeTokenRecord, _stub_sharing
    import eve_trader.auth as auth_module
    import eve_trader.storage as storage

    _stub_sharing(monkeypatch, assets_character=[1], market_orders_character=[1])
    monkeypatch.setattr(storage, "load_assets_grouped", lambda c, o: [("character", 1, 10, 34, 10)])
    monkeypatch.setattr(storage, "load_blueprints_grouped", lambda c, o: [])
    monkeypatch.setattr(storage, "load_sell_orders_grouped", lambda c, o: [])
    monkeypatch.setattr(storage, "wallet_balances_by_owner", lambda c, o: [])
    monkeypatch.setattr(portfolio, "_priced", lambda ids: {34: 2.0})
    monkeypatch.setattr(
        "eve_trader.realized.db.open_buy_escrow_by_owner", lambda owners: None,
    )
    monkeypatch.setattr(auth_module, "TokenManager", _FakeTokenManager([
        _FakeTokenRecord(character_id=1, character_name="Alice", scopes="esi-wallet.read_character_wallet.v1"),
    ]))

    result = portfolio.total_wealth(TradingConfig())

    assert result["wealth_escrow"] is None
    assert result["total_wealth"] == 20.0
