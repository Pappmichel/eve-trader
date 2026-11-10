"""do_plan_ore_import / do_plan_module_import (Import tab). Storage and ESI are
monkeypatched - no Postgres, no network."""
import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.module_reprocessing import actions as module_actions
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig
from eve_trader.module_reprocessing.models import ModuleCandidate
from eve_trader.refining import actions
from eve_trader.refining.config import RefiningConfig
from eve_trader.refining.models import OreCandidate

TRIT, PYE = 34, 35
VELDSPAR = 28430
RIFLE = 2000

_SDE = {
    TRIT: (TRIT, 18, "Tritanium", 0.01, 1, None, 0, None, 1),
    PYE: (PYE, 18, "Pyerite", 0.01, 1, None, 0, None, 1),
    VELDSPAR: (VELDSPAR, 1, "Compressed Veldspar", 1.0, 1, None, 0, None, 1),
    RIFLE: (RIFLE, 7, "Railgun", 5.0, 1, None, 0, None, 1),
}
_MATERIALS = {VELDSPAR: [(TRIT, 100.0)], RIFLE: [(TRIT, 1000.0), (PYE, 100.0)]}

_STATS = {
    VELDSPAR: OrderStats(sell_percentile=50.0, sell_volume=1000.0, buy_percentile=None, buy_volume=0.0),
    RIFLE: OrderStats(sell_percentile=200.0, sell_volume=40.0, buy_percentile=None, buy_volume=0.0),
}
_MINERALS = {
    TRIT: OrderStats(sell_percentile=1.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0),
    PYE: OrderStats(sell_percentile=4.0, sell_volume=1e9, buy_percentile=None, buy_volume=0.0),
}
_BOOKS = {
    # 500 units at 50, 500 more at 80 (above the margin cap), 1000 at 120.
    VELDSPAR: [{"price": 50.0, "volume_remain": 500, "is_buy_order": False},
               {"price": 80.0, "volume_remain": 500, "is_buy_order": False},
               {"price": 120.0, "volume_remain": 1000, "is_buy_order": False}],
    RIFLE: [{"price": 200.0, "volume_remain": 40, "is_buy_order": False}],
}


class _Client:
    def __init__(self, *a, **kw):
        pass

    def region_order_stats_bulk(self, region_id, type_ids, **kw):
        return {t: _STATS[t] for t in type_ids if t in _STATS}

    def region_orders_raw_bulk(self, region_id, type_ids, **kw):
        return {t: _BOOKS[t] for t in type_ids if t in _BOOKS}

    def structure_order_stats_bulk_or_goonmetrics(self, structure_id, type_ids, **kw):
        return {t: _MINERALS[t] for t in type_ids if t in _MINERALS}, False


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: _SDE.get(type_id))
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: (_SDE.get(type_id) or (None,) * 9)[8])
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: _MATERIALS.get(type_id, []))
    ore = [OreCandidate(type_id=VELDSPAR, item="Compressed Veldspar", family="Veldspar", is_ice=False, volume_m3=1.0)]
    monkeypatch.setattr(actions, "build_ore_candidate_universe", lambda: ore)
    monkeypatch.setattr(module_actions, "build_ore_candidate_universe", lambda: ore)
    monkeypatch.setattr(storage, "load_ore_shortlist",
                        lambda: [(VELDSPAR, "Compressed Veldspar", "Veldspar", False, True)])
    for mod in (actions, module_actions):
        monkeypatch.setattr(mod, "ESIClient", _Client)
        monkeypatch.setattr(mod, "TokenManager", lambda *a, **kw: object())
        monkeypatch.setattr(mod, "_seller_roles", lambda tm: [])
    monkeypatch.setattr(module_actions, "_module_shopping_candidates",
                        lambda: [ModuleCandidate(type_id=RIFLE, item="Railgun", volume_m3=5.0)])


def _cfgs():
    trading = TradingConfig(jita_buy_broker_fee=0.0, import_cost_per_m3=0.0, structure_sell_haircut=1.0,
                            min_margin_threshold=0.0, min_profit_threshold=0.0)
    refining = RefiningConfig(refining_tax_rate=0.0)
    return trading, refining


def test_ore_import_buys_only_orders_within_the_margin_cap(env):
    trading, refining = _cfgs()
    trading.min_margin_threshold = 0.10
    plan = actions.do_plan_ore_import(budget_isk=1e12, trading_cfg=trading, refining_cfg=refining)
    assert len(plan["lines"]) == 1
    line = plan["lines"][0]
    net = line["net_value"] / line["units"]
    # Only the 50 ISK orders clear landed <= net / 1.1 at Veldspar's yield.
    assert line["max_hub_price"] <= net / 1.10
    assert line["units"] <= line["units_within_cap"] < 2000
    assert plan["limited_by"] == "supply"
    assert plan["minerals"][0]["name"] == "Tritanium"
    assert plan["home_listed_known"] is True


def test_ore_import_respects_the_budget(env):
    trading, refining = _cfgs()
    plan = actions.do_plan_ore_import(budget_isk=5_000.0, trading_cfg=trading, refining_cfg=refining)
    assert plan["total_cost"] <= 5_000.0 + 1e-6
    assert plan["limited_by"] == "budget"


def test_ore_import_needs_a_limit(env):
    trading, refining = _cfgs()
    with pytest.raises(ActionError):
        actions.do_plan_ore_import(trading_cfg=trading, refining_cfg=refining)


def test_module_import_mixes_modules_and_ore(env):
    trading, refining = _cfgs()
    cfg = ModuleReprocessingConfig(freight_cost_per_m3=0.0, refining_tax_rate=0.0,
                                   scrapmetal_processing_skill_level=5, min_margin_threshold=0.0,
                                   min_profit_threshold=0.0)
    plan = module_actions.do_plan_module_import(volume_m3=10_000.0, cfg=cfg, trading_cfg=trading,
                                                refining_cfg=refining)
    categories = {line["category"] for line in plan["lines"]}
    assert "module" in categories
    rifle = next(line for line in plan["lines"] if line["category"] == "module")
    assert rifle["units"] <= 40
    assert plan["total_volume_m3"] <= 10_000.0
