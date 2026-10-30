"""Pure solver tests for docs/REACTION_CAPACITY_PLAN.md phase 1 / 1b / 1c.

Synthetic layers only - no ESI, no planner wiring. Covers the two-window
model (D13/D14), revised D10, LP dual shadow prices, phase 1c fixes
(continuous W2, hard W1 slots, like-for-like chaining, w1/w2 costs), and
excluded-character busy-slot filtering.
"""
from __future__ import annotations

import time

import pytest

from eve_trader import storage
from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import CharacterSlotRow
from datetime import datetime, timezone

from eve_trader.production.reaction_capacity import (
    ChainingComparison,
    ReactionLayer,
    ReactionPlan,
    ReactionRecipe,
    ReactionWindow,
    apply_plan_to_build_buy,
    effective_window_datetime,
    effective_window_iso,
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
    slots2: int | None = None,
    on_hand: dict | None = None,
    incoming1: dict | None = None,
    incoming2: dict | None = None,
    prices: dict | None = None,
    manual_buy: frozenset | None = None,
    manual_build: frozenset | None = None,
    chain_choice: dict | None = None,
    payback: float = 1.0,
    demand_factor: float = 1.0,
) -> ReactionWindow:
    base_prices = {9000: 100.0}
    for p, _ in demand.items():
        base_prices[p] = 50_000.0
    if prices:
        base_prices.update(prices)
    return ReactionWindow(
        slots=slots,
        slots2=slots2,
        on_hand=on_hand or {},
        incoming1=incoming1 or {},
        incoming2=incoming2 or {},
        demand=demand,
        prices=base_prices,
        manual_buy=manual_buy or frozenset(),
        manual_build=manual_build or frozenset(),
        chain_choice=chain_choice or {},
        formula_payback_cycles=payback,
        demand_factor=demand_factor,
    )


def test_fit_runs_floor_of_cycle():
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
    assert by_key["n"].runs == 2
    assert "a" in by_key
    assert by_key["a"].jobs >= 1
    assert plan.buy.get(1, 0) < 1e-6
    assert plan.unmet_demand == {}


