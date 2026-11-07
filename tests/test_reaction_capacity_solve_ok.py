"""A failed buffer stage keeps the demand-only plan (solve_ok / O7-06 pattern).

The instance is a seeded small layer from the reaction solver audit: two
slots, demand 3 of a product, a buffer, and one raw material without a price.
Stage 1 (demand only) is feasible; stage 2 (demand plus buffer) is infeasible
and used to replace a good plan with an empty one that showed no failure.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from eve_trader.production import reaction_capacity as rc

RAW1, RAW2, I, P, Q = 1, 2, 10, 20, 21


def _layer():
    return rc.ReactionLayer((
        rc.ReactionRecipe(
            key="normal:10", product_type_id=I, blueprint_type_id=100, seconds_per_run=3600,
            fee_per_run=500_000.0, inputs={RAW1: 2.0, RAW2: 1.0}, outputs={I: 2.0},
            fit=3, owned_formulas=0, formula_price=None),
        rc.ReactionRecipe(
            key="normal:20", product_type_id=P, blueprint_type_id=101, seconds_per_run=3600,
            fee_per_run=2_000_000.0, inputs={I: 2.0, RAW1: 1.0}, outputs={P: 3.0},
            fit=2, owned_formulas=2, formula_price=None),
        rc.ReactionRecipe(
            key="normal:21", product_type_id=Q, blueprint_type_id=102, seconds_per_run=3600,
            fee_per_run=500_000.0, inputs={I: 1.0, RAW2: 2.0}, outputs={Q: 2.0},
            fit=2, owned_formulas=1, formula_price=20_000_000.0),
    ))


def _window(**overrides):
    base = dict(
        slots=2, on_hand={RAW1: 9.0, RAW2: 1.0, I: 2.0, P: 3.0},
        demand={P: 11.0, Q: 3.0},
        prices={RAW1: 3e6, I: 9e6, P: 1e7, Q: 5e7},  # RAW2 has no price
        incoming1={I: 2.0}, incoming2={}, slots2=3, demand_factor=0.5,
        cycle_demand={P: 2.0, Q: 1.0}, formula_payback_cycles=2.0,
    )
    base.update(overrides)
    return rc.ReactionWindow(**base)


BUFFER = {P: 10.0, Q: 1.0}


def test_failed_buffer_stage_keeps_the_demand_only_plan():
    layer = _layer()
    window = replace(_window(), buffer=dict(BUFFER))
    stage1 = rc._solve_core(
        layer, replace(window, demand=window.hard_demand(), buffer={}, buffer_buy_cap=None))
    assert stage1.solve_ok and stage1.jobs_used > 0

    plan = rc.solve(layer, window)

    # Stage 2 is infeasible: its empty plan must not replace stage 1.
    assert "Buffer solve failed; buffer left open" in plan.warnings
    assert plan.jobs_used == stage1.jobs_used
    assert plan.w1_cost == pytest.approx(stage1.w1_cost)
    assert dict(plan.buy) == dict(stage1.buy)
    assert dict(plan.buffer_open) == BUFFER
    assert dict(plan.buffer_target) == BUFFER
    assert plan.solve_ok  # stage 1, the plan that is kept, solved fine


def test_stage_two_alone_reports_the_solver_failure():
    layer = _layer()
    window = replace(_window(), buffer=dict(BUFFER))
    hard = window.hard_demand()
    base = rc._solve_core(layer, replace(window, demand=hard, buffer={}, buffer_buy_cap=None))
    stage2_demand = dict(hard)
    for product, qty in BUFFER.items():
        stage2_demand[product] = stage2_demand.get(product, 0.0) + qty
    soft = rc._solve_core(layer, replace(
        window, demand=stage2_demand,
        buffer_buy_cap={p: float(base.buy.get(p, 0.0)) for p in layer.product_ids}))

    assert any(w.startswith("Solver:") for w in soft.warnings)
    assert soft.solve_ok is False


def test_plain_solve_without_buffer_is_ok():
    plan = rc.solve(_layer(), _window())
    assert plan.solve_ok
    assert not any(w.startswith("Solver:") for w in plan.warnings)


def test_buffer_solve_that_works_is_used_and_ok():
    # Everything priced: the buffer stage is feasible and kept.
    window = _window(prices={RAW1: 3e6, RAW2: 2e6, I: 9e6, P: 1e7, Q: 5e7})
    plan = rc.solve(_layer(), replace(window, buffer={P: 1.0}))
    assert plan.solve_ok
    assert "Buffer solve failed; buffer left open" not in plan.warnings
    assert not any(w.startswith("Solver:") for w in plan.warnings)


def test_failed_first_stage_stays_failed():
    # No demand can be priced at all: the demand stage itself fails.
    layer = _layer()
    window = _window(prices={}, on_hand={})
    plan = rc.solve(layer, replace(window, buffer=dict(BUFFER)))
    if any(w.startswith("Solver:") for w in plan.warnings):
        assert plan.solve_ok is False


def test_time_limit_and_usable_results_are_not_failures():
    failed = SimpleNamespace(success=False, message="The problem is infeasible.", mip_time_limit=False)
    time_limit = SimpleNamespace(success=False, message="Time limit reached.", mip_time_limit=True)
    usable = SimpleNamespace(success=True, message="Optimal solution found.", mip_time_limit=False)
    silent = SimpleNamespace(success=False, message="", mip_time_limit=False)
    assert rc._raw_solve_failed(failed) is True
    assert rc._raw_solve_failed(time_limit) is False
    assert rc._raw_solve_failed(usable) is False
    assert rc._raw_solve_failed(silent) is False


def test_lp_rounding_path_keeps_solve_ok(monkeypatch):
    # A MIP that fails but whose LP can be rounded is degraded, not failed.
    layer, window = _layer(), _window(prices={RAW1: 3e6, RAW2: 2e6, I: 9e6, P: 1e7, Q: 5e7})
    real = rc._solve_mip

    def failing_mip(layer_, window_, **kwargs):
        raw = real(layer_, window_, **kwargs)
        if kwargs.get("continuous"):
            return raw
        return replace(raw, success=False, mip_time_limit=True, message="Time limit reached.")

    monkeypatch.setattr(rc, "_solve_mip", failing_mip)
    plan = rc.solve(layer, window)
    assert plan.solve_ok
    assert not any(w.startswith("Solver:") for w in plan.warnings)

