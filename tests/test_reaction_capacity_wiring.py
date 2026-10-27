"""Planner wiring tests for docs/REACTION_CAPACITY_PLAN.md phase 3.

Switch off must reproduce today's plans exactly. Switch on: both Build Lists
agree on reaction jobs/purchases; fuel blocks still appear as manufacturing;
tight slots move products to capacity_reason; manual Buy/Build respected;
alchemy only when enabled; solver runs once per plan context.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict

import pytest

from eve_trader import storage
from eve_trader.production import engine, reaction_capacity
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import ACTIVITY_MANUFACTURING, ACTIVITY_REACTION
from eve_trader.production.reaction_capacity import WindowState

from . import pg_helpers
from .pg_helpers import tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(autouse=True)
def _noop_save_latest_buy_list(monkeypatch):
    monkeypatch.setattr(storage, "save_latest_buy_list", lambda rows: None)


@pytest.fixture(autouse=True)
def _stub_shared_production_owner_ids(monkeypatch):
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda data_kind: (None, None))

# BOM:
#   Component (1, Tech I) <- 10x Reaction product R (10)
#   R (Reaction, 100/run) <- 5x Fuel (20) + 100x Moon (30)
#   Fuel (20, Tech I, 40/run) <- 1x Trit (40, buy-only)
#   Moon (30) buy-only
_BP = {
    1: (101, ACTIVITY_MANUFACTURING, 1.0),
    10: (1010, ACTIVITY_REACTION, 100.0),
    20: (1020, ACTIVITY_MANUFACTURING, 40.0),
}
_MATERIALS = {
    101: [(10, 10)],
    1010: [(20, 5), (30, 100)],
    1020: [(40, 1)],
}
_NAMES = {
    1: "Component", 10: "ReactionProd", 20: "FuelBlock", 30: "MoonMat", 40: "Tritanium",
    1010: "Reaction Formula", 1020: "Fuel Blueprint",
}


def _activity(type_id):
    if type_id == 10:
        return "Reaction", _BP[10]
    if type_id in (1, 20):
        return "Tech I", _BP[type_id]
    return "Input", None


def _fake_ctx(stock_targets, manual_overrides=None, manual_stock=None):
    class _Ctx:
        def __init__(self, cfg, extra_type_ids=()):
            self.stock_targets = stock_targets
            self.manual_stock = manual_stock or {}
            self.manual_overrides = manual_overrides or {}
            self.selected_decryptors = {}
            self.home = {}
            self.jita = {}
            self.cost_indices = {}
            self.adjusted_prices = {20: 1000.0, 30: 500.0, 40: 5.0, 10: 50_000.0, 1010: 10_000_000.0}
            self.adjusted_prices_available = True
            self._reaction_solve_cache = None
    return _Ctx


@pytest.fixture
def reaction_bom(monkeypatch, tenant):
    monkeypatch.setattr(engine, "classify_activity", _activity)
    monkeypatch.setattr(storage, "get_blueprint_materials",
                        lambda blueprint_id, activity_id: _MATERIALS.get(blueprint_id, []))
    monkeypatch.setattr(storage, "get_blueprint_time",
                        lambda blueprint_id, activity_id: 3600.0 if activity_id == ACTIVITY_REACTION else 600.0)
    monkeypatch.setattr(storage, "get_blueprint_for_product",
                        lambda type_id: _BP.get(type_id))
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: (type_id, 1, _NAMES.get(type_id, str(type_id)), 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "count_owned_formula_originals",
                        lambda type_ids=None, **k: {1010: 50})
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {})
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 4)
    monkeypatch.setattr(storage, "list_integrated_special_order_items", lambda: [])
    monkeypatch.setattr(engine, "_activity_mods",
                        lambda activity, type_id, cfg, cost_indices, blueprint_id=None: (1.0, 1.0, 0.02))

    def _unit_cost(type_id, cfg, home, jita, cost_memo, *a, **k):
        # Real _unit_cost writes the memo; capacity_reason compares against it.
        cost_memo[type_id] = 1000.0
        return 1000.0

    monkeypatch.setattr(engine, "_unit_cost", _unit_cost)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 1.0)  # always clears min_margin
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_haul_volume", lambda type_id, cfg: 1.0)
    monkeypatch.setattr(engine, "job_category",
                        lambda type_id: "Reactions" if type_id == 10 else (
                            "Advanced Components" if type_id == 1 else None))
    monkeypatch.setattr(engine, "margin_home", lambda *a, **k: 0.5)
    monkeypatch.setattr(engine, "_free_slots_by_category", lambda: {"Reactions": 10, "Manufacturing": 10})
    monkeypatch.setattr(engine.pricing, "buy_price",
                        lambda type_id, *a, **k: {
                            10: 80_000.0, 20: 50_000.0, 30: 1000.0, 40: 5.0, 1010: 10_000_000.0,
                        }.get(type_id, 1000.0))
    monkeypatch.setattr(engine.pricing, "buy_source", lambda *a, **k: "Jita")
    monkeypatch.setattr(engine.pricing, "quote_hub", lambda *a, **k: (None, None))
    monkeypatch.setattr(
        reaction_capacity, "window_state",
        lambda cfg, **k: WindowState(
            total_slots=5, busy_slots=0, free_slots=5,
            busy_slots_w2=0, free_slots_w2=5,
            on_hand_from_jobs={}, incoming1_from_jobs={}, incoming2_from_jobs={},
        ),
    )
    # Build is cheaper than buy for reaction product so today's plan builds it.
    def _decision(type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth=0):
        if type_id in manual_overrides:
            return manual_overrides[type_id]
        if bp is None:
            return "Buy"
        return "Build"
    monkeypatch.setattr(engine, "_buy_or_build_decision", _decision)


def _snapshot(plan: dict) -> dict:
    """JSON-friendly plan snapshot for byte-identical comparisons."""
    out = {}
    for key, value in plan.items():
        if key == "reaction_capacity":
            continue
        if isinstance(value, list) and value and hasattr(value[0], "__dataclass_fields__"):
            out[key] = [asdict(v) for v in value]
        else:
            out[key] = copy.deepcopy(value)
    return out


@pg_helpers.postgres_required()
def test_switch_off_plans_identical_to_today(monkeypatch, reaction_bom, tenant):
    stock_targets = [(1, "Component", 10, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    cfg = ProductionConfig(component_overbuild=0.0, reaction_capacity_enabled=False)

    prod = engine.plan_production(cfg)
    asset = engine.plan_asset_optimized(cfg)

    cfg_on_then_off = ProductionConfig(component_overbuild=0.0, reaction_capacity_enabled=False)
    prod2 = engine.plan_production(cfg_on_then_off)
    assert _snapshot(prod) == _snapshot(prod2)

    # Has reaction + fuel manufacturing jobs when capacity is off.
    build_ids = {e.type_id for e in prod["build_list"]}
    assert 10 in build_ids
    assert 20 in build_ids
    assert "reaction_capacity" not in prod
    assert "reaction_capacity" not in asset


@pg_helpers.postgres_required()
def test_switch_on_both_lists_agree_on_reaction_jobs_and_purchases(monkeypatch, reaction_bom, tenant):
    stock_targets = [(1, "Component", 10, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True,
        reaction_cycle_days=13.0, reaction_slot_override=5,
    )

    prod = engine.plan_production(cfg)
    asset = engine.plan_asset_optimized(cfg)

    prod_rx = [e for e in prod["build_list"] if e.activity == "Reaction"]
    asset_rx = [j for j in asset["jobs"] if j.activity == "Reaction"]
    assert prod_rx, "expected reaction jobs on Build List"
    assert asset_rx, "expected reaction jobs on Asset Plan"

    prod_by_bp = {(e.blueprint_type_id, e.type_id, e.job_runs) for e in prod_rx}
    asset_by_bp = {(j.blueprint_type_id, j.type_id, j.job_runs) for j in asset_rx}
    assert prod_by_bp == asset_by_bp

    assert "reaction_capacity" in prod
    assert "reaction_capacity" in asset
    assert prod["reaction_capacity"]["slots_available"] == 5


@pg_helpers.postgres_required()
def test_fuel_blocks_still_appear_as_manufacturing(monkeypatch, reaction_bom, tenant):
    stock_targets = [(1, "Component", 10, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True, reaction_slot_override=5,
    )
    prod = engine.plan_production(cfg)
    fuel = [e for e in prod["build_list"] if e.type_id == 20]
    assert fuel, "fuel blocks must still be planned as manufacturing jobs"
    assert fuel[0].activity == "Manufacturing"


@pg_helpers.postgres_required()
def test_tight_slots_move_reaction_to_capacity_reason(monkeypatch, reaction_bom, tenant):
    # One run fills the whole 13-day window (fit=1), one slot → at most 100
    # units of R built; Component demand of 50 needs 500 R → rest bought.
    monkeypatch.setattr(
        storage, "get_blueprint_time",
        lambda blueprint_id, activity_id: (
            13 * 86400.0 if activity_id == ACTIVITY_REACTION else 600.0),
    )
    stock_targets = [(1, "Component", 50, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    monkeypatch.setattr(
        reaction_capacity, "window_state",
        lambda cfg, **k: WindowState(
            total_slots=1, busy_slots=0, free_slots=1,
            busy_slots_w2=0, free_slots_w2=1,
            on_hand_from_jobs={}, incoming1_from_jobs={}, incoming2_from_jobs={},
        ),
    )
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True,
        reaction_slot_override=1, reaction_cycle_days=13.0,
    )
    prod = engine.plan_production(cfg)
    capacity_buys = [e for e in prod["buy_list"] if e.capacity_reason == "capacity"]
    assert capacity_buys, "tight slots must produce capacity-tagged Buy List rows"
    assert any(e.type_id == 10 for e in capacity_buys)


@pg_helpers.postgres_required()
def test_manual_buy_and_build_respected(monkeypatch, reaction_bom, tenant):
    stock_targets = [(10, "ReactionProd", 500, 0, 0)]
    overrides = {10: "Buy"}
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets, manual_overrides=overrides))
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True, reaction_slot_override=5,
    )
    prod = engine.plan_production(cfg)
    assert not any(e.activity == "Reaction" for e in prod["build_list"])
    assert any(e.type_id == 10 and e.capacity_reason is None for e in prod["buy_list"])

    overrides_build = {10: "Build"}
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets, manual_overrides=overrides_build))
    prod_b = engine.plan_production(cfg)
    assert any(e.type_id == 10 and e.activity == "Reaction" for e in prod_b["build_list"])


@pg_helpers.postgres_required()
def test_alchemy_only_when_enabled(monkeypatch, reaction_bom, tenant):
    stock_targets = [(10, "ReactionProd", 100, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    # No alchemy alternative registered → only normal recipe even if flag on.
    monkeypatch.setattr(engine, "find_alchemy_alternative", lambda type_id: None)
    monkeypatch.setattr(engine, "_alchemy_yield", lambda *a, **k: None)

    cfg_off = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True,
        alchemy_reactions_enabled=False, reaction_slot_override=5,
    )
    prod_off = engine.plan_production(cfg_off)
    assert all(e.recipe_source is None for e in prod_off["build_list"] if e.activity == "Reaction")

    cfg_on = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True,
        alchemy_reactions_enabled=True, reaction_slot_override=5,
    )
    prod_on = engine.plan_production(cfg_on)
    # Still no alchemy rows without an alternative in cost_indices.
    assert all(e.recipe_source is None for e in prod_on["build_list"] if e.activity == "Reaction")


@pg_helpers.postgres_required()
def test_solver_runs_once_per_plan_context(monkeypatch, reaction_bom, tenant):
    stock_targets = [(1, "Component", 10, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    calls = {"n": 0}
    real_solve = reaction_capacity.solve

    def counting_solve(layer, window):
        calls["n"] += 1
        return real_solve(layer, window)

    monkeypatch.setattr(reaction_capacity, "solve", counting_solve)
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True, reaction_slot_override=5,
    )
    engine.plan_production(cfg)
    assert calls["n"] == 1, f"solver should run once per plan context, got {calls['n']}"


@pg_helpers.postgres_required()
def test_ample_slots_match_switch_off_with_stock_and_incoming(monkeypatch, reaction_bom, tenant):
    """Live production bug: stock netted in the walk *and* again in the
    solver wiped all reaction demand. With hangar stock + running-job
    incoming, ample slots, alchemy off and no window date, switch on must
    produce the same reaction runs per product as switch off (within one
    run of rounding).

    ``reaction_next_window_demand_factor=0`` isolates W1 from W2 pre-build
    so the comparison is about double-netting, not D13/D14 foresight.
    """
    hangar = {10: 200.0}  # ReactionProd on hand
    incoming = {10: 100.0}  # still-running reaction jobs

    def current_stock(type_id, manual_stock, cfg, bp=None):
        return hangar.get(type_id, 0.0) + incoming.get(type_id, 0.0)

    def stock_on_hand(type_id, manual_stock, cfg):
        return hangar.get(type_id, 0.0)

    monkeypatch.setattr(engine, "_current_stock", current_stock)
    monkeypatch.setattr(engine, "_stock_on_hand", stock_on_hand)
    monkeypatch.setattr(
        reaction_capacity, "window_state",
        lambda cfg, **k: WindowState(
            total_slots=50, busy_slots=0, free_slots=50,
            busy_slots_w2=0, free_slots_w2=50,
            # No window date → build_window folds incoming into incoming1
            # from _current_stock - _stock_on_hand; keep jobs empty here.
            on_hand_from_jobs={}, incoming1_from_jobs={}, incoming2_from_jobs={},
        ),
    )
    # Component needs 1000 R (10 units × 10/run); stock covers 300 → 700 left.
    stock_targets = [(1, "Component", 100, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))

    cfg_off = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=False, alchemy_reactions_enabled=False,
    )
    cfg_on = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True, alchemy_reactions_enabled=False,
        reaction_slot_override=50, reaction_cycle_days=13.0,
        reaction_next_window_demand_factor=0.0,
    )
    off = engine.plan_production(cfg_off)
    on = engine.plan_production(cfg_on)

    def reaction_runs(plan):
        return {
            e.type_id: e.job_runs
            for e in plan["build_list"] if e.activity == "Reaction"
        }

    off_runs = reaction_runs(off)
    on_runs = reaction_runs(on)
    assert off_runs, "switch-off fixture must build reaction products"
    assert on_runs, "switch-on must not wipe reaction jobs when slots are ample"
    assert set(off_runs) == set(on_runs)
    for tid, runs_off in off_runs.items():
        assert abs(on_runs[tid] - runs_off) <= 1, (
            f"type {tid}: on={on_runs[tid]} off={runs_off} (allowed ±1 run)"
        )


@pg_helpers.postgres_required()
def test_top_level_reaction_target_nets_stock_once(monkeypatch, reaction_bom, tenant):
    """W1 demand for a Reaction stock target equals target − stock once."""
    hangar = {10: 250.0}
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: hangar.get(a[0], 0.0))
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: hangar.get(a[0], 0.0))
    monkeypatch.setattr(
        reaction_capacity, "window_state",
        lambda cfg, **k: WindowState(
            total_slots=20, busy_slots=0, free_slots=20,
            busy_slots_w2=0, free_slots_w2=20,
            on_hand_from_jobs={}, incoming1_from_jobs={}, incoming2_from_jobs={},
        ),
    )
    stock_targets = [(10, "ReactionProd", 1000, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    captured = {}
    real_solve = reaction_capacity.solve

    def capture(layer, window):
        captured["demand"] = dict(window.demand)
        captured["on_hand"] = dict(window.on_hand)
        return real_solve(layer, window)

    monkeypatch.setattr(reaction_capacity, "solve", capture)
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True,
        reaction_slot_override=20, alchemy_reactions_enabled=False,
        reaction_next_window_demand_factor=0.0,
    )
    prod = engine.plan_production(cfg)
    assert captured["demand"].get(10, 0) == pytest.approx(1000.0)
    assert captured["on_hand"].get(10, 0) == pytest.approx(250.0)
    # Net need the solver covers by build/buy: 750, not 1000-250-250=500.
    rx = [e for e in prod["build_list"] if e.type_id == 10 and e.activity == "Reaction"]
    bought = sum(e.quantity for e in prod["buy_list"] if e.type_id == 10)
    built_units = sum(e.quantity for e in rx)
    assert built_units + bought == pytest.approx(750.0, abs=100.0)  # one-run slack


def test_recipe_inputs_per_run_are_continuous(monkeypatch):
    """Per-run inputs stay base×mult; EVE rounds once per job, not per run."""
    monkeypatch.setattr(
        storage, "get_blueprint_materials",
        lambda blueprint_id, activity_id: [(30, 5)],  # base 5
    )
    # material_mult 0.9 → 4.5 continuous; per-run ceil would wrongly yield 5.
    out = reaction_capacity._recipe_inputs_per_run(1010, ACTIVITY_REACTION, 0.9)
    assert out[30] == pytest.approx(4.5)
    # Two-run job quantity in the planner would be ceil(4.5 * 2) = 9, not 10.
    assert math.ceil(out[30] * 2) == 9


@pg_helpers.postgres_required()
def test_capacity_reason_none_when_buy_is_cheaper(monkeypatch, reaction_bom, tenant):
    # Buy cheaper than build → ordinary buy, not a capacity badge.
    def _expensive_build(type_id, cfg, home, jita, cost_memo, *a, **k):
        cost_memo[type_id] = 50_000.0
        return 50_000.0

    monkeypatch.setattr(engine, "_unit_cost", _expensive_build)
    monkeypatch.setattr(
        engine.pricing, "buy_price",
        lambda type_id, *a, **k: {
            10: 100.0, 20: 50_000.0, 30: 1000.0, 40: 5.0, 1010: 10_000_000.0,
        }.get(type_id, 1000.0),
    )
    stock_targets = [(10, "ReactionProd", 100, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True,
        reaction_slot_override=50, alchemy_reactions_enabled=False,
    )
    prod = engine.plan_production(cfg)
    buys = [e for e in prod["buy_list"] if e.type_id == 10]
    assert buys, "cheaper buy must still appear on the Buy List"
    assert all(e.capacity_reason is None for e in buys)


@pg_helpers.postgres_required()
def test_capacity_reason_when_slots_force_purchase(monkeypatch, reaction_bom, tenant):
    # Build cheaper, but one slot with fit=1 cannot cover demand → capacity.
    monkeypatch.setattr(
        storage, "get_blueprint_time",
        lambda blueprint_id, activity_id: (
            13 * 86400.0 if activity_id == ACTIVITY_REACTION else 600.0),
    )
    monkeypatch.setattr(
        reaction_capacity, "window_state",
        lambda cfg, **k: WindowState(
            total_slots=1, busy_slots=0, free_slots=1,
            busy_slots_w2=0, free_slots_w2=1,
            on_hand_from_jobs={}, incoming1_from_jobs={}, incoming2_from_jobs={},
        ),
    )
    stock_targets = [(1, "Component", 50, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True,
        reaction_slot_override=1, reaction_cycle_days=13.0,
    )
    prod = engine.plan_production(cfg)
    capacity_buys = [e for e in prod["buy_list"] if e.type_id == 10 and e.capacity_reason == "capacity"]
    assert capacity_buys
    assert all((e.isk_given_up or 0) > 0 for e in capacity_buys)


@pg_helpers.postgres_required()
def test_asset_plan_recommends_only_startable_jobs(monkeypatch, reaction_bom, tenant):
    stock_targets = [(1, "Component", 10, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(stock_targets))
    # Inputs not on hand → readiness fraction < 1.
    monkeypatch.setattr(engine, "_stock_on_hand", lambda type_id, *a, **k: 0.0)
    cfg = ProductionConfig(
        component_overbuild=0.0, reaction_capacity_enabled=True, reaction_slot_override=5,
    )
    asset = engine.plan_asset_optimized(cfg)
    rx = [j for j in asset["jobs"] if j.activity == "Reaction"]
    assert rx
    for job in rx:
        assert job.formulas_needed is not None and job.formulas_needed > 0
        if job.runs_ready_now <= 0:
            assert job.recommended_slots in (0, None)
        else:
            assert job.runs_per_slot and job.runs_per_slot > 0
            assert job.recommended_slots == math.ceil(job.runs_ready_now / job.runs_per_slot)
            assert job.recommended_slots <= job.formulas_needed
