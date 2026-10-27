"""Pure solver tests for docs/REACTION_CAPACITY_PLAN.md phase 1.

Synthetic layers only - no ESI, no planner wiring. Each case names the
decision it locks (spare slots, tight slots, formulas, fit, chaining,
byproduct credit, manual overrides, busy slots, infeasible warning).
"""
from __future__ import annotations

import pytest

from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import CharacterSlotRow
from eve_trader.production.reaction_capacity import (
    ReactionLayer,
    ReactionRecipe,
    ReactionWindow,
    fit_runs,
    solve,
    window_state,
)


def _recipe(
    key: str,
    product: int,
    *,
    seconds: float = 3600.0,
    fee: float = 1000.0,
    inputs: dict | None = None,
    outputs: dict | None = None,
    fit: int | None = None,
    formulas: int = 10,
    formula_price: float | None = 10_000_000.0,
    blueprint: int | None = None,
    cycle_days: float = 13.0,
) -> ReactionRecipe:
    outs = outputs if outputs is not None else {product: 100.0}
    secs = seconds
    return ReactionRecipe(
        key=key,
        product_type_id=product,
        blueprint_type_id=blueprint if blueprint is not None else product + 1000,
        seconds_per_run=secs,
        fee_per_run=fee,
        inputs=inputs or {9000: 1.0},
        outputs=outs,
        fit=fit if fit is not None else fit_runs(secs, cycle_days),
        owned_formulas=formulas,
        formula_price=formula_price,
    )


def _window(
    demand: dict,
    *,
    slots: int = 10,
    on_hand: dict | None = None,
    incoming: dict | None = None,
    prices: dict | None = None,
    manual_buy: frozenset | None = None,
    manual_build: frozenset | None = None,
    chain_choice: dict | None = None,
    payback: float = 1.0,
) -> ReactionWindow:
    # Default: every demanded product and a cheap moon-goo input are buyable.
    base_prices = {9000: 100.0}
    for p, _ in demand.items():
        base_prices[p] = 50_000.0
    if prices:
        base_prices.update(prices)
    return ReactionWindow(
        slots=slots,
        on_hand=on_hand or {},
        incoming=incoming or {},
        demand=demand,
        prices=base_prices,
        manual_buy=manual_buy or frozenset(),
        manual_build=manual_build or frozenset(),
        chain_choice=chain_choice or {},
        formula_payback_cycles=payback,
    )


def test_fit_runs_floor_of_cycle():
    # 13 days = 1_123_200 s; a 3h run fits floor(1123200/10800) = 104.
    assert fit_runs(3 * 3600, 13.0) == 104
    assert fit_runs(14 * 86400, 13.0) == 0
    assert fit_runs(0, 13.0) == 0


def test_spare_slots_builds_all_and_adds_alchemy():
    # Normal is cheaper per unit but only 1 formula and fit=2 (200 units/job).
    # Demand is 500; leftover slots go to alchemy, still cheaper than buying (D5).
    normal = _recipe("n", 1, seconds=3600, fee=100.0, formulas=1, fit=2,
                     outputs={1: 100.0}, inputs={9000: 1.0})
    alchemy = _recipe("a", 1, seconds=3600, fee=5_000.0, formulas=5, fit=2,
                      outputs={1: 100.0, 2: 50.0},
                      inputs={9001: 1.0}, blueprint=2001)
    layer = ReactionLayer(recipes=[normal, alchemy])
    window = _window(
        {1: 500.0}, slots=10,
        on_hand={9000: 10_000.0, 9001: 10_000.0},
        prices={1: 100_000.0, 9000: 1.0, 9001: 1.0, 2: 1.0},
    )

    plan = solve(layer, window)

    by_key = {r.recipe_key: r for r in plan.recipes}
    assert "n" in by_key
    assert by_key["n"].jobs == 1
    assert by_key["n"].runs == 2  # capped by fit x formulas
    assert "a" in by_key
    assert by_key["a"].jobs >= 1
    assert plan.buy.get(1, 0) < 1e-6
    assert plan.unmet_demand == {}


