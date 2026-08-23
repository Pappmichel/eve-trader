"""Tests for the Mineral Shopping List do_* actions (GitHub issue #93) in
eve_trader/refining/actions.py. Storage and ESI are monkeypatched throughout -
no Postgres, no network (same pattern as test_refining_pricing.py).
"""
import pytest
import requests

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production.config import ProductionConfig
from eve_trader.refining import actions
from eve_trader.refining.config import RefiningConfig
from eve_trader.refining.models import OreCandidate

TRIT, PYE = 34, 35
VELDSPAR = 28430

_SDE = {
    TRIT: (TRIT, 18, "Tritanium", 0.01, 1, None, 0, None, 1),
    PYE: (PYE, 18, "Pyerite", 0.01, 1, None, 0, None, 1),
    VELDSPAR: (VELDSPAR, 1, "Compressed Veldspar", 0.15, 1, None, 0, None, 100),
}


@pytest.fixture
def sde(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: _SDE.get(type_id))
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: (_SDE.get(type_id) or (None,) * 9)[8])
    monkeypatch.setattr(storage, "get_type_materials",
                        lambda type_id: [(TRIT, 415.0), (PYE, 10.0)] if type_id == VELDSPAR else [])
    return _SDE


@pytest.fixture
def candidates(monkeypatch):
    universe = [OreCandidate(type_id=VELDSPAR, item="Compressed Veldspar", family="Veldspar",
                              is_ice=False, volume_m3=0.15)]
    monkeypatch.setattr(actions, "build_ore_candidate_universe", lambda: universe)
    return universe


@pytest.fixture
def esi(monkeypatch):
    """Stubs both the token manager and the Jita regional order book. The
    shopping list deliberately needs no logged-in character (public regional
    endpoint only) - this fixture proves the action never asks for one."""
    stats = {
        VELDSPAR: OrderStats(sell_percentile=10.0, sell_volume=1e6, buy_percentile=None, buy_volume=0.0),
        TRIT: OrderStats(sell_percentile=6.0, sell_volume=1e9, buy_percentile=None, buy_volume=0.0),
        PYE: OrderStats(sell_percentile=12.0, sell_volume=1e9, buy_percentile=None, buy_volume=0.0),
    }

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def region_order_stats_bulk(self, region_id, type_ids, **kw):
            return {tid: stats[tid] for tid in type_ids if tid in stats}

    class _TM:
        def __init__(self, *a, **kw):
            pass

        def list_roles(self, role):  # pragma: no cover - must never be called
            raise AssertionError("the shopping list must not need a logged-in character")

    monkeypatch.setattr(actions, "ESIClient", _Client)
    monkeypatch.setattr(actions, "TokenManager", _TM)
    return stats


@pytest.fixture
def cfgs():
    return (TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, jita_region_id=10000002),
            RefiningConfig(refining_tax_rate=0.0, reprocessing_skill_level=0,
                           reprocessing_efficiency_skill_level=0))


# ------------------------------------------------------------------- saving
def test_save_requirements_replaces_the_whole_list(monkeypatch, sde):
    saved = {}
    monkeypatch.setattr(storage, "replace_mineral_requirements", lambda rows: saved.update(rows=list(rows)))

    result = actions.do_save_mineral_requirements([{"type_id": TRIT, "required_qty": 1000}])

    assert result == {"saved": 1}
    assert saved["rows"] == [(TRIT, "Tritanium", 1000.0)]


def test_save_requirements_resolves_the_name_from_the_sde_not_the_caller(monkeypatch, sde):
    saved = {}
    monkeypatch.setattr(storage, "replace_mineral_requirements", lambda rows: saved.update(rows=list(rows)))
    actions.do_save_mineral_requirements([{"type_id": TRIT, "name": "Tritanuim (typo)", "required_qty": 5}])
    assert saved["rows"][0][1] == "Tritanium"


def test_save_requirements_rejects_a_non_positive_quantity(sde):
    with pytest.raises(ActionError, match="greater than 0"):
        actions.do_save_mineral_requirements([{"type_id": TRIT, "required_qty": 0}])


def test_save_requirements_rejects_an_unknown_type(sde):
    with pytest.raises(ActionError, match="Refresh SDE"):
        actions.do_save_mineral_requirements([{"type_id": 999999, "required_qty": 5}])


def test_save_requirements_rejects_a_duplicate_mineral(sde):
    with pytest.raises(ActionError, match="listed twice"):
        actions.do_save_mineral_requirements([{"type_id": TRIT, "required_qty": 5},
                                               {"type_id": TRIT, "required_qty": 7}])


def test_save_requirements_rejects_a_malformed_entry(sde):
    with pytest.raises(ActionError, match="numeric type_id"):
        actions.do_save_mineral_requirements([{"required_qty": 5}])