def test_tight_slots_buys_weakest_per_slot_day_first():
    strong = _recipe("strong", 1, seconds=86400, fee=100.0, formulas=5,
                     outputs={1: 100.0}, inputs={9000: 1.0})
    weak = _recipe("weak", 2, seconds=86400, fee=100.0, formulas=5,
                   outputs={2: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[strong, weak])
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
    recipe = _recipe("r", 1, seconds=86400, fee=100.0, formulas=1,
                     formula_price=10_000_000.0, outputs={1: 100.0},
                     inputs={9000: 1.0})
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
    # Formula so expensive it never pays back at payback=1; buy the product.
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
    # demand_factor=0 so W2 adds no extra demand; W1 alone needs 5 runs.
    recipe = _recipe("r", 1, seconds=3600, fee=10.0, formulas=10,
                     fit=2, outputs={1: 1.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: 5.0}, slots=10,
        on_hand={9000: 100.0},
        prices={1: 1_000_000.0, 9000: 1.0},
        demand_factor=0.0,
    )

    plan = solve(layer, window)

    alloc = {r.recipe_key: r for r in plan.recipes}["r"]
    assert alloc.runs == 5
    assert alloc.jobs == 3
    assert alloc.runs <= alloc.jobs * 2


def test_no_chaining_inside_window():
    # Intermediate started in W1 cannot feed the same window's composite.
    intermediate = _recipe("i", 10, seconds=86400, fee=100.0, formulas=5,
                           outputs={10: 100.0}, inputs={9000: 1.0})
    final = _recipe("f", 20, seconds=86400, fee=100.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    layer = ReactionLayer(recipes=[intermediate, final])
    window = _window(
        {20: 100.0}, slots=10,
        on_hand={9000: 100.0},
        prices={10: 10_000.0, 20: 1_000_000.0, 9000: 1.0},
    )

    plan = solve(layer, window)

    by_key = {r.recipe_key: r for r in plan.recipes}
    if "f" in by_key and by_key["f"].runs > 0:
        assert plan.buy.get(10, 0) >= 100.0 - 1e-6
    consumed_by_f = 100.0 * (by_key["f"].runs if "f" in by_key else 0)
    bought_i = plan.buy.get(10, 0.0)
    assert bought_i + 1e-6 >= consumed_by_f


def test_alchemy_byproduct_credit_in_coverage():
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


def test_effective_window_rolls_forward_two_intervals():
    # Anchor two full intervals in the past → effective = anchor + 2×interval.
    now = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
    anchor = "2026-09-29T12:00:00+00:00"  # exactly 28 days = 2×14 before now
    effective = effective_window_datetime(anchor, 14.0, now=now)
    assert effective == datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
    assert effective_window_iso(anchor, 14.0, now=now) == effective.isoformat()

    # Still in the past by a second → roll one more interval.
    now2 = datetime(2026, 10, 27, 12, 0, 1, tzinfo=timezone.utc)
    effective2 = effective_window_datetime(anchor, 14.0, now=now2)
    assert effective2 == datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Future anchor is unchanged.
    future = "2030-01-15T12:00:00+00:00"
    assert effective_window_datetime(future, 14.0, now=now) == datetime(
        2030, 1, 15, 12, 0, 0, tzinfo=timezone.utc,
    )
    assert effective_window_datetime(None, 14.0, now=now) is None


def test_window_state_uses_effective_next_when_anchor_is_past(monkeypatch):
    """A past anchor must not treat every running job as busy for W1."""
    from eve_trader import storage

    monkeypatch.setattr(storage, "get_product_quantity", lambda *a, **k: 10.0)
    # Anchor two intervals before "now"; effective W1 = 2026-10-27.
    # Freeze wall clock by patching effective_window_datetime's now via
    # the cfg path: monkeypatch datetime used inside by injecting through
    # a thin wrapper — instead pass jobs relative to a known effective.
    cfg = ProductionConfig(
        reaction_slot_override=5,
        reaction_window_next="2026-09-29T12:00:00+00:00",
        reaction_window_interval_days=14.0,
    )
    rows = [
        CharacterSlotRow(
            character_name="A", job_type="Reactions",
            total_slots=5, used_slots=0, free_slots=5,
            excluded_from_planning=False,
        ),
    ]
    # Job ends after the stored (past) anchor but before the effective next
    # window — under a naive past-anchor read it would count as busy; with
    # roll-forward it is on_hand for W1.
    from eve_trader.production import reaction_capacity as rc
    real_eff = rc.effective_window_datetime

    def _eff(anchor, interval_days, *, now=None):
        return real_eff(
            anchor, interval_days,
            now=datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(rc, "effective_window_datetime", _eff)
    industry_jobs = [
        # Ends 2026-10-20: after past anchor, before effective W1 → on_hand.
        (1, 11, 100, 50, "X", 2, 0, "active", "2026-10-20T00:00:00+00:00", None, "A"),
        # Ends 2026-11-01: after effective W1, before W2 → busy W1 / incoming1.
        (2, 11, 100, 51, "Y", 1, 0, "active", "2026-11-01T00:00:00+00:00", None, "A"),
    ]
    state = window_state(cfg, character_rows=rows, industry_jobs=industry_jobs)
    assert state.busy_slots == 1
    assert state.free_slots == 4
    assert state.on_hand_from_jobs.get(50) == 20.0
    assert state.incoming1_from_jobs.get(51) == 10.0


def test_busy_slots_reduce_capacity_via_window_state(monkeypatch):
    from eve_trader import storage

    monkeypatch.setattr(storage, "get_product_quantity", lambda *a, **k: 10.0)
    cfg = ProductionConfig(
        reaction_slot_override=5,
        reaction_window_next="2030-01-15T12:00:00+00:00",
        reaction_window_interval_days=14.0,
    )
    rows = [
        CharacterSlotRow(
            character_name="A", job_type="Reactions",
            total_slots=5, used_slots=0, free_slots=5,
            excluded_from_planning=False,
        ),
    ]
    industry_jobs = [
        (1, 11, 100, 50, "X", 2, 0, "active", "2030-01-20T00:00:00+00:00", None, "A"),
        (2, 11, 100, 51, "Y", 1, 0, "active", "2030-01-16T00:00:00+00:00", None, "A"),
        (3, 11, 100, 52, "Z", 3, 0, "active", "2030-01-10T00:00:00+00:00", None, "A"),
    ]

    state = window_state(cfg, character_rows=rows, industry_jobs=industry_jobs)

    assert state.total_slots == 5
    assert state.busy_slots == 2
    assert state.free_slots == 3
    assert state.on_hand_from_jobs.get(52) == 30.0
    # Both post-W1 jobs end before W2 (W1+14d = 2030-01-29).
    assert state.incoming1_from_jobs.get(50) == 20.0
    assert state.incoming1_from_jobs.get(51) == 10.0
    assert state.busy_slots_w2 == 0


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


def test_excluded_characters_jobs_do_not_reduce_free_slots(monkeypatch):
    from eve_trader import storage

    monkeypatch.setattr(storage, "get_product_quantity", lambda *a, **k: 1.0)
    cfg = ProductionConfig(
        reaction_slot_override=None,
        reaction_window_next="2030-01-15T12:00:00+00:00",
    )
    rows = [
        CharacterSlotRow(
            character_name="Keeper", job_type="Reactions",
            total_slots=10, used_slots=0, free_slots=10,
            excluded_from_planning=False,
        ),
        CharacterSlotRow(
            character_name="Excluded", job_type="Reactions",
            total_slots=10, used_slots=0, free_slots=10,
            excluded_from_planning=True,
        ),
    ]
    industry_jobs = [
        # Excluded installer's job must not count as busy.
        (1, 11, 100, 50, "X", 1, 0, "active", "2030-01-20T00:00:00+00:00", None, "Excluded"),
        (2, 11, 100, 51, "Y", 1, 0, "active", "2030-01-20T00:00:00+00:00", None, "Keeper"),
    ]

    state = window_state(cfg, character_rows=rows, industry_jobs=industry_jobs)

    assert state.total_slots == 10  # only non-excluded
    assert state.busy_slots == 1
    assert state.free_slots == 9


def test_infeasible_unpriced_demand_warns():
    # No slots, no formula, product unpriced -> short covers W1 demand.
    recipe = _recipe("r", 1, seconds=86400, fee=10.0, formulas=0,
                     formula_price=None, outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = ReactionWindow(
        slots=0,
        on_hand={},
        incoming1={},
        incoming2={},
        demand={1: 100.0},
        prices={9000: 1.0},
        formula_payback_cycles=1.0,
        demand_factor=0.0,
    )

    plan = solve(layer, window)

    assert plan.unmet_demand.get(1, 0) > 0 or any("Unmet" in w for w in plan.warnings)
    assert plan.buy.get(1, 0) < 1e-6


def test_intermediate_reacted_in_w1_for_w2_composites():
    # Spare W1 slots + W2 composite demand: Hexite-like intermediate is
    # reacted in W1 and not bought in W2 (D13).
    intermediate = _recipe("i", 10, seconds=86400, fee=100.0, formulas=5,
                           outputs={10: 100.0}, inputs={9000: 1.0})
    final = _recipe("f", 20, seconds=86400, fee=100.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    layer = ReactionLayer(recipes=[intermediate, final])
    window = _window(
        {20: 100.0}, slots=10, slots2=10,
        on_hand={9000: 10_000.0},
        prices={10: 500_000.0, 20: 1_000_000.0, 9000: 1.0},
        demand_factor=1.0,
    )

    plan = solve(layer, window)

    by_key = {r.recipe_key: r for r in plan.recipes}
    assert "i" in by_key and by_key["i"].jobs >= 1
    assert plan.next_window is not None
    assert plan.next_window.buy.get(10, 0) < 1e-6


def test_wait_defers_composite_to_w2_default_is_buy():
    intermediate = _recipe("i", 10, seconds=86400, fee=50.0, formulas=5,
                           outputs={10: 100.0}, inputs={9000: 1.0})
    final = _recipe("f", 20, seconds=86400, fee=50.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    layer = ReactionLayer(recipes=[intermediate, final])
    prices = {10: 200_000.0, 20: 1_000_000.0, 9000: 1.0}

    # Default (no choice): buy the composite / its inputs in W1.
    buy_plan = solve(layer, _window(
        {20: 100.0}, slots=10,
        on_hand={9000: 10_000.0},
        prices=prices,
    ))
    assert buy_plan.deferred.get(20, 0) < 1e-6
    assert buy_plan.buy.get(20, 0) + buy_plan.buy.get(10, 0) > 0 or any(
        a.recipe_key == "f" and a.runs > 0 for a in buy_plan.recipes
    )

    # Stored "wait": W1 demand for 20 is deferred; W2 runs it from W1 intermediates.
    wait_plan = solve(layer, _window(
        {20: 100.0}, slots=10,
        on_hand={9000: 10_000.0},
        prices=prices,
        chain_choice={20: "wait"},
    ))
    assert wait_plan.deferred.get(20, 0) >= 100.0 - 1e-6
    assert wait_plan.buy.get(20, 0) < 1e-6
    by_key = {r.recipe_key: r for r in wait_plan.recipes}
    assert "i" in by_key and by_key["i"].jobs >= 1
    assert wait_plan.next_window is not None
    w2 = {r.recipe_key: r for r in wait_plan.next_window.recipes}
    assert "f" in w2 and w2["f"].runs > 0


def test_chaining_comparison_reports_positive_saving_for_wait():
    intermediate = _recipe("i", 10, seconds=86400, fee=50.0, formulas=5,
                           outputs={10: 100.0}, inputs={9000: 1.0})
    final = _recipe("f", 20, seconds=86400, fee=50.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    layer = ReactionLayer(recipes=[intermediate, final])
    window = _window(
        {20: 100.0}, slots=10,
        on_hand={9000: 10_000.0},
        prices={10: 200_000.0, 20: 1_000_000.0, 9000: 1.0},
    )

    plan = solve(layer, window)

    assert plan.chaining
    for comp in plan.chaining:
        assert comp.isk_saved_by_waiting >= 0
    comp = next(c for c in plan.chaining if c.product_type_id == 20)
    assert comp.preferred == "buy"  # default
    assert comp.isk_saved_by_waiting > 0
    assert comp.units_delayed == pytest.approx(100.0)
    # Like-for-like: both sides are continuous LP objectives.
    assert comp.wait_cost <= comp.buy_now_cost + 1e-6


def test_shadow_prices_match_brute_force_plus_one():
    # Small layer where slots and formulas bind; duals must match a
    # re-solve with +1 slot / +1 owned formula (within rounding).
    # Demand is exactly two full jobs (fit=13 -> 1300 units/job) so a
    # +1 capacity step displaces a full job of purchases and the LP dual
    # matches the integer finite difference (a partial leftover job would
    # make the instantaneous dual larger than the +1 step).
    demand_units = 2600.0
    recipe = _recipe("r", 1, seconds=86400, fee=100.0, formulas=1,
                     formula_price=None,  # cannot buy more
                     outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    base = _window(
        {1: demand_units}, slots=1,
        on_hand={9000: 100.0},
        prices={1: 500_000.0, 9000: 1.0},
    )
    plan = solve(layer, base)
    assert plan.jobs_used >= 0

    # Give 2 formulas so slot binds alone for the slot probe.
    recipe2 = _recipe("r", 1, seconds=86400, fee=100.0, formulas=2,
                      formula_price=None, outputs={1: 100.0}, inputs={9000: 1.0})
    layer2 = ReactionLayer(recipes=[recipe2])
    base2 = _window(
        {1: demand_units}, slots=1,
        on_hand={9000: 100.0},
        prices={1: 500_000.0, 9000: 1.0},
        demand_factor=0.0,
    )
    plan2 = solve(layer2, base2)
    more_slots = solve(layer2, _window(
        {1: demand_units}, slots=2,
        on_hand={9000: 100.0},
        prices={1: 500_000.0, 9000: 1.0},
        demand_factor=0.0,
    ))
    brute_slot = plan2.w1_cost - more_slots.w1_cost
    assert plan2.slot_shadow_price > 0
    # Continuous-LP dual vs integer re-solve: same order, small gap allowed.
    assert plan2.slot_shadow_price == pytest.approx(brute_slot, rel=0.15, abs=1.0)

    # Formula binds with 2 slots and 1 owned formula (W1 only).
    recipe3 = _recipe("r", 1, seconds=86400, fee=100.0, formulas=1,
                      formula_price=None, outputs={1: 100.0}, inputs={9000: 1.0})
    layer3 = ReactionLayer(recipes=[recipe3])
    plan3 = solve(layer3, _window(
        {1: demand_units}, slots=2,
        on_hand={9000: 100.0},
        prices={1: 500_000.0, 9000: 1.0},
        demand_factor=0.0,  # isolate W1 so dual matches +1-formula re-solve
    ))
    recipe3b = _recipe("r", 1, seconds=86400, fee=100.0, formulas=2,
                       formula_price=None, outputs={1: 100.0}, inputs={9000: 1.0})
    more_f = solve(ReactionLayer(recipes=[recipe3b]), _window(
        {1: demand_units}, slots=2,
        on_hand={9000: 100.0},
        prices={1: 500_000.0, 9000: 1.0},
        demand_factor=0.0,
    ))
    brute_f = plan3.w1_cost - more_f.w1_cost
    assert plan3.formula_shadow_prices.get("r", 0) > 0
    assert plan3.formula_shadow_prices["r"] == pytest.approx(brute_f, rel=0.15, abs=1.0)


def test_formula_shadow_is_sum_of_both_window_rows():
    # Each window needs two jobs (2600 units, fit=13 → 1300/job) but only one
    # formula exists, so both formula rows bind. Dual = d1 + d2 matches +1.
    demand_units = 2600.0
    recipe = _recipe("r", 1, seconds=86400, fee=100.0, formulas=1,
                     formula_price=None, outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: demand_units}, slots=2, slots2=2,
        on_hand={9000: 10_000.0},
        prices={1: 500_000.0, 9000: 1.0},
        demand_factor=1.0,
    )
    plan = solve(layer, window)
    more = solve(
        ReactionLayer(recipes=[_recipe(
            "r", 1, seconds=86400, fee=100.0, formulas=2, formula_price=None,
            outputs={1: 100.0}, inputs={9000: 1.0},
        )]),
        window,
    )
    brute = plan.total_cost - more.total_cost
    assert plan.formula_shadow_prices.get("r", 0) > 0
    assert plan.formula_shadow_prices["r"] == pytest.approx(brute, rel=0.15, abs=1.0)


def test_purchase_only_w2_needs_moves_to_the_preview():
    # Wait: W1 defers the composite; W2 runs it from a bought intermediate.
    # That intermediate is only needed at the W2 start, so it is not on
    # today's Buy List (and not in w1_cost) but in the W2 preview.
    final = _recipe("f", 20, seconds=86400, fee=10.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    # No recipe for intermediate 10 -> must be bought.
    layer = ReactionLayer(recipes=[final])
    window = _window(
        {20: 100.0}, slots=5,
        on_hand={9000: 10_000.0},
        prices={10: 50_000.0, 20: 1_000_000.0, 9000: 1.0},
        chain_choice={20: "wait"},
        demand_factor=1.0,
    )
    plan = solve(layer, window)
    assert plan.deferred.get(20, 0) >= 100.0 - 1e-6
    assert plan.buy.get(10, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert plan.next_window.buy.get(10, 0.0) >= 100.0 - 1e-6
    assert plan.w2_preview_cost >= 100.0 * 50_000.0 - 1.0
    assert plan.total_cost == pytest.approx(plan.w1_cost + plan.w2_preview_cost)


def test_w1_needs_stay_on_todays_buy_list():
    # No stock of the intermediate: running the composite in W1 needs it now.
    final = _recipe("f", 20, seconds=86400, fee=10.0, formulas=5,
                    outputs={20: 100.0}, inputs={10: 100.0})
    layer = ReactionLayer(recipes=[final])
    window = _window(
        {20: 100.0}, slots=5,
        on_hand={9000: 10_000.0},
        prices={10: 5_000.0, 20: 1_000_000.0, 9000: 1.0},
        demand_factor=0.0,
    )
    plan = solve(layer, window)
    w1_runs = sum(a.runs for a in plan.recipes)
    assert plan.buy.get(10, 0.0) == pytest.approx(100.0 * w1_runs)
    assert w1_runs >= 1


def test_w1_slot_slack_never_without_manual_build():
    # Tight slots, no manual build: plan must not use W1 slot slack.
    recipe = _recipe("r", 1, seconds=86400, fee=100.0, formulas=50,
                     formula_price=None, outputs={1: 100.0}, inputs={9000: 1.0})
    layer = ReactionLayer(recipes=[recipe])
    window = _window(
        {1: 50_000.0}, slots=3,
        on_hand={9000: 1_000_000.0},
        prices={1: 500_000.0, 9000: 1.0},
        demand_factor=1.0,
    )
    plan = solve(layer, window)
    assert plan.jobs_used <= window.slots
    assert not any("exceed W1 slot capacity" in w for w in plan.warnings)


def test_runtime_guard_sixty_products():
    recipes = []
    demand = {}
    prices = {9000: 1.0}
    on_hand = {9000: 1_000_000.0}
    for i in range(60):
        pid = 1000 + i
        # Every 3rd product is a composite that consumes the previous intermediate.
        if i > 0 and i % 3 == 0:
            prev = 1000 + i - 1
            recipes.append(_recipe(
                f"c{i}", pid, seconds=3600, fee=100.0 + i, formulas=5, fit=10,
                outputs={pid: 100.0}, inputs={prev: 50.0, 9000: 1.0},
                formula_price=5_000_000.0,
            ))
            prices[prev] = 50_000.0
        else:
            recipes.append(_recipe(
                f"r{i}", pid, seconds=3600, fee=100.0 + i, formulas=3, fit=10,
                outputs={pid: 100.0}, inputs={9000: 1.0},
                formula_price=5_000_000.0,
            ))
        demand[pid] = 500.0
        prices[pid] = 100_000.0 + 1000 * i
        # A few alchemy alternatives (~80 recipes with these).
        if i % 5 == 0:
            recipes.append(_recipe(
                f"a{i}", pid, seconds=3600 * 5, fee=500.0, formulas=2, fit=2,
                outputs={pid: 20.0}, inputs={9000: 2.0},
                formula_price=2_000_000.0, blueprint=5000 + i,
            ))

    layer = ReactionLayer(recipes=recipes)
    assert len(recipes) >= 70
    window = _window(demand, slots=200, slots2=200, on_hand=on_hand, prices=prices)

    t0 = time.perf_counter()
    plan = solve(layer, window)
    elapsed = time.perf_counter() - t0

    # The 5 s target is measured on production (plan section 1.6); this runs
    # next to the whole suite under pytest-xdist, so the wall-clock bound is
    # loose and the solver's own time-limit warning is the real guard.
    assert elapsed < 20.0, f"solve took {elapsed:.2f}s (limit 20s under parallel test load)"
    assert not any("time limit" in w.lower() for w in plan.warnings)
    assert plan.jobs_used >= 0
    assert plan.next_window is not None
    for comp in plan.chaining:
        assert comp.isk_saved_by_waiting >= 0


# ------------------------------------------------------------------ Buy List reason
def _bought_plan(*, jobs_used: int, slots: int, chained: bool) -> tuple[ReactionLayer, ReactionPlan]:
    layer = ReactionLayer(recipes=[_recipe("normal:20", 20, inputs={10: 1.0})])
    chaining = (ChainingComparison(
        product_type_id=20, buy_now_cost=1.0, wait_cost=1.0, isk_saved_by_waiting=0.0,
        units_delayed=100.0, preferred="buy", short_input_ids=(10,)),) if chained else ()
    plan = ReactionPlan(
        recipes=(), buy={20: 100.0}, total_cost=0.0, slot_shadow_price=0.0,
        formula_shadow_prices={}, warnings=(), chaining=chaining,
        jobs_used=jobs_used, slots=slots)
    return layer, plan


@pytest.mark.parametrize("jobs_used, slots, chained, expected", [
    (2, 10, True, "inputs"),      # slots free, inputs short at the window
    (10, 10, True, "capacity"),   # slots full: capacity wins
    (2, 10, False, "capacity"),   # free slots but no input shortage (formulas)
])
def test_buy_reason_separates_missing_inputs_from_capacity(monkeypatch, jobs_used, slots, chained, expected):
    from eve_trader.production import engine, pricing
    monkeypatch.setattr(pricing, "buy_price", lambda *a, **k: 1000.0)
    monkeypatch.setattr(engine, "_haul_volume", lambda type_id, cfg: 0.0)
    layer, plan = _bought_plan(jobs_used=jobs_used, slots=slots, chained=chained)

    _runs, buys, reason, given_up, _keys = apply_plan_to_build_buy(
        layer, plan, cost_memo={20: 400.0}, home={}, jita={}, cfg=ProductionConfig())

    assert buys[20] == 100.0
    assert reason[20] == expected
    assert given_up[20] == pytest.approx(60_000.0)


# ------------------------------------------------------------------ review fixes
def test_uncoverable_w1_demand_does_not_make_the_model_infeasible():
    # Product 2 is a manual Build without any formula: W1 cannot cover it.
    # Before the fix the W2 input row had no short1, the model was infeasible
    # and product 1 was bought in full although it fits the slots.
    a = _recipe("normal:1", 1, fee=10.0, inputs={11: 100.0}, outputs={1: 200.0},
                fit=100, formulas=5, formula_price=None)
    b = _recipe("normal:2", 2, fee=10.0, inputs={12: 100.0}, outputs={2: 200.0},
                fit=100, formulas=0, formula_price=None)
    window = _window(
        {1: 40_000.0, 2: 1_000.0}, slots=10,
        prices={1: 1000.0, 2: 1000.0, 11: 100.0, 12: 100.0},
        manual_build=frozenset({2}),
    )

    plan = solve(ReactionLayer(recipes=[a, b]), window)

    jobs = {r.recipe_key: r.jobs for r in plan.recipes}
    assert jobs.get("normal:1", 0) >= 2
    assert plan.buy.get(1, 0.0) < 1e-6
    assert plan.unmet_demand.get(2, 0.0) > 0
    assert not any("infeasible" in w.lower() for w in plan.warnings)


def test_unmet_demand_penalty_does_not_loosen_the_slot_choice():
    # The relative MIP gap used to include the 1e12 short penalty, so one
    # unrelated unmet unit let the solver keep a far worse slot mix.
    recipes, prices, demand = [], {}, {}
    for i in range(40):
        pid, inp = 1000 + i, 5000 + i
        recipes.append(_recipe(f"normal:{pid}", pid, fee=1000.0, inputs={inp: 100.0},
                               outputs={pid: 200.0}, fit=100, formulas=50, formula_price=None))
        prices[pid] = 1000.0 + 37 * i
        prices[inp] = 500.0
        demand[pid] = 30_000.0
    layer = ReactionLayer(recipes=recipes)
    base = solve(layer, _window(dict(demand), slots=30, prices=prices))
    with_short = solve(layer, _window({**demand, 999: 10.0}, slots=30, prices=prices))

    def chosen(plan):
        return sorted(r.recipe_key for r in plan.recipes if r.jobs)

    assert chosen(with_short) == chosen(base)
    assert with_short.total_cost == pytest.approx(base.total_cost, abs=1e6)


def _carry_layer():
    # 100 A -> 100 P, no fee: building P uses A and nothing else.
    recipe = _recipe("normal:1", 1, fee=0.0, inputs={50: 100.0}, outputs={1: 100.0},
                     fit=100, formulas=5, formula_price=None)
    return ReactionLayer(recipes=[recipe])


def _carry_window(carry_need):
    return ReactionWindow(
        slots=5, on_hand={50: 1000.0}, demand={1: 1000.0},
        prices={1: 10.0, 50: 15.0}, demand_factor=0.0, carry_need=carry_need,
    )


def test_stock_free_to_use_without_carry_need():
    plan = solve(_carry_layer(), _carry_window({}))
    assert sum(r.jobs for r in plan.recipes) > 0
    assert plan.buy.get(1, 0.0) < 1e-6


def test_stock_the_next_cycle_needs_is_charged_at_buy_price():
    # Using 1000 A costs 15,000 to replace; buying 1000 P costs 10,000.
    plan = solve(_carry_layer(), _carry_window({50: 1000.0}))
    assert sum(r.jobs for r in plan.recipes) == 0
    assert plan.buy.get(1, 0.0) == pytest.approx(1000.0)


def test_stock_above_the_next_cycle_need_stays_free():
    # Owned 1000, next cycle needs 0 of it beyond this window: still free.
    window = ReactionWindow(
        slots=5, on_hand={50: 2000.0}, demand={1: 1000.0},
        prices={1: 10.0, 50: 15.0}, demand_factor=0.0, carry_need={50: 1000.0},
    )
    plan = solve(_carry_layer(), window)
    assert sum(r.jobs for r in plan.recipes) > 0
    assert plan.buy.get(1, 0.0) < 1e-6


def test_bought_units_earn_no_carry_credit_on_todays_buy_list():
    # A purchase made only to keep stock must never reach today's Buy List.
    plan = solve(_carry_layer(), _carry_window({50: 1000.0}))
    assert plan.buy.get(50, 0.0) < 1e-6


def test_carry_need_expands_demand_through_normal_recipes():
    from eve_trader.production.reaction_capacity import carry_need_from_layer
    composite = _recipe("normal:3", 3, inputs={2: 2.0}, outputs={3: 1.0})
    intermediate = _recipe("normal:2", 2, inputs={50: 3.0}, outputs={2: 1.0})
    alchemy = _recipe("alchemy:2", 2, inputs={60: 99.0}, outputs={2: 1.0}, blueprint=7000)
    layer = ReactionLayer(recipes=[composite, intermediate, alchemy])

    need = carry_need_from_layer(layer, {3: 10.0}, factor=2.0)

    assert need == pytest.approx({3: 20.0, 2: 40.0, 50: 120.0})


def test_build_window_counts_non_reaction_incoming_with_a_window_date(monkeypatch):
    # With a window date, window_state splits reaction jobs only. Fuel block
    # jobs and manual incoming used to be dropped; they belong in incoming1.
    from eve_trader.production import engine
    from eve_trader.production import reaction_capacity as rc

    fuel, reaction_product = 4051, 1
    hangar = {fuel: 100.0, reaction_product: 10.0}
    current = {fuel: 160.0, reaction_product: 999.0}  # 999 includes reaction jobs
    monkeypatch.setattr(engine, "classify_activity", lambda t: (
        ("Reaction", (91, 11, 100)) if t == reaction_product else ("Manufacturing", (92, 1, 40))))
    monkeypatch.setattr(engine, "_stock_on_hand", lambda t, *a, **k: hangar.get(t, 0.0))
    monkeypatch.setattr(engine, "_current_stock", lambda t, *a, **k: current.get(t, 0.0))
    monkeypatch.setattr(engine, "_haul_volume", lambda *a, **k: 1.0)
    monkeypatch.setattr(engine.pricing, "buy_price", lambda *a, **k: 10.0)
    monkeypatch.setattr(storage, "manual_incoming_qty",
                        lambda t: 7.0 if t == reaction_product else 0.0)
    cfg = ProductionConfig(reaction_window_next="2999-01-01T00:00:00+00:00")
    layer = ReactionLayer(recipes=[_recipe(
        "normal:1", reaction_product, inputs={fuel: 5.0}, outputs={reaction_product: 100.0})])

    window, _state = rc.build_window(
        cfg, {reaction_product: 500.0}, layer, home={}, jita={}, cost_memo={},
        manual_overrides={}, manual_stock={}, character_rows=[], industry_jobs=[],
    )

    assert window.incoming1[fuel] == pytest.approx(60.0)
    assert window.incoming1[reaction_product] == pytest.approx(7.0)
    assert window.on_hand[fuel] == pytest.approx(100.0)
    assert window.carry_need[fuel] == pytest.approx(25.0)


def test_lp_rounding_replaces_a_poor_time_limited_incumbent(monkeypatch):
    from eve_trader.production import reaction_capacity as rc

    recipes, prices, demand = [], {9000: 1.0}, {}
    for i in range(30):
        pid = 1000 + i
        recipes.append(_recipe(f"normal:{pid}", pid, fee=100.0, fit=10, formulas=3,
                               outputs={pid: 100.0}, inputs={9000: 1.0},
                               formula_price=5_000_000.0))
        demand[pid] = 5000.0
        prices[pid] = 100_000.0 + 1000 * i
    layer = ReactionLayer(recipes=recipes)
    window = _window(demand, slots=50, on_hand={9000: 1e6}, prices=prices)
    reference = solve(layer, window)

    monkeypatch.setattr(rc, "_MIP_TIME_LIMIT_SECONDS", 1e-6)
    plan = solve(layer, window)

    assert any("rounded to whole jobs" in w for w in plan.warnings)
    assert plan.jobs_used <= 50
    assert plan.jobs_used >= 45
    assert plan.total_cost <= reference.total_cost * 1.02
