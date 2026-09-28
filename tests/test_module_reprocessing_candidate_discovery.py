"""Tests for eve_trader/module_reprocessing/candidate_discovery.py. Pure/no
Postgres needed - storage.module_reprocessing_candidate_types is
monkeypatched (the real SQL exclusion filter itself - category_id/
meta_group_id/rig-slot - is covered by tests/test_storage_module_
reprocessing.py, which needs a real Postgres connection)."""
import pytest

from eve_trader import storage
from eve_trader.config import TradingConfig
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.module_reprocessing.candidate_discovery import build_module_candidate_universe, discover_candidates
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig


def test_builds_candidates_from_storage_rows(monkeypatch):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [
        (100, "200mm AutoCannon I", 0.01),
        (200, "Small Shield Extender I", 0.005),
    ])
    candidates = build_module_candidate_universe()
    assert len(candidates) == 2
    assert candidates[0].type_id == 100
    assert candidates[0].item == "200mm AutoCannon I"
    assert candidates[0].volume_m3 == 0.01


def test_skips_rows_with_no_name_or_volume(monkeypatch):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [
        (1, "", 0.01),
        (2, "Some Module I", 0.0),
        (3, "Some Module II", None),
        (4, "Real Module I", 0.02),
    ])
    candidates = build_module_candidate_universe()
    assert len(candidates) == 1
    assert candidates[0].type_id == 4


def test_empty_universe_returns_empty_list(monkeypatch):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [])
    assert build_module_candidate_universe() == []


# ---------------------------------------------------------- discover_candidates
class FakeGoonmetricsClient:
    """Same shape as test_station_trading_discovery.py's own fake - avoids
    class-level monkeypatching of the real GoonmetricsClient."""
    def __init__(self, prices_by_market):
        self._prices_by_market = prices_by_market

    def current_prices(self, market):
        return self._prices_by_market.get(market, [])


def _price(type_id, buy, sell):
    return CurrentPrice(type_id=type_id, updated="", buy=buy, sell=sell)


@pytest.fixture
def trading_cfg():
    return TradingConfig(jita_buy_broker_fee=0.0147, structure_sell_haircut=0.9463,
                          structure_market_slug="my-structure")


def _setup_one_module(monkeypatch, type_id=100, volume_m3=0.01):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types",
                         lambda: [(type_id, "200mm AutoCannon I", volume_m3)])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_portion_size", lambda tid: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda tid: [(34, 100.0)])


def test_discover_candidates_includes_items_clearing_the_bar(monkeypatch, trading_cfg):
    _setup_one_module(monkeypatch)
    client = FakeGoonmetricsClient({
        "jita": [_price(100, buy=0.5, sell=1.0)],
        "my-structure": [_price(34, buy=9.0, sell=10.0)],
    })
    cfg = ModuleReprocessingConfig(min_profit_threshold=0.0, min_margin_threshold=0.05)

    result = discover_candidates(cfg, trading_cfg, client=client)

    assert [r.type_id for r in result] == [100]
    assert result[0].est_profit_per_unit > 0


def test_discover_candidates_excludes_items_below_the_margin_bar(monkeypatch, trading_cfg):
    _setup_one_module(monkeypatch)
    client = FakeGoonmetricsClient({
        "jita": [_price(100, buy=1000.0, sell=1000.0)],  # expensive, thin margin
        "my-structure": [_price(34, buy=0.01, sell=0.01)],
    })
    cfg = ModuleReprocessingConfig(min_profit_threshold=0.0, min_margin_threshold=0.05)

    assert discover_candidates(cfg, trading_cfg, client=client) == []


def test_discover_candidates_ignore_thresholds_bypasses_both_bars(monkeypatch, trading_cfg):
    """Same fixture as test_discover_candidates_excludes_items_below_the_margin_bar
    (thin margin, would normally be filtered out) - ignore_thresholds=True still
    includes it, since a priced estimate exists."""
    _setup_one_module(monkeypatch)
    client = FakeGoonmetricsClient({
        "jita": [_price(100, buy=1000.0, sell=1000.0)],  # expensive, thin margin
        "my-structure": [_price(34, buy=0.01, sell=0.01)],
    })
    cfg = ModuleReprocessingConfig(min_profit_threshold=0.0, min_margin_threshold=0.05, ignore_thresholds=True)

    result = discover_candidates(cfg, trading_cfg, client=client)

    assert [r.type_id for r in result] == [100]