def test_save_empty_list_clears_the_requirements(monkeypatch, sde):
    saved = {}
    monkeypatch.setattr(storage, "replace_mineral_requirements", lambda rows: saved.update(rows=list(rows)))
    assert actions.do_save_mineral_requirements([]) == {"saved": 0}
    assert saved["rows"] == []


def test_load_requirements_maps_storage_tuples(monkeypatch):
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [(TRIT, "Tritanium", 1000.0)])
    assert actions.do_load_mineral_requirements() == [
        {"type_id": TRIT, "name": "Tritanium", "required_qty": 1000.0}]


# ------------------------------------------------------- refinable minerals
def test_list_refinable_minerals_comes_from_real_sde_materials(sde, candidates):
    assert actions.do_list_refinable_minerals() == [
        {"type_id": PYE, "name": "Pyerite"}, {"type_id": TRIT, "name": "Tritanium"}]


# --------------------------------------------------------------- optimizing
def test_optimize_uses_the_saved_requirements_by_default(monkeypatch, sde, candidates, esi, cfgs):
    trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [(TRIT, "Tritanium", 41_500.0)])

    plan = actions.do_optimize_mineral_shopping_list(trading_cfg=trading_cfg, refining_cfg=refining_cfg)

    # 100 units of Veldspar (1 portion) refines to floor(415 x 50%) = 207 Trit
    # at level-0 skills in an unrigged station, so this needs ~201 portions.
    assert plan["ore_purchases"][0]["type_id"] == VELDSPAR
    assert plan["ore_purchases"][0]["units"] == plan["ore_purchases"][0]["portions"] * 100
    coverage = {c["type_id"]: c for c in plan["coverage"]}
    assert coverage[TRIT]["delivered"] >= 41_500


def test_optimize_accepts_an_ad_hoc_list_without_persisting_it(monkeypatch, sde, candidates, esi, cfgs):
    trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(storage, "replace_mineral_requirements",
                        lambda rows: (_ for _ in ()).throw(AssertionError("must not persist")))
    monkeypatch.setattr(storage, "load_mineral_requirements",
                        lambda: (_ for _ in ()).throw(AssertionError("must not read the saved list")))

    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": TRIT, "name": "Tritanium", "required_qty": 1000}],
        trading_cfg=trading_cfg, refining_cfg=refining_cfg)

    assert plan["total_cost"] > 0


def test_optimize_buys_the_mineral_directly_when_that_is_cheaper(monkeypatch, sde, candidates, esi, cfgs):
    """Veldspar is a terrible Pyerite source (one 1000-ISK portion yields a
    handful of Pyerite, ~200 ISK/unit) against a 12-ISK Jita sell price, so
    the plan must buy Pyerite outright rather than refine for it."""
    trading_cfg, refining_cfg = cfgs
    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": PYE, "name": "Pyerite", "required_qty": 100}],
        trading_cfg=trading_cfg, refining_cfg=refining_cfg)
    assert [p["type_id"] for p in plan["direct_purchases"]] == [PYE]
    assert plan["ore_purchases"] == []


def test_optimize_applies_the_refining_tax_as_reduced_yield(monkeypatch, sde, candidates, esi, cfgs):
    """A 100% refining tax means ore delivers nothing, so the only way left to
    cover the requirement is buying the mineral outright."""
    trading_cfg, _ = cfgs
    taxed = RefiningConfig(refining_tax_rate=1.0, reprocessing_skill_level=0,
                            reprocessing_efficiency_skill_level=0)
    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": TRIT, "name": "Tritanium", "required_qty": 1000}],
        trading_cfg=trading_cfg, refining_cfg=taxed)
    assert plan["ore_purchases"] == []
    assert [p["quantity"] for p in plan["direct_purchases"]] == [1000]


def test_optimize_with_no_requirements_raises(monkeypatch, sde, candidates, esi, cfgs):
    trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [])
    with pytest.raises(ActionError, match="No mineral requirements yet"):
        actions.do_optimize_mineral_shopping_list(trading_cfg=trading_cfg, refining_cfg=refining_cfg)


def test_optimize_with_an_empty_sde_candidate_universe_raises(monkeypatch, sde, esi, cfgs):
    trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(actions, "build_ore_candidate_universe", list)
    with pytest.raises(ActionError, match="Refresh SDE"):
        actions.do_optimize_mineral_shopping_list(
            requirements=[{"type_id": TRIT, "name": "Tritanium", "required_qty": 10}],
            trading_cfg=trading_cfg, refining_cfg=refining_cfg)


