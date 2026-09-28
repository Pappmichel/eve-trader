"""Tests for Module Reprocessing's Mineral Shopping List do_* actions in
eve_trader/module_reprocessing/actions.py - ore/ice AND module/drone sources
in one plan. Mirrors test_refining_shopping_list_actions.py's pattern:
storage, ESI and Goonmetrics are monkeypatched throughout - no Postgres, no
network.
"""
import pytest
import requests

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.module_reprocessing import actions
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig
from eve_trader.refining.config import RefiningConfig
from eve_trader.refining.models import OreCandidate

TRIT, PYE, MEX = 34, 35, 36
VELDSPAR = 28430
AUTOCANNON = 9071      # a module that reprocesses into Mexallon only (test fixture)
INACTIVE_MODULE = 9072  # deactivated on the shortlist - must never be used

_SDE = {
    TRIT: (TRIT, 18, "Tritanium", 0.01, 1, None, 0, None, 1),
    PYE: (PYE, 18, "Pyerite", 0.01, 1, None, 0, None, 1),
    MEX: (MEX, 18, "Mexallon", 0.01, 1, None, 0, None, 1),
    VELDSPAR: (VELDSPAR, 1, "Compressed Veldspar", 0.15, 1, None, 0, None, 100),
    AUTOCANNON: (AUTOCANNON, 55, "200mm AutoCannon I", 5.0, 1, None, 0, None, 1),
    INACTIVE_MODULE: (INACTIVE_MODULE, 55, "Inactive Module", 5.0, 1, None, 0, None, 1),
}
_MATERIALS = {
    VELDSPAR: [(TRIT, 415.0), (PYE, 10.0)],
    AUTOCANNON: [(MEX, 100.0)],
    INACTIVE_MODULE: [(MEX, 1000.0), (11399, 5.0)],
}


@pytest.fixture
def sde(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: _SDE.get(type_id))
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {t: _SDE.get(t) for t in type_ids})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: (_SDE.get(type_id) or (None,) * 9)[8])
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: _MATERIALS.get(type_id, []))
    monkeypatch.setattr(storage, "get_type_materials_bulk",
                        lambda type_ids: {t: _MATERIALS.get(t, []) for t in type_ids})
    return _SDE


@pytest.fixture
def candidates(monkeypatch):
    universe = [OreCandidate(type_id=VELDSPAR, item="Compressed Veldspar", family="Veldspar",
                              is_ice=False, volume_m3=0.15)]
    monkeypatch.setattr(actions, "build_ore_candidate_universe", lambda: universe)
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist",
                        lambda: [(AUTOCANNON, "200mm AutoCannon I", True),
                                 (INACTIVE_MODULE, "Inactive Module", False)])
    return universe


@pytest.fixture
def esi(monkeypatch):
    """Stubs the token manager and the regional order book, recording every
    (region_id, type_ids) call. Needs no logged-in character."""
    stats = {
        VELDSPAR: OrderStats(sell_percentile=10.0, sell_volume=1e6, buy_percentile=None, buy_volume=0.0),
        AUTOCANNON: OrderStats(sell_percentile=60.0, sell_volume=1e3, buy_percentile=None, buy_volume=0.0),
        INACTIVE_MODULE: OrderStats(sell_percentile=0.01, sell_volume=1e3, buy_percentile=None, buy_volume=0.0),
        TRIT: OrderStats(sell_percentile=6.0, sell_volume=1e9, buy_percentile=None, buy_volume=0.0),
        PYE: OrderStats(sell_percentile=12.0, sell_volume=1e9, buy_percentile=None, buy_volume=0.0),
        MEX: OrderStats(sell_percentile=100.0, sell_volume=1e9, buy_percentile=None, buy_volume=0.0),
    }
    calls = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def region_order_stats_bulk(self, region_id, type_ids, **kw):
            calls.append((region_id, list(type_ids)))
            return {tid: stats[tid] for tid in type_ids if tid in stats}

    class _TM:
        def __init__(self, *a, **kw):
            pass

        def list_roles(self, role):  # pragma: no cover - must never be called
            raise AssertionError("the shopping list must not need a logged-in character")

    monkeypatch.setattr(actions, "ESIClient", _Client)
    monkeypatch.setattr(actions, "TokenManager", _TM)
    return calls


@pytest.fixture
def cfgs():
    # structure_id/structure_market_slug unset keeps every test but the
    # dedicated home-market ones from constructing a real GoonmetricsClient.
    return (ModuleReprocessingConfig(scrapmetal_processing_skill_level=0, refining_tax_rate=0.0,
                                      freight_cost_per_m3=0.0, purchase_region_id=10000002),
            TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, jita_region_id=10000002,
                          structure_id=None, structure_market_slug=None),
            RefiningConfig(refining_tax_rate=0.0, reprocessing_skill_level=0,
                           reprocessing_efficiency_skill_level=0))


