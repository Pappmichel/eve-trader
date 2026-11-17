"""Reaction plan after rounding W1 runs to whole runs: an input that is short
and cannot be bought is stated, and a buffered reaction product is never
bought above what the demand without buffer buys (D9)."""
from __future__ import annotations

from dataclasses import replace

from eve_trader.production import reaction_capacity as rc
from eve_trader.production.reaction_capacity import (
    ReactionLayer,
    ReactionRecipe,
    ReactionWindow,
    solve,
)

RAW1, RAW2, INTER, PROD_P, PROD_Q = 1, 2, 10, 20, 21


def _recipe(key, product, bp, fee, inputs, outputs, fit, owned, formula_price):
    return ReactionRecipe(
        key=key, product_type_id=product, blueprint_type_id=bp,
        seconds_per_run=3600, fee_per_run=fee, inputs=inputs, outputs=outputs,
        fit=fit, owned_formulas=owned, formula_price=formula_price,
    )


def _unpriced_input_case():
    layer = ReactionLayer(recipes=(
        _recipe("normal:10", INTER, 100, 2e6, {RAW1: 2.0, RAW2: 1.0}, {INTER: 2.0},
                4, 0, 1e6),
        _recipe("normal:20", PROD_P, 101, 0.0, {INTER: 2.0, RAW1: 1.0}, {PROD_P: 3.0},
                4, 1, 2e7),
    ))
    window = ReactionWindow(
        slots=1,
        on_hand={RAW1: 4.0, RAW2: 2.0, INTER: 3.0, PROD_P: 1.0},
        demand={PROD_P: 7.0},
        # The intermediate has no price: it cannot be bought.
        prices={RAW1: 3e6, RAW2: 5e6, PROD_P: 3e7, PROD_Q: 1.5e7},
        incoming2={PROD_P: 3.0},
        slots2=3,
        cycle_demand={PROD_P: 6.0, PROD_Q: 1.0},
        type_names={INTER: "Intermediate"},
    )
    return layer, window


def test_short_input_without_price_is_warned_and_runs_are_kept():
    layer, window = _unpriced_input_case()
    plan = solve(layer, window)

    runs = {a.recipe_key: a.runs for a in plan.recipes}
    # Two runs need 4 intermediates, 3 are on hand and none can be bought.
    assert runs == {"normal:20": 2.0}
    assert INTER not in plan.buy
    assert "Input Intermediate short by 1 units and has no price" in plan.warnings


def test_no_warning_when_the_unpriced_input_is_on_hand():
    layer, window = _unpriced_input_case()
    window = replace(window, on_hand={**window.on_hand, INTER: 4.0})
    plan = solve(layer, window)

    assert not any("has no price" in w for w in plan.warnings)


def _buffer_case(buffer):
    layer = ReactionLayer(recipes=(
        _recipe("normal:10", INTER, 100, 1e5, {RAW1: 2.0, RAW2: 1.0}, {INTER: 2.0},
                3, 1, 1e6),
        _recipe("normal:20", PROD_P, 101, 0.0, {INTER: 2.0, RAW1: 1.0}, {PROD_P: 3.0},
                4, 1, None),
        _recipe("normal:21", PROD_Q, 102, 5e5, {INTER: 1.0, RAW2: 2.0}, {PROD_Q: 2.0},
                3, 2, 1e6),
    ))
    window = ReactionWindow(
        slots=3,
        on_hand={RAW1: 5.0, RAW2: 6.0, INTER: 3.0, PROD_P: 1.0},
        demand={PROD_P: 3.0, PROD_Q: 4.0},
        prices={RAW1: 1e6, RAW2: 5e6, INTER: 2e7, PROD_P: 3e7, PROD_Q: 1.5e7},
        slots2=3,
        buffer=buffer,
    )
    return layer, window


def test_buffer_run_rounded_up_never_buys_a_reaction_product_above_the_cap():
    layer, window = _buffer_case({PROD_P: 2.0, PROD_Q: 2.0})
    plan = solve(layer, window)
    base = rc._solve_core(layer, replace(
        window, demand=window.hard_demand(), buffer={}, buffer_buy_cap=None))

    for p in layer.product_ids:
        assert plan.buy.get(p, 0.0) <= base.buy.get(p, 0.0) + 1e-6
    # The run that only filled the buffer is cut; its units stay open.
    assert plan.buffer_open.get(PROD_P, 0.0) > 0.0
    assert "normal:20" not in {a.recipe_key for a in plan.recipes}


def test_plan_without_buffer_never_reaches_the_buffer_cut(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("buffer cut used without a buffer")

    monkeypatch.setattr(rc, "_cut_buffer_runs_over_cap", boom)
    layer, window = _buffer_case({})
    plan = solve(layer, window)
    assert plan.buffer_open == {}
