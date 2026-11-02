"""Overbuild buffer on reaction products (docs/REACTION_CAPACITY_PLAN.md D9,
revised 2026-11-02): the buffer is reacted with spare slots only and never
bought; real need is still bought when slots are short."""
from __future__ import annotations

from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.reaction_capacity import (
    ReactionLayer,
    ReactionRecipe,
    ReactionWindow,
    fit_runs,
    plan_payload,
    solve,
    WindowState,
)

RAW = 9000


def _recipe(key, product, *, seconds=86400.0, fee=100.0, out=100.0, formulas=10,
            inputs=None):
    return ReactionRecipe(
        key=key, product_type_id=product, blueprint_type_id=product + 1000,
        seconds_per_run=seconds, fee_per_run=fee,
        inputs=inputs if inputs is not None else {RAW: 1.0},
        outputs={product: out}, fit=fit_runs(seconds, 13.0),
        owned_formulas=formulas, formula_price=None,
    )


def _window(demand, buffer, *, slots, on_hand=None, prices=None):
    base_prices = {RAW: 1.0}
    for p in demand:
        base_prices[p] = 10_000.0
    base_prices.update(prices or {})
    return ReactionWindow(
        slots=slots, on_hand=on_hand or {RAW: 1e9}, demand=demand,
        prices=base_prices, buffer=buffer, cycle_demand={},
    )


def test_buffer_is_never_bought_when_slots_are_short():
    # One slot makes 1,300 units (13 runs x 100). Need 1,000, buffer 5,000.
    layer = ReactionLayer(recipes=[_recipe("r", 1)])
    plan = solve(layer, _window({1: 6000.0}, {1: 5000.0}, slots=1))

    assert plan.buy.get(1, 0.0) < 1e-6
    assert plan.unmet_demand == {}
    assert {a.recipe_key: a.jobs for a in plan.recipes}["r"] == 1
    assert plan.buffer_target == {1: 5000.0}
    assert abs(plan.buffer_open[1] - (5000.0 - 300.0)) < 1e-6


def test_need_is_still_bought_when_slots_are_short():
    # Need 3,000 on one slot (1,300 per job): 1,700 bought, buffer stays open.
    layer = ReactionLayer(recipes=[_recipe("r", 1)])
    plan = solve(layer, _window({1: 8000.0}, {1: 5000.0}, slots=1))

    assert abs(plan.buy.get(1, 0.0) - 1700.0) < 1e-6
    assert plan.buffer_open[1] == 5000.0
    assert not any("Unmet" in w for w in plan.warnings)


def test_spare_slots_fill_the_buffer_worth_most_per_slot():
    cheap = _recipe("cheap", 1)
    dear = _recipe("dear", 2)
    layer = ReactionLayer(recipes=[cheap, dear])
    window = _window(
        {1: 2600.0, 2: 2600.0}, {1: 2600.0, 2: 2600.0}, slots=2,
        prices={1: 50.0, 2: 10_000.0})
    plan = solve(layer, window)

    jobs = {a.recipe_key: a.jobs for a in plan.recipes}
    assert jobs.get("dear") == 2
    assert jobs.get("cheap", 0) == 0
    assert plan.buy == {}
    assert plan.buffer_open.get(2, 0.0) < 1e-6
    assert plan.buffer_open[1] == 2600.0


def test_raw_inputs_may_be_bought_for_buffer_jobs():
    layer = ReactionLayer(recipes=[_recipe("r", 1)])
    plan = solve(layer, _window({1: 1300.0}, {1: 1300.0}, slots=1, on_hand={RAW: 0.0}))

    assert {a.recipe_key: a.jobs for a in plan.recipes}["r"] == 1
    assert plan.buy.get(RAW, 0.0) >= 13.0 - 1e-6
    assert plan.buy.get(1, 0.0) < 1e-6
    assert plan.buffer_open == {}


def test_intermediate_is_not_bought_to_make_a_buffer_composite():
    # Composite 2 needs intermediate 1 (not on hand). Only the buffer wants 2:
    # buying 1 to react 2 would be buying the buffer through its input.
    inter = _recipe("inter", 1)
    comp = _recipe("comp", 2, inputs={1: 1.0})
    layer = ReactionLayer(recipes=[inter, comp])
    window = _window({2: 1300.0}, {2: 1300.0}, slots=5,
                     on_hand={RAW: 1e9, 1: 0.0}, prices={1: 10.0, 2: 1_000_000.0})
    plan = solve(layer, window)

    assert plan.buy.get(1, 0.0) < 1e-6
    assert plan.buy.get(2, 0.0) < 1e-6
    assert "comp" not in {a.recipe_key for a in plan.recipes if a.jobs}


def test_no_buffer_keeps_todays_result():
    layer = ReactionLayer(recipes=[_recipe("r", 1)])
    with_empty = solve(layer, _window({1: 3000.0}, {}, slots=1))
    assert abs(with_empty.buy.get(1, 0.0) - 1700.0) < 1e-6
    assert with_empty.buffer_target == {}
    assert with_empty.buffer_open == {}


def test_payload_lists_buffer_rows(monkeypatch):
    from eve_trader.production import reaction_capacity as rc
    monkeypatch.setattr(rc, "_type_names", lambda ids: {1: "Sylramic Fibers"})
    layer = ReactionLayer(recipes=[_recipe("r", 1)])
    plan = solve(layer, _window({1: 6000.0}, {1: 5000.0}, slots=1))
    state = WindowState(total_slots=1, busy_slots=0, free_slots=1, busy_slots_w2=0,
                        free_slots_w2=1, on_hand_from_jobs={}, incoming1_from_jobs={},
                        incoming2_from_jobs={})
    payload = plan_payload(plan, state, ProductionConfig())
    assert payload["buffer"] == [{
        "product_type_id": 1, "type_name": "Sylramic Fibers",
        "target": 5000.0, "open": 4700.0, "filled": 300.0,
    }]


def test_expand_all_reports_the_buffer_at_the_reaction_boundary(monkeypatch):
    # Component 100 (built, base 10 runs) needs 5 x reaction product 200 per run.
    def classify(type_id):
        if type_id == 100:
            return "Advanced Component", (1100, 1, 1)
        if type_id == 200:
            return "Reaction", (1200, 11, 1)
        return None, None

    monkeypatch.setattr(engine, "classify_activity", classify)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine.storage, "get_blueprint_materials",
                        lambda bp, act: [(200, 5)] if bp == 1100 else [])
    cfg = ProductionConfig()
    cfg.component_overbuild = 0.7
    demand: dict[int, float] = {}
    buffer: dict[int, float] = {}
    engine._expand_all(
        {100: 4.0}, cfg, {}, {}, {}, {}, {}, {}, {}, {}, {100: 10.0},
        stop_at_reactions=True, reaction_demand=demand, reaction_buffer=buffer)

    assert demand == {200: 4 * 5 + 0.7 * 5 * 10}
    assert buffer == {200: 0.7 * 5 * 10}