def _optimize(cfgs, requirements, **overrides):
    cfg, trading_cfg, refining_cfg = cfgs
    kwargs = {"cfg": cfg, "trading_cfg": trading_cfg, "refining_cfg": refining_cfg, **overrides}
    return actions.do_optimize_module_shopping_list(requirements=requirements, **kwargs)


def _gm(**methods):
    return lambda cfg: type("_GM", (), {k: staticmethod(v) for k, v in methods.items()})()


# ------------------------------------------------------------------- saving
def test_save_requirements_replaces_the_whole_list_in_its_own_table(monkeypatch, sde):
    saved = {}
    monkeypatch.setattr(storage, "replace_module_shopping_requirements",
                        lambda rows: saved.update(rows=list(rows)))
    monkeypatch.setattr(storage, "replace_mineral_requirements",
                        lambda rows: (_ for _ in ()).throw(AssertionError("must not touch Ore & Minerals' list")))

    result = actions.do_save_module_shopping_requirements([{"type_id": TRIT, "required_qty": 1000}])

    assert result == {"saved": 1}
    assert saved["rows"] == [(TRIT, "Tritanium", 1000.0)]


def test_save_requirements_resolves_the_name_from_the_sde_not_the_caller(monkeypatch, sde):
    saved = {}
    monkeypatch.setattr(storage, "replace_module_shopping_requirements",
                        lambda rows: saved.update(rows=list(rows)))
    actions.do_save_module_shopping_requirements([{"type_id": TRIT, "name": "Tritanuim (typo)", "required_qty": 5}])
    assert saved["rows"][0][1] == "Tritanium"


def test_save_requirements_rejects_a_non_positive_quantity(sde):
    with pytest.raises(ActionError, match="greater than 0"):
        actions.do_save_module_shopping_requirements([{"type_id": TRIT, "required_qty": 0}])


def test_save_requirements_rejects_an_unknown_type(sde):
    with pytest.raises(ActionError, match="Refresh SDE"):
        actions.do_save_module_shopping_requirements([{"type_id": 999999, "required_qty": 5}])


def test_save_requirements_rejects_a_duplicate_mineral(sde):
    with pytest.raises(ActionError, match="listed twice"):
        actions.do_save_module_shopping_requirements([{"type_id": TRIT, "required_qty": 5},
                                                       {"type_id": TRIT, "required_qty": 7}])


def test_save_requirements_rejects_a_malformed_entry(sde):
    with pytest.raises(ActionError, match="numeric type_id"):
        actions.do_save_module_shopping_requirements([{"required_qty": 5}])


def test_load_requirements_maps_storage_tuples(monkeypatch):
    monkeypatch.setattr(storage, "load_module_shopping_requirements", lambda: [(TRIT, "Tritanium", 1000.0)])
    assert actions.do_load_module_shopping_requirements() == [
        {"type_id": TRIT, "name": "Tritanium", "required_qty": 1000.0}]


# ------------------------------------------------------- shoppable minerals
def test_list_shoppable_minerals_unions_ore_and_active_module_yields(sde, candidates):
    """Veldspar yields Trit/Pye, the active autocannon Mexallon; the inactive
    module's Morphite (11399) must not appear."""
    assert actions.do_list_shoppable_minerals() == [
        {"type_id": MEX, "name": "Mexallon"}, {"type_id": PYE, "name": "Pyerite"},
        {"type_id": TRIT, "name": "Tritanium"}]


# --------------------------------------------------------------- optimizing
def test_optimize_uses_the_saved_requirements_by_default(monkeypatch, sde, candidates, esi, cfgs):
    cfg, trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(storage, "load_module_shopping_requirements", lambda: [(TRIT, "Tritanium", 41_500.0)])

    plan = actions.do_optimize_module_shopping_list(cfg=cfg, trading_cfg=trading_cfg, refining_cfg=refining_cfg)

    assert plan["reprocess_purchases"][0]["type_id"] == VELDSPAR
    assert plan["reprocess_purchases"][0]["category"] == "ore"
    assert plan["reprocess_purchases"][0]["family"] == "Veldspar"
    coverage = {c["type_id"]: c for c in plan["coverage"]}
    assert coverage[TRIT]["delivered"] >= 41_500
    assert "from_reprocessing" in coverage[TRIT]


