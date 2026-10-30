"""Doctrine Shopping List buyer sources: the buyers' hangars and open buy
orders in the hub region (doctrine/buyer_sources.py) and how
engine.shopping_list splits a shortfall across them."""
from __future__ import annotations

import pytest

from eve_trader.auth import TokenRecord
from eve_trader.doctrine import buyer_sources, engine
from eve_trader.doctrine.buyer_sources import BuyerStock
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.doctrine.constants import SEVERITY_CRITICAL
from eve_trader.doctrine.models import AggregatedStockpileRow
from eve_trader.esi_client import ESIError
from eve_trader.locations.reader import LocationInfo
from eve_trader.production import esi_sync as production_esi_sync

MODULE = 2048
OTHER = 3001
BUYER = 91
NOT_BUYER = 92
FORGE = 10000002
JITA_44 = 60003760
PERIMETER_TTT = 1042508032148     # structure: registry knows no system
AMARR_8 = 60008494
CJ = 1035466617946                # stockpile structure


class _Tokens:
    def list_records(self):
        return [TokenRecord("esi:91", BUYER, "Jason Andven", "", "", 0.0, "")]


def _asset(owner_id, type_id, qty, location=JITA_44, **extra):
    row = {"owner_type": "character", "owner_id": owner_id, "type_id": type_id, "quantity": qty,
           "resolved_location_id": location, "resolved_hangar_flag": "Hangar", "location_flag": "Hangar",
           "assembled_or_fitted": False}
    row.update(extra)
    return row


def _describe(ids):
    regions = {JITA_44: FORGE, AMARR_8: 10000043}
    return {i: LocationInfo(location_id=i, region_id=regions.get(i)) for i in ids}


def test_hub_hangar_counts_buyers_in_hub_region_and_order_locations(monkeypatch):
    rows = [
        _asset(BUYER, MODULE, 3),                                  # Jita 4-4 (SDE region)
        _asset(BUYER, MODULE, 4, location=PERIMETER_TTT),          # structure known from an order
        _asset(BUYER, MODULE, 2, assembled_or_fitted=True),        # fitted - not stock
        _asset(BUYER, MODULE, 7, location=AMARR_8),                # other region
        _asset(BUYER, MODULE, 5, location=CJ),                     # stockpile location - counted there
        _asset(NOT_BUYER, MODULE, 11),                             # not a configured buyer
    ]
    monkeypatch.setattr(buyer_sources, "read_esi", lambda kind, tool: rows)
    monkeypatch.setattr(buyer_sources.reader, "describe", _describe)

    stock = buyer_sources.hub_hangar_stock([MODULE], {BUYER}, {FORGE}, {PERIMETER_TTT}, CJ)

    assert stock == {MODULE: 7.0}


def test_hub_regions_all_hubs_means_every_trade_hub():
    from eve_trader import hubs
    assert buyer_sources.hub_regions(DoctrineConfig(hub_region_id=hubs.ALL_HUBS)) == set(hubs.TRADE_HUBS)
    assert buyer_sources.hub_regions(DoctrineConfig()) == {FORGE}


class _Client:
    def __init__(self, orders=None, fail=False):
        self.orders, self.fail = orders or [], fail

    def character_orders(self, character_id, auth_role):
        if self.fail:
            raise ESIError("boom")
        return self.orders


def _share(monkeypatch, shared=True, role="esi:91"):
    monkeypatch.setattr(buyer_sources, "is_shared", lambda *a: shared)
    monkeypatch.setattr(buyer_sources, "select_auth_role", lambda *a, **k: role)


def _order(type_id, qty, location, region=FORGE, buy=True):
    return {"is_buy_order": buy, "location_id": location, "region_id": region, "type_id": type_id,
            "volume_remain": qty}


def test_open_buy_orders_sums_buy_orders_anywhere_in_hub_region(monkeypatch):
    _share(monkeypatch)
    client = _Client([
        _order(MODULE, 4, PERIMETER_TTT),
        _order(MODULE, 1, JITA_44),
        _order(MODULE, 9, JITA_44, buy=False),            # sell order - only marks the location
        _order(MODULE, 9, AMARR_8, region=10000043),      # other region
        _order(OTHER, 9, JITA_44),                        # not wanted
    ])

    result = buyer_sources.open_buy_orders([MODULE], {BUYER}, {FORGE}, _Tokens(), client)

    assert result.by_type == {MODULE: 5}
    assert result.hub_locations == {PERIMETER_TTT, JITA_44}
    assert result.warnings == []


@pytest.mark.parametrize("shared,role,client,needle", [
    (False, "esi:91", _Client(), "Market Orders are not shared"),
    (True, None, _Client(), "no token holds"),
    (True, "esi:91", _Client(fail=True), "could not be read"),
])
def test_open_buy_orders_warns_instead_of_failing(monkeypatch, shared, role, client, needle):
    _share(monkeypatch, shared=shared, role=role)

    result = buyer_sources.open_buy_orders([MODULE], {BUYER}, {FORGE}, _Tokens(), client)

    assert result.by_type == {}
    assert len(result.warnings) == 1 and needle in result.warnings[0] and "Jason Andven" in result.warnings[0]