def test_tight_slots_buys_weakest_per_slot_day_first():
    # Two products, one slot. Product 1 saves far more per slot-day than
    # product 2, so the single job must go to product 1 and product 2 is bought.
    strong = _recipe("strong", 1, seconds=86400, fee=100.0, formulas=5,
                     outputs={1: 100.0}, inputs={9000: 1.0})
    weak = _recipe("weak", 2, seconds=86400, fee=100.0, formulas=5,
                   outputs={2: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[strong, weak])
    # Buy price of 1 is high (building saves a lot); buy price of 2 is barely
    # above fee, so building it saves little.
    window = _window(
        {1: 100.0, 2: 100.0}, slots=1,
        on_hand={9000: 100.0},
        prices={1: 1_000_000.0, 2: 2_000.0, 9000: 1.0},
    )

    plan = solve(layer, window)

    by_key = {r.recipe_key: r for r in plan.recipes}
    assert by_key.get("strong") and by_key["strong"].jobs == 1
    assert "weak" not in by_key or by_key["weak"].jobs == 0
    assert plan.buy.get(2, 0) >= 100.0 - 1e-6
    assert plan.buy.get(1, 0) < 1e-6


def test_formula_limit_and_purchase_with_payback():
    # One owned formula, demand needs two jobs; buying a second formula at
    # 10 M pays back within one cycle because the product's buy price is high.
    recipe = _recipe("r", 1, seconds=86400, fee=100.0, formulas=1,
                     formula_price=10_000_000.0, outputs={1: 100.0},
                     inputs={9000: 1.0})
    # fit at 13d / 1d = 13 runs per job -> one job makes 1300 units; need two
    # jobs for 2000 units of demand (20 runs).
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: 2000.0}, slots=5,
        on_hand={9000: 100.0},
        prices={1: 500_000.0, 9000: 1.0},
        payback=1.0,
    )

    plan = solve(layer, window)

    alloc = {r.recipe_key: r for r in plan.recipes}["r"]
    assert alloc.jobs == 2
    assert alloc.formulas_to_buy == 1
    assert plan.buy.get(1, 0) < 1e-6


def test_formula_not_bought_when_payback_too_long():
    # Same setup, but payback_cycles is huge so the amortised formula cost
    # exceeds what building saves - buy the product instead of a formula.
    recipe = _recipe("r", 1, seconds=86400, fee=100.0, formulas=1,
                     formula_price=10_000_000.0, outputs={1: 100.0},
                     inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: 2000.0}, slots=5,
        on_hand={9000: 100.0},
        prices={1: 50_000.0, 9000: 1.0},
        payback=1000.0,  # amortised cost near zero... wait, high payback makes
        # purchase CHEAPER in the objective. Use a high formula price and
        # low product price with payback=1 instead - covered above. Here:
        # formula so expensive it never pays back even at payback=1.
    )
    # Override: make formula absurdly expensive relative to buying the product.
    recipe = _recipe("r", 1, seconds=86400, fee=100.0, formulas=1,
                     formula_price=1e15, outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: 2000.0}, slots=5,
        on_hand={9000: 100.0},
        prices={1: 50_000.0, 9000: 1.0},
        payback=1.0,
    )

    plan = solve(layer, window)

    allocs = {r.recipe_key: r for r in plan.recipes}
    assert allocs.get("r", None) is None or allocs["r"].formulas_to_buy == 0
    assert allocs.get("r", None) is None or allocs["r"].jobs <= 1
    assert plan.buy.get(1, 0) > 0