def test_discover_candidates_ignore_thresholds_still_requires_a_priced_estimate(monkeypatch, trading_cfg):
    """ignore_thresholds bypasses the two numeric bars, not the underlying
    None-check - a candidate Goonmetrics/mineral pricing couldn't estimate at
    all still has nothing to sort/act on."""
    _setup_one_module(monkeypatch)

    class NoHomeMarket(FakeGoonmetricsClient):
        def current_prices(self, market):
            return [] if market == "my-structure" else super().current_prices(market)

    client = NoHomeMarket({"jita": [_price(100, buy=0.5, sell=1.0)]})
    cfg = ModuleReprocessingConfig(ignore_thresholds=True)

    assert discover_candidates(cfg, trading_cfg, client=client) == []


def test_discover_candidates_sorts_by_profit_descending(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [
        (100, "Cheap Module I", 0.01), (200, "Rich Module I", 0.01),
    ])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_portion_size", lambda tid: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda tid: [(34, 100.0)])
    client = FakeGoonmetricsClient({
        "jita": [_price(100, buy=1.0, sell=1.0), _price(200, buy=0.1, sell=0.1)],
        "my-structure": [_price(34, buy=10.0, sell=10.0)],
    })
    cfg = ModuleReprocessingConfig(min_profit_threshold=0.0, min_margin_threshold=0.0)

    result = discover_candidates(cfg, trading_cfg, client=client)

    assert [r.type_id for r in result] == [200, 100]  # cheaper buy -> higher profit, first


def test_discover_candidates_empty_universe_short_circuits(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [])
    assert discover_candidates(ModuleReprocessingConfig(), trading_cfg, client=FakeGoonmetricsClient({})) == []


def test_discover_candidates_respects_cap_when_enforced(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [
        (100, "Module A", 0.01), (200, "Module B", 0.01),
    ])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_portion_size", lambda tid: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda tid: [(34, 100.0)])
    client = FakeGoonmetricsClient({
        "jita": [_price(100, buy=1.0, sell=1.0), _price(200, buy=1.0, sell=1.0)],
        "my-structure": [_price(34, buy=10.0, sell=10.0)],
    })
    cfg = ModuleReprocessingConfig(min_profit_threshold=0.0, min_margin_threshold=0.0,
                                    enforce_shortlist_cap=True, max_active_shortlist_items=1)

    result = discover_candidates(cfg, trading_cfg, client=client)

    assert len(result) == 1


def test_discover_candidates_no_cap_by_default(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [
        (100, "Module A", 0.01), (200, "Module B", 0.01),
    ])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_portion_size", lambda tid: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda tid: [(34, 100.0)])
    client = FakeGoonmetricsClient({
        "jita": [_price(100, buy=1.0, sell=1.0), _price(200, buy=1.0, sell=1.0)],
        "my-structure": [_price(34, buy=10.0, sell=10.0)],
    })
    cfg = ModuleReprocessingConfig(min_profit_threshold=0.0, min_margin_threshold=0.0)  # enforce_shortlist_cap=False

    result = discover_candidates(cfg, trading_cfg, client=client)

    assert len(result) == 2


def test_discover_candidates_survives_home_market_outage(monkeypatch, trading_cfg):
    """The Jita dump succeeds but the C-J home-market dump fails - mineral
    values are just missing (est_mineral_value=None -> filtered out by the
    margin bar, not a hard failure), same "best-effort" reasoning refining/
    actions.py's do_optimize_mineral_shopping_list already uses for its own
    Goonmetrics home-market call."""
    import requests

    _setup_one_module(monkeypatch)

    class BoomingHomeMarket(FakeGoonmetricsClient):
        def current_prices(self, market):
            if market == "jita":
                return super().current_prices(market)
            raise requests.RequestException("Goonmetrics home market down")

    client = BoomingHomeMarket({"jita": [_price(100, buy=0.5, sell=1.0)]})
    cfg = ModuleReprocessingConfig(min_profit_threshold=0.0, min_margin_threshold=0.0)

    assert discover_candidates(cfg, trading_cfg, client=client) == []
