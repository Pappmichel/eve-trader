"""Assembled ships and their fittings are not stock - the pure/live-path
side of the rule (storage.is_fitted_location_flag, mark_assembled_or_fitted
and the in-memory readers that consume it). The synced-table side is
covered against Postgres in test_storage_stock.py."""
import pytest

from eve_trader import own_orders, storage
from eve_trader.production import actions as production_actions

HULL = 990101
MODULE = 990201
LOCATION_ID = 1000000000001


@pytest.fixture(autouse=True)
def _ships_are_hull(monkeypatch):
    monkeypatch.setattr(storage, "_ship_type_ids", lambda type_ids: {t for t in type_ids if t == HULL})


@pytest.mark.parametrize("flag,expected", [
    ("HiSlot0", True), ("MedSlot7", True), ("LoSlot3", True), ("RigSlot2", True),
    ("SubSystemSlot1", True), ("ServiceSlot0", True), ("FighterTube4", True),
    ("DroneBay", True), ("FighterBay", True),
    ("Cargo", False), ("Hangar", False), ("CorpSAG1", False), ("FleetHangar", False),
    ("ShipHangar", False), ("Unlocked", False), (None, False),
])
def test_is_fitted_location_flag(flag, expected):
    assert storage.is_fitted_location_flag(flag) is expected


def test_mark_assembled_or_fitted_flags_assembled_hulls_and_fittings_only():
    assets = storage.mark_assembled_or_fitted([
        {"type_id": HULL, "location_flag": "Hangar", "is_singleton": True},
        {"type_id": HULL, "location_flag": "Hangar", "is_singleton": False},
        {"type_id": MODULE, "location_flag": "HiSlot0", "is_singleton": True},
        {"type_id": MODULE, "location_flag": "Cargo", "is_singleton": False},
        {"type_id": MODULE, "location_flag": "Hangar", "is_singleton": True},
    ])
    assert [a["assembled_or_fitted"] for a in assets] == [True, False, True, False, False]


def test_esi_stock_from_asset_rows_skips_assembled_or_fitted():
    rows = [
        {"type_id": HULL, "location_flag": "Hangar", "resolved_location_id": LOCATION_ID,
         "quantity": 1, "assembled_or_fitted": True},
        {"type_id": HULL, "location_flag": "Hangar", "resolved_location_id": LOCATION_ID,
         "quantity": 3, "assembled_or_fitted": False},
    ]
    assert storage.esi_stock_from_asset_rows(rows, [HULL], LOCATION_ID) == {HULL: 3}


def test_unlisted_stock_accumulation_ignores_assembled_ship_and_fitting():
    out: dict[int, float] = {}
    production_actions._accumulate_stock_at_location([
        {"item_id": 1, "type_id": HULL, "location_id": LOCATION_ID, "location_flag": "Hangar",
         "quantity": 1, "is_singleton": True},
        {"item_id": 2, "type_id": HULL, "location_id": LOCATION_ID, "location_flag": "Hangar",
         "quantity": 2, "is_singleton": False},
    ], LOCATION_ID, out)
    assert out == {HULL: 2}


class _Client:
    def __init__(self, assets):
        self._assets = assets

    def character_assets(self, character_id, auth_role):
        return [dict(a) for a in self._assets]

    def character_orders(self, character_id, auth_role):
        return []


def _live_trading_assets(monkeypatch):
    import eve_trader.esi_data.access as access
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: True)
    monkeypatch.setattr(access, "read_esi", lambda *a, **k: [])  # empty snapshot -> live fetch


def test_trading_seller_unlisted_ignores_assembled_ship(monkeypatch):
    _live_trading_assets(monkeypatch)
    monkeypatch.setattr(own_orders, "fetch_own_sell_orders", lambda *a, **k: {})

    class Cfg:
        structure_id = LOCATION_ID

    client = _Client([
        {"type_id": HULL, "location_id": LOCATION_ID, "location_flag": "Hangar", "quantity": 1, "is_singleton": True},
    ])
    assert own_orders.fetch_seller_stock_without_order_pooled([(1, "seller:1")], client, {HULL}, Cfg()) == []


def test_trading_buyer_covered_ignores_assembled_ship(monkeypatch):
    _live_trading_assets(monkeypatch)
    monkeypatch.setattr(own_orders, "_hub_station_ids", lambda region_id: frozenset({60003760}))

    class Cfg:
        jita_region_id = 10000002
        structure_id = LOCATION_ID

    client = _Client([
        {"type_id": HULL, "location_id": 60003760, "location_flag": "Hangar", "quantity": 1, "is_singleton": True},
        {"type_id": MODULE, "location_id": 60003760, "location_flag": "Hangar", "quantity": 5, "is_singleton": False},
    ])
    assert own_orders.fetch_buyer_already_covered(1, "buyer:1", client, Cfg()) == {MODULE}