def test_job_fit_caps_runs_per_job():
    # Cycle allows only 2 runs per job; demand needs 5 runs -> at least 3 jobs.
    recipe = _recipe("r", 1, seconds=3600, fee=10.0, formulas=10,
                     fit=2, outputs={1: 1.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: 5.0}, slots=10,
        on_hand={9000: 100.0},
        prices={1: 1_000_000.0, 9000: 1.0},
    )

    plan = solve(layer, window)

    alloc = {r.recipe_key: r for r in plan.recipes}["r"]
    assert alloc.runs == 5
    assert alloc.jobs == 3
    assert alloc.runs <= alloc.jobs * 2


def test_no_chaining_inside_window():
    # Intermediate I is itself a reaction product. Demand for final F needs
    # I as input, but I is not on hand - the solver must buy I (or leave F
    # unmet), never schedule I's reaction to feed F in the same window.
    intermediate = _recipe("i", 10, seconds=86400, fee=100.0, formulas=5,
                           outputs={10: 100.0}, inputs={9000: 1.0})
    final = _recipe("f", 20, seconds=86400, fee=100.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    layer = ReactionLayer(recipes=[intermediate, final])
    window = _window(
        {20: 100.0}, slots=10,
        on_hand={9000: 100.0},  # no intermediate on hand
        prices={10: 10_000.0, 20: 1_000_000.0, 9000: 1.0},
    )

    plan = solve(layer, window)

    by_key = {r.recipe_key: r for r in plan.recipes}
    # Final may run only if intermediate was bought (not produced this window).
    if "f" in by_key and by_key["f"].runs > 0:
        assert plan.buy.get(10, 0) >= 100.0 - 1e-6
    # Intermediate runs cannot reduce the buy of 10 for feeding final in-window:
    # any intermediate production is for its own stock/demand, not this window's input.
    produced_i = by_key["i"].units.get(10, 0.0) if "i" in by_key else 0.0
    consumed_by_f = 100.0 * (by_key["f"].runs if "f" in by_key else 0)
    on_hand_i = 0.0
    bought_i = plan.buy.get(10, 0.0)
    assert on_hand_i + bought_i + 1e-6 >= consumed_by_f
    # Produced this window does not count toward that inequality's left side
    # in the model - verify the solve did not "cheat" by consuming production.
    assert produced_i == 0.0 or bought_i + on_hand_i + 1e-6 >= consumed_by_f


def test_alchemy_byproduct_credit_in_coverage():
    # Alchemy outputs target + byproduct; the byproduct covers that product's
    # own demand so it is not bought.
    alchemy = _recipe(
        "a", 1, seconds=3600, fee=100.0, formulas=5,
        outputs={1: 50.0, 2: 80.0}, inputs={9000: 1.0},
    )
    layer = ReactionLayer(recipes=[alchemy])
    window = _window(
        {1: 50.0, 2: 80.0}, slots=5,
        on_hand={9000: 10.0},
        prices={1: 100_000.0, 2: 100_000.0, 9000: 1.0},
    )

    plan = solve(layer, window)

    alloc = {r.recipe_key: r for r in plan.recipes}["a"]
    assert alloc.runs >= 1
    assert plan.buy.get(1, 0) < 1e-6
    assert plan.buy.get(2, 0) < 1e-6


def test_manual_buy_never_builds():
    recipe = _recipe("r", 1, seconds=3600, fee=10.0, formulas=5,
                     outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: 100.0}, slots=5,
        on_hand={9000: 10.0},
        prices={1: 1_000_000.0, 9000: 1.0},
        manual_buy=frozenset({1}),
    )

    plan = solve(layer, window)

    assert plan.recipes == () or all(r.jobs == 0 for r in plan.recipes)
    assert plan.buy.get(1, 0) >= 100.0 - 1e-6