def test_collect_is_off_without_buyers():
    assert buyer_sources.collect([MODULE], DoctrineConfig()) is None


def test_collect_passes_order_locations_and_stockpile_to_hangar(monkeypatch):
    seen = {}
    monkeypatch.setattr(buyer_sources, "TokenManager", lambda *a: _Tokens())
    monkeypatch.setattr(buyer_sources, "is_shared", lambda *a: True)
    monkeypatch.setattr(buyer_sources, "open_buy_orders", lambda *a, **k: buyer_sources.BuyOrders(
        by_type={MODULE: 2.0}, hub_locations={PERIMETER_TTT}))

    def _hangar(type_ids, buyer_ids, regions, known, exclude):
        seen.update(known=known, exclude=exclude, regions=regions)
        return {MODULE: 1.0}
    monkeypatch.setattr(buyer_sources, "hub_hangar_stock", _hangar)
    cfg = DoctrineConfig(stockpile_location_id=CJ, buyer_character_ids=(BUYER,))

    result = buyer_sources.collect([MODULE], cfg)

    assert (result.hub_hangar, result.on_order, result.warnings) == ({MODULE: 1.0}, {MODULE: 2.0}, [])
    assert seen == {"known": {PERIMETER_TTT}, "exclude": CJ, "regions": {FORGE}}


# ------------------------------------------------------------ engine split
class _FakePlanContext:
    def __init__(self, cfg, extra_type_ids=(), **kwargs):
        self.home, self.jita = {}, {}
        self.cost_indices, self.adjusted_prices, self.selected_decryptors = {}, {}, {}


class _FakeESI:
    def __init__(self, *a, **k):
        pass


def _stub_engine(monkeypatch, shortfalls: dict[int, float], buyer: BuyerStock | None):
    agg = [AggregatedStockpileRow(type_id=t, type_name=str(t), required_total=s, available=0.0, shortfall=s,
                                  severity=SEVERITY_CRITICAL, fitting_count=1) for t, s in shortfalls.items()]
    monkeypatch.setattr(engine, "stockpile_rows_for_doctrine", lambda doctrine_id, cfg: ([], True))
    monkeypatch.setattr(engine, "aggregate_stockpile_rows", lambda rows: agg)
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(engine, "unit_cost_detail", lambda *a, **k: (None, 10.0, None))
    monkeypatch.setattr(engine, "_haul_volume", lambda *a: 1.0)
    monkeypatch.setattr(engine.hubs, "hub_pricing", lambda *a, **k: engine.hubs.HubPricing())
    monkeypatch.setattr(production_esi_sync, "list_capability_characters", lambda *a, **k: [])
    monkeypatch.setattr(engine, "ESIClient", _FakeESI)
    monkeypatch.setattr(engine.buyer_sources, "collect", lambda type_ids, cfg: buyer)


def test_shopping_list_covers_shortfall_with_hangar_then_orders(monkeypatch):
    buyer = BuyerStock(hub_hangar={MODULE: 3.0, OTHER: 50.0}, on_order={MODULE: 10.0}, warnings=["w"])
    _stub_engine(monkeypatch, {MODULE: 6.0, OTHER: 4.0}, buyer)

    rows, warnings = engine.shopping_list(cfg=DoctrineConfig(buyer_character_ids=(BUYER,)))

    by_type = {r.type_id: r for r in rows}
    module = by_type[MODULE]
    assert (module.shortfall, module.hub_hangar, module.on_order, module.to_buy) == (6.0, 3.0, 3.0, 0.0)
    assert module.total_cost == 0.0
    other = by_type[OTHER]
    assert (other.hub_hangar, other.on_order, other.to_buy) == (4.0, 0.0, 0.0)
    assert warnings == ["w"]


def test_shopping_list_partial_cover_prices_only_to_buy(monkeypatch):
    _stub_engine(monkeypatch, {MODULE: 6.0}, BuyerStock(hub_hangar={MODULE: 1.0}, on_order={MODULE: 2.0}))

    rows, _ = engine.shopping_list(cfg=DoctrineConfig(buyer_character_ids=(BUYER,)))

    assert rows[0].to_buy == 3.0
    assert rows[0].recommended_source == "Build"
    assert rows[0].total_cost == pytest.approx(30.0)


def test_shopping_list_without_buyers_keeps_full_shortfall(monkeypatch):
    _stub_engine(monkeypatch, {MODULE: 6.0}, None)

    rows, warnings = engine.shopping_list()

    assert (rows[0].hub_hangar, rows[0].on_order, rows[0].to_buy) == (0.0, 0.0, 6.0)
    assert rows[0].total_cost == pytest.approx(60.0)
    assert warnings == []