def test_optimize_surfaces_an_unsourceable_mineral_as_an_action_error(monkeypatch, sde, candidates, esi, cfgs):
    trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: _SDE.get(type_id) or (type_id, 18, "Morphite", 0.01, 1, None, 0, None, 1))
    with pytest.raises(ActionError, match="No way to source"):
        actions.do_optimize_mineral_shopping_list(
            requirements=[{"type_id": 11399, "name": "Morphite", "required_qty": 10}],
            trading_cfg=trading_cfg, refining_cfg=refining_cfg)


def test_optimize_includes_haul_cost_in_both_ore_and_mineral_prices(monkeypatch, sde, candidates, esi):
    """Both sides of the buy-vs-refine comparison are landed at C-J, via the
    one shared pricing.landed_cost_per_unit formula - not raw Jita prices."""
    trading_cfg = TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=1000.0, jita_region_id=10000002)
    refining_cfg = RefiningConfig(refining_tax_rate=0.0, reprocessing_skill_level=0,
                                   reprocessing_efficiency_skill_level=0)
    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": PYE, "name": "Pyerite", "required_qty": 100}],
        trading_cfg=trading_cfg, refining_cfg=refining_cfg)
    # Pyerite: 12 ISK + 0.01 m3 x 1000 ISK/m3 = 22 ISK landed.
    assert plan["direct_purchases"][0]["landed_cost_per_unit"] == pytest.approx(22.0)


# --------------------------------------------------- home-market comparison
def test_optimize_prefers_a_cheaper_home_market_price_over_jita(monkeypatch, sde, candidates, esi, cfgs):
    """GitHub issue #102: the direct-mineral alternative used to check Jita
    only, so a mineral already sitting cheaper at C-J (no haul needed) never
    got recommended."""
    trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(actions, "GoonmetricsClient", lambda cfg: type(
        "_GM", (), {"current_prices": staticmethod(lambda market: [CurrentPrice(type_id=PYE, updated="", buy=1.0, sell=3.0)])})())

    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": PYE, "name": "Pyerite", "required_qty": 100}],
        trading_cfg=trading_cfg, refining_cfg=refining_cfg,
        production_cfg=ProductionConfig(home_market="c-j"))

    # Home (3 ISK) beats Jita (12 ISK) - the plan must use it and say so.
    assert plan["direct_purchases"][0]["landed_cost_per_unit"] == pytest.approx(3.0)
    assert plan["direct_purchases"][0]["source"] == "Home"


def test_optimize_falls_back_to_jita_when_home_market_has_no_listing(monkeypatch, sde, candidates, esi, cfgs):
    trading_cfg, refining_cfg = cfgs
    monkeypatch.setattr(actions, "GoonmetricsClient", lambda cfg: type(
        "_GM", (), {"current_prices": staticmethod(lambda market: [])})())

    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": PYE, "name": "Pyerite", "required_qty": 100}],
        trading_cfg=trading_cfg, refining_cfg=refining_cfg,
        production_cfg=ProductionConfig(home_market="c-j"))

    assert plan["direct_purchases"][0]["landed_cost_per_unit"] == pytest.approx(12.0)
    assert plan["direct_purchases"][0]["source"] == "Jita"


def test_optimize_skips_the_home_market_check_when_unconfigured(monkeypatch, sde, candidates, esi, cfgs):
    trading_cfg, refining_cfg = cfgs

    def _must_not_be_called(cfg):
        raise AssertionError("must not construct a GoonmetricsClient when home_market is unset")
    monkeypatch.setattr(actions, "GoonmetricsClient", _must_not_be_called)

    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": PYE, "name": "Pyerite", "required_qty": 100}],
        trading_cfg=trading_cfg, refining_cfg=refining_cfg,
        production_cfg=ProductionConfig(home_market=None))

    assert plan["direct_purchases"][0]["source"] == "Jita"


def test_optimize_degrades_to_jita_only_on_a_goonmetrics_failure(monkeypatch, sde, candidates, esi, cfgs):
    """A Goonmetrics outage must not break the whole shopping list - only the
    ESI order-book fetch (which the function genuinely can't proceed
    without) is a hard failure; the home-market check is best-effort."""
    trading_cfg, refining_cfg = cfgs

    def _raise(market):
        raise requests.exceptions.ConnectionError("boom")
    monkeypatch.setattr(actions, "GoonmetricsClient",
                        lambda cfg: type("_GM", (), {"current_prices": staticmethod(_raise)})())

    plan = actions.do_optimize_mineral_shopping_list(
        requirements=[{"type_id": PYE, "name": "Pyerite", "required_qty": 100}],
        trading_cfg=trading_cfg, refining_cfg=refining_cfg,
        production_cfg=ProductionConfig(home_market="c-j"))

    assert plan["direct_purchases"][0]["landed_cost_per_unit"] == pytest.approx(12.0)
    assert plan["direct_purchases"][0]["source"] == "Jita"