def test_manual_build_exceeds_capacity_warns():
    recipe = _recipe("r", 1, seconds=86400, fee=10.0, formulas=20,
                     outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    # Demand needs many jobs; only 1 slot; manual build forbids buying.
    window = _window(
        {1: 5000.0}, slots=1,
        on_hand={9000: 10_000.0},
        prices={1: 1_000_000.0, 9000: 1.0},
        manual_build=frozenset({1}),
    )

    plan = solve(layer, window)

    assert any("Manual Build" in w or "exceed" in w.lower() or "Unmet" in w
               for w in plan.warnings)
    assert plan.buy.get(1, 0) < 1e-6


def test_busy_slots_reduce_capacity_via_window_state(monkeypatch):
    from eve_trader import storage

    monkeypatch.setattr(storage, "get_product_quantity", lambda *a, **k: 10.0)
    cfg = ProductionConfig(
        reaction_slot_override=5,
        reaction_window_next="2030-01-15T12:00:00+00:00",
    )
    rows = [
        CharacterSlotRow(
            character_name="A", job_type="Reactions",
            total_slots=5, used_slots=0, free_slots=5,
            excluded_from_planning=False,
        ),
    ]
    # Two reaction jobs still running past the window; one finishes before.
    industry_jobs = [
        # end after window -> busy
        (1, 11, 100, 50, "X", 2, 0, "active", "2030-01-20T00:00:00+00:00", None, "A"),
        (2, 11, 100, 51, "Y", 1, 0, "active", "2030-01-16T00:00:00+00:00", None, "A"),
        # end before window -> on_hand, not busy
        (3, 11, 100, 52, "Z", 3, 0, "active", "2030-01-10T00:00:00+00:00", None, "A"),
    ]

    state = window_state(cfg, character_rows=rows, industry_jobs=industry_jobs)

    assert state.total_slots == 5
    assert state.busy_slots == 2
    assert state.free_slots == 3
    assert state.on_hand_from_jobs.get(52) == 30.0  # 3 runs x 10
    assert state.incoming_from_jobs.get(50) == 20.0
    assert state.incoming_from_jobs.get(51) == 10.0


def test_no_window_date_all_slots_free():
    cfg = ProductionConfig(reaction_slot_override=8, reaction_window_next=None)
    rows = [
        CharacterSlotRow(
            character_name="A", job_type="Reactions",
            total_slots=8, used_slots=3, free_slots=5,
            excluded_from_planning=False,
        ),
    ]
    state = window_state(cfg, character_rows=rows, industry_jobs=[
        (1, 11, 100, 50, "X", 1, 0, "active", "2030-01-20T00:00:00+00:00", None, "A"),
    ])
    assert state.busy_slots == 0
    assert state.free_slots == 8
    assert state.on_hand_from_jobs == {}


def test_infeasible_unpriced_demand_warns():
    # Demand for a product with no buy price and no recipe capacity (0 slots)
    # -> unmet demand warning, model stays feasible via short.
    recipe = _recipe("r", 1, seconds=86400, fee=10.0, formulas=0,
                     formula_price=None, outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = ReactionWindow(
        slots=0,
        on_hand={},
        incoming={},
        demand={1: 100.0},
        prices={9000: 1.0},  # product 1 has no price
        formula_payback_cycles=1.0,
    )

    plan = solve(layer, window)

    assert plan.unmet_demand.get(1, 0) > 0
    assert any("Unmet" in w for w in plan.warnings)


def test_chaining_comparison_returns_both_totals():
    intermediate = _recipe("i", 10, seconds=86400, fee=100.0, formulas=5,
                           outputs={10: 100.0}, inputs={9000: 1.0})
    final = _recipe("f", 20, seconds=86400, fee=100.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    layer = ReactionLayer(recipes=[intermediate, final])
    window = _window(
        {20: 100.0}, slots=5,
        on_hand={9000: 100.0},
        prices={10: 10_000.0, 20: 1_000_000.0, 9000: 1.0},
    )

    plan = solve(layer, window)

    assert plan.chaining
    comp = next(c for c in plan.chaining if c.product_type_id == 20)
    assert comp.buy_inputs_cost >= 0
    assert comp.wait_cost >= 0
    assert comp.preferred in ("buy", "wait")
    assert 10 in comp.short_input_ids