def test_optimize_accepts_an_ad_hoc_list_without_persisting_it(monkeypatch, sde, candidates, esi, cfgs):
    monkeypatch.setattr(storage, "replace_module_shopping_requirements",
                        lambda rows: (_ for _ in ()).throw(AssertionError("must not persist")))
    monkeypatch.setattr(storage, "load_module_shopping_requirements",
                        lambda: (_ for _ in ()).throw(AssertionError("must not read the saved list")))

    plan = _optimize(cfgs, [{"type_id": TRIT, "name": "Tritanium", "required_qty": 1000}])

    assert plan["total_cost"] > 0


def test_optimize_combines_ore_and_modules_in_one_plan(sde, candidates, esi, cfgs):
    """Veldspar is the only Tritanium source; the autocannon (60 ISK for
    floor(100 x 50%) = 50 Mexallon, 1.2 ISK/unit) beats buying Mexallon at
    100 ISK - so the plan has to use both kinds at once."""
    plan = _optimize(cfgs, [{"type_id": TRIT, "name": "Tritanium", "required_qty": 41_500},
                            {"type_id": MEX, "name": "Mexallon", "required_qty": 500}])

    by_category = {p["category"]: p for p in plan["reprocess_purchases"]}
    assert set(by_category) == {"ore", "module"}
    module = by_category["module"]
    assert (module["type_id"], module["family"], module["is_ice"]) == (AUTOCANNON, None, False)
    assert module["units"] == module["portions"] == 10
    assert plan["direct_purchases"] == []
    assert plan["reprocess_cost"] == pytest.approx(sum(p["total_cost"] for p in plan["reprocess_purchases"]))


def test_optimize_never_uses_an_inactive_shortlist_module(sde, candidates, esi, cfgs):
    """The inactive module would be absurdly cheap Mexallon (0.01 ISK for 500)
    - it must still stay out, and out of the ESI fetch too."""
    plan = _optimize(cfgs, [{"type_id": MEX, "name": "Mexallon", "required_qty": 500}])

    assert INACTIVE_MODULE not in {p["type_id"] for p in plan["reprocess_purchases"]}
    assert all(INACTIVE_MODULE not in ids for _region, ids in esi)


def test_optimize_prices_modules_with_this_tools_own_freight(sde, candidates, esi, cfgs):
    cfg, trading_cfg, refining_cfg = cfgs
    cfg.freight_cost_per_m3 = 2.0  # 5 m3 x 2 ISK = 10 ISK freight on a 60 ISK module
    plan = _optimize(cfgs, [{"type_id": MEX, "name": "Mexallon", "required_qty": 500}])
    module = next(p for p in plan["reprocess_purchases"] if p["category"] == "module")
    assert module["landed_cost_per_unit"] == pytest.approx(70.0)


def test_optimize_applies_the_module_tax_as_reduced_yield(sde, candidates, esi, cfgs):
    """A 100% reprocessing tax means modules deliver nothing, so Mexallon has
    to be bought outright."""
    cfg, _, _ = cfgs
    cfg.refining_tax_rate = 1.0
    plan = _optimize(cfgs, [{"type_id": MEX, "name": "Mexallon", "required_qty": 500}])
    assert plan["reprocess_purchases"] == []
    assert [p["quantity"] for p in plan["direct_purchases"]] == [500]


def test_optimize_fetches_modules_from_the_purchase_region_when_it_differs(sde, candidates, esi, cfgs):
    cfg, _, _ = cfgs
    cfg.purchase_region_id = 10000043  # Domain, not Jita
    _optimize(cfgs, [{"type_id": MEX, "name": "Mexallon", "required_qty": 500}])

    by_region = dict(esi)
    assert set(by_region) == {10000002, 10000043}
    assert by_region[10000043] == [AUTOCANNON]
    assert AUTOCANNON not in by_region[10000002]


def test_optimize_uses_one_bulk_call_when_both_regions_are_jita(sde, candidates, esi, cfgs):
    _optimize(cfgs, [{"type_id": MEX, "name": "Mexallon", "required_qty": 500}])
    assert len(esi) == 1
    assert {VELDSPAR, AUTOCANNON, MEX} <= set(esi[0][1])


def test_optimize_works_with_an_empty_module_shortlist(monkeypatch, sde, candidates, esi, cfgs):
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [])
    plan = _optimize(cfgs, [{"type_id": TRIT, "name": "Tritanium", "required_qty": 1000}])
    assert {p["category"] for p in plan["reprocess_purchases"]} <= {"ore"}


def test_optimize_with_no_requirements_raises(monkeypatch, sde, candidates, esi, cfgs):
    cfg, trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(storage, "load_module_shopping_requirements", lambda: [])
    with pytest.raises(ActionError, match="No mineral requirements yet"):
        actions.do_optimize_module_shopping_list(cfg=cfg, trading_cfg=trading_cfg, refining_cfg=refining_cfg)


def test_optimize_with_no_sources_at_all_raises(monkeypatch, sde, esi, cfgs):
    monkeypatch.setattr(actions, "build_ore_candidate_universe", list)
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [])
    with pytest.raises(ActionError, match="Refresh SDE"):
        _optimize(cfgs, [{"type_id": TRIT, "name": "Tritanium", "required_qty": 10}])


def test_optimize_surfaces_an_unsourceable_mineral_as_an_action_error(monkeypatch, sde, candidates, esi, cfgs):
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: _SDE.get(type_id) or (type_id, 18, "Morphite", 0.01, 1, None, 0, None, 1))
    with pytest.raises(ActionError, match="No way to source"):
        _optimize(cfgs, [{"type_id": 11399, "name": "Morphite", "required_qty": 10}])


def test_optimize_wraps_an_esi_transport_failure(monkeypatch, sde, candidates, esi, cfgs):
    class _Broken:
        def __init__(self, *a, **kw):
            pass

        def region_order_stats_bulk(self, *a, **kw):
            raise requests.exceptions.ConnectionError("boom")
    monkeypatch.setattr(actions, "ESIClient", _Broken)
    with pytest.raises(ActionError, match="order book"):
        _optimize(cfgs, [{"type_id": TRIT, "name": "Tritanium", "required_qty": 10}])


# --------------------------------------------------- home-market comparison
def test_optimize_prefers_station_id_home_prices_when_structure_id_is_set(monkeypatch, sde, candidates, esi, cfgs):
    _, trading_cfg, _ = cfgs
    trading_cfg.structure_id = 1_000_000_000_001
    trading_cfg.structure_market_slug = "c-j"
    seen = {}

    def _station(station_id, type_ids):
        seen["station"] = (station_id, set(type_ids))
        return {PYE: CurrentPrice(type_id=PYE, updated="", buy=1.0, sell=3.0)}

    def _slug(market):  # pragma: no cover - must never be called
        raise AssertionError("must prefer structure_id over the slug")
    monkeypatch.setattr(actions, "GoonmetricsClient", _gm(station_current_prices=_station, current_prices=_slug))

    plan = _optimize(cfgs, [{"type_id": PYE, "name": "Pyerite", "required_qty": 100}])

    assert seen["station"] == (1_000_000_000_001, {PYE})
    assert plan["direct_purchases"][0]["landed_cost_per_unit"] == pytest.approx(3.0)
    assert plan["direct_purchases"][0]["source"] == "Home"


def test_optimize_falls_back_to_the_market_slug_without_a_structure_id(monkeypatch, sde, candidates, esi, cfgs):
    _, trading_cfg, _ = cfgs
    trading_cfg.structure_market_slug = "c-j"
    monkeypatch.setattr(actions, "GoonmetricsClient", _gm(
        current_prices=lambda market: [CurrentPrice(type_id=PYE, updated="", buy=1.0, sell=3.0)]))

    plan = _optimize(cfgs, [{"type_id": PYE, "name": "Pyerite", "required_qty": 100}])

    assert plan["direct_purchases"][0]["source"] == "Home"


def test_optimize_skips_the_home_market_check_when_unconfigured(monkeypatch, sde, candidates, esi, cfgs):
    def _must_not_be_called(cfg):
        raise AssertionError("must not construct a GoonmetricsClient without structure_id/slug")
    monkeypatch.setattr(actions, "GoonmetricsClient", _must_not_be_called)

    plan = _optimize(cfgs, [{"type_id": PYE, "name": "Pyerite", "required_qty": 100}])

    assert plan["direct_purchases"][0]["landed_cost_per_unit"] == pytest.approx(12.0)
    assert plan["direct_purchases"][0]["source"] == "Jita"


def test_optimize_degrades_to_jita_only_on_a_goonmetrics_failure(monkeypatch, sde, candidates, esi, cfgs):
    _, trading_cfg, _ = cfgs
    trading_cfg.structure_id = 1_000_000_000_001

    def _raise(station_id, type_ids):
        raise requests.exceptions.ConnectionError("boom")
    monkeypatch.setattr(actions, "GoonmetricsClient", _gm(station_current_prices=_raise))

    plan = _optimize(cfgs, [{"type_id": PYE, "name": "Pyerite", "required_qty": 100}])

    assert plan["direct_purchases"][0]["source"] == "Jita"
