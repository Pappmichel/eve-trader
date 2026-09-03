"""Tests for eve_trader/refining/optimizer.py - GitHub issue #93's multi-ore
buy-vs-refine LP. Fully pure: the optimizer takes pre-fetched prices/yields,
so nothing here touches storage/ESI/Postgres (same shape as
test_refining_pricing.py's monkeypatched-input tests).
"""
import itertools
import math
import random
import time

import pytest

from eve_trader.refining.models import MineralOption, MineralRequirement, OreOption
from eve_trader.refining.optimizer import OptimizationError, optimize_shopping_list

TRIT, PYE, MEX = 34, 35, 36


def _ore(type_id=1, item="Compressed Veldspar", family="Veldspar", portion_size=100,
         landed_cost_per_unit=10.0, yields=None, volume_m3=0.15):
    return OreOption(type_id=type_id, item=item, family=family, is_ice=False, volume_m3=volume_m3,
                      portion_size=portion_size, landed_cost_per_unit=landed_cost_per_unit,
                      yield_per_portion=yields or {TRIT: 400})


def _mineral(type_id, name, price):
    return MineralOption(type_id=type_id, name=name, landed_cost_per_unit=price)


def _req(type_id, name, qty):
    return MineralRequirement(type_id=type_id, name=name, required_qty=qty)


def test_buys_ore_when_refining_is_cheaper_than_the_mineral():
    # One portion = 1000 ISK for 400 Tritanium (2.5 ISK/unit) vs. 6 ISK/unit direct.
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 6.0)})
    assert [p.type_id for p in plan.ore_purchases] == [1]
    assert plan.ore_purchases[0].portions == 10
    assert plan.ore_purchases[0].units == 1000
    assert plan.direct_purchases == []
    assert plan.total_cost == pytest.approx(10_000.0)


def test_buys_the_mineral_outright_when_ore_is_more_expensive():
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [_ore(landed_cost_per_unit=10.0)],
                                   {TRIT: _mineral(TRIT, "Tritanium", 1.0)})
    assert plan.ore_purchases == []
    assert [p.quantity for p in plan.direct_purchases] == [4000]
    assert plan.total_cost == pytest.approx(4000.0)


def test_mixes_ore_and_direct_purchase_when_that_is_cheapest():
    """The ore is a cheap source of Tritanium but yields no Mexallon at all,
    so the optimal plan is ore for one mineral + direct buy for the other -
    the case a single "best ore per mineral" heuristic gets right only by
    accident."""
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 4000), _req(MEX, "Mexallon", 100)],
        [_ore(yields={TRIT: 400})],
        {TRIT: _mineral(TRIT, "Tritanium", 6.0), MEX: _mineral(MEX, "Mexallon", 50.0)},
    )
    assert plan.ore_purchases[0].portions == 10
    assert [(p.type_id, p.quantity) for p in plan.direct_purchases] == [(MEX, 100)]
    assert plan.total_cost == pytest.approx(10_000.0 + 5_000.0)


def test_picks_the_cheaper_of_two_ores_for_the_same_mineral():
    cheap = _ore(type_id=1, item="Cheap", landed_cost_per_unit=5.0, yields={TRIT: 400})
    pricey = _ore(type_id=2, item="Pricey", landed_cost_per_unit=20.0, yields={TRIT: 400})
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [pricey, cheap],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert [p.item for p in plan.ore_purchases] == ["Cheap"]


def test_combines_two_ores_whose_ratios_suit_different_minerals():
    """Real multi-ore optimization: neither ore alone is the cheapest way to
    cover BOTH minerals - a greedy per-mineral ranking would buy far more of
    whichever ore it ranked first."""
    tri_ore = _ore(type_id=1, item="TritOre", landed_cost_per_unit=1.0, portion_size=100,
                    yields={TRIT: 1000, MEX: 1})
    mex_ore = _ore(type_id=2, item="MexOre", landed_cost_per_unit=1.0, portion_size=100,
                    yields={TRIT: 1, MEX: 1000})
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 10_000), _req(MEX, "Mexallon", 10_000)],
        [tri_ore, mex_ore],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 99.0)},
    )
    by_item = {p.item: p.portions for p in plan.ore_purchases}
    assert by_item == {"TritOre": 10, "MexOre": 10}
    assert plan.direct_purchases == []


def test_rounds_ore_up_to_whole_portions_never_down():
    # 401 Tritanium needs 1.0025 portions - buying 1 would under-deliver, and
    # with nothing listed in Jita there's no direct top-up to fall back on.
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 401)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", None)})
    assert plan.ore_purchases[0].portions == 2
    assert plan.coverage[0].delivered == 800
    assert plan.coverage[0].surplus == pytest.approx(399)


def test_trims_a_rounded_up_portion_when_buying_the_gap_is_cheaper():
    """1000 ISK for a whole spare portion vs. 1 unit of Tritanium at 99 ISK -
    the rounded-up plan is trimmed back and the gap bought outright."""
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 401)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert plan.ore_purchases[0].portions == 1
    assert [(p.type_id, p.quantity) for p in plan.direct_purchases] == [(TRIT, 1)]
    assert plan.total_cost == pytest.approx(1099.0)
    assert plan.coverage[0].delivered >= 401


def test_trimming_never_drops_below_a_requirement_with_no_direct_price():
    """A tiny Mexallon requirement (worth a fraction of a portion) still has to
    be covered by ore when nothing lists Mexallon - the trim pass must not
    "save" money by leaving it short."""
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 400), _req(MEX, "Mexallon", 1)],
        [_ore(yields={TRIT: 400}), _ore(type_id=2, item="MexOre", landed_cost_per_unit=500.0,
                                         yields={MEX: 50})],
        {TRIT: _mineral(TRIT, "Tritanium", 1.0), MEX: _mineral(MEX, "Mexallon", None)},
    )
    assert {c.type_id: c.delivered for c in plan.coverage}[MEX] >= 1
    assert any(p.item == "MexOre" for p in plan.ore_purchases)


def test_rounding_never_leaves_a_requirement_uncovered():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 1234), _req(PYE, "Pyerite", 567)],
        [_ore(yields={TRIT: 400, PYE: 90})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), PYE: _mineral(PYE, "Pyerite", 99.0)},
    )
    for coverage in plan.coverage:
        assert coverage.delivered >= coverage.required


def test_exact_portion_multiple_does_not_buy_a_spare_portion():
    """Solver noise (400.0000000001 portions' worth) must not round up."""
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert plan.ore_purchases[0].portions == 10
    assert plan.coverage[0].surplus == pytest.approx(0.0)


def test_direct_purchase_tops_up_what_the_rounded_ore_still_misses():
    """The ore is only worth buying for Tritanium; its trickle of Pyerite
    leaves a gap the plan closes with a direct buy."""
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 4000), _req(PYE, "Pyerite", 1000)],
        [_ore(yields={TRIT: 400, PYE: 10})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), PYE: _mineral(PYE, "Pyerite", 2.0)},
    )
    direct = {p.type_id: p.quantity for p in plan.direct_purchases}
    assert direct == {PYE: 900}
    assert {c.type_id: c.from_ore for c in plan.coverage}[PYE] == 100


def test_repairs_a_gap_for_a_mineral_with_no_direct_price():
    """A mineral nothing lists in Jita can only come from ore - the plan has
    to buy enough ore, even though the LP's own rounding wouldn't be checked
    against a direct top-up."""
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 4000), _req(PYE, "Pyerite", 1000)],
        [_ore(yields={TRIT: 400, PYE: 10})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), PYE: _mineral(PYE, "Pyerite", None)},
    )
    assert plan.direct_purchases == []
    assert plan.ore_purchases[0].portions == 100
    assert {c.type_id: c.delivered for c in plan.coverage}[PYE] >= 1000


def test_unsourceable_mineral_raises():
    with pytest.raises(OptimizationError, match="No way to source Morphite"):
        optimize_shopping_list([_req(11399, "Morphite", 10)], [_ore()],
                                {11399: _mineral(11399, "Morphite", None)})


def test_empty_requirements_raises():
    with pytest.raises(OptimizationError, match="No mineral requirements"):
        optimize_shopping_list([], [_ore()], {})


def test_zero_quantity_requirements_are_ignored():
    with pytest.raises(OptimizationError, match="No mineral requirements"):
        optimize_shopping_list([_req(TRIT, "Tritanium", 0)], [_ore()],
                                {TRIT: _mineral(TRIT, "Tritanium", 1.0)})


def test_ore_yielding_nothing_requested_is_never_bought():
    useless = _ore(type_id=9, item="Useless", landed_cost_per_unit=0.0001, yields={MEX: 500})
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [useless, _ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert [p.item for p in plan.ore_purchases] == ["Compressed Veldspar"]


def test_all_direct_baseline_and_savings_are_reported():
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 6.0)})
    assert plan.all_direct_cost == pytest.approx(24_000.0)
    assert plan.savings_vs_all_direct == pytest.approx(24_000.0 - plan.total_cost)


def test_all_direct_baseline_is_none_when_a_mineral_has_no_price():
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 400)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", None)})
    assert plan.all_direct_cost is None
    assert plan.savings_vs_all_direct is None


def test_lp_cost_is_the_continuous_optimum_below_the_rounded_total():
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 401)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert plan.lp_cost < plan.total_cost
    assert plan.lp_cost == pytest.approx(401 / 400 * 1000.0)


def test_totals_and_volume_add_up():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 4000), _req(MEX, "Mexallon", 100)],
        [_ore(volume_m3=0.15)],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 50.0)},
    )
    assert plan.total_cost == pytest.approx(plan.ore_cost + plan.direct_cost)
    assert plan.total_volume_m3 == pytest.approx(1000 * 0.15)


def test_coverage_is_reported_for_every_requested_mineral_sorted_by_name():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 400), _req(MEX, "Mexallon", 10)],
        [_ore(yields={TRIT: 400, MEX: 10})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 99.0)},
    )
    assert [c.name for c in plan.coverage] == ["Mexallon", "Tritanium"]
    assert all(c.delivered == c.from_ore + c.from_direct for c in plan.coverage)


def test_ore_purchases_are_sorted_most_expensive_first():
    a = _ore(type_id=1, item="A", landed_cost_per_unit=1.0, yields={TRIT: 400})
    b = _ore(type_id=2, item="B", landed_cost_per_unit=1.0, yields={MEX: 400})
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 40_000), _req(MEX, "Mexallon", 400)],
        [a, b], {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 99.0)},
    )
    costs = [p.total_cost for p in plan.ore_purchases]
    assert costs == sorted(costs, reverse=True)


def test_ore_with_zero_portion_size_is_skipped():
    broken = _ore(type_id=7, item="Broken", portion_size=0)
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 400)], [broken, _ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert [p.item for p in plan.ore_purchases] == ["Compressed Veldspar"]


# ---------------------------------------------------------------------------
# Regression test for the relax-then-round optimality gap (fixed by solving
# with `linprog`'s `integrality` param instead - see optimizer.py's module
# docstring, decision 1). The old code only ever *dropped* portions from the
# ore set the continuous relaxation gave non-zero weight to, so it could
# never discover that a single portion of a relaxation-excluded ore was the
# true whole-unit optimum. This brute-forces every small case's real answer
# independently (by enumerating every whole-portion/whole-unit combination,
# using the exact same "ceil the direct-buy gap" pricing the real app uses)
# and asserts the solver actually finds it - not just a feasible plan.

def _real_cost(ores, portions, mineral_ids, required, direct_price):
    """Mirrors optimizer.py's own real-world costing: portions*ore price plus
    a whole-unit ceil'd direct purchase for whatever gap is left - the same
    arithmetic `optimize_shopping_list` uses to build the plan it returns."""
    delivered = {m: 0 for m in mineral_ids}
    cost = 0.0
    for qty, ore in zip(portions, ores):
        cost += qty * ore.landed_cost_per_portion
        for m in mineral_ids:
            delivered[m] += qty * ore.yield_per_portion.get(m, 0)
    for m in mineral_ids:
        gap = required[m] - delivered[m]
        if gap > 1e-9:
            if m not in direct_price:
                return None  # infeasible: nothing left to cover this mineral
            cost += math.ceil(gap - 1e-9) * direct_price[m]
    return cost


def _brute_force_optimum(ores, mineral_ids, required, direct_price):
    # A single portion can yield as little as 1 unit of a mineral (see
    # _random_case), so the ceiling on portions needed has to scale with the
    # largest requirement, not a fixed small constant - otherwise brute force
    # itself misses the true (and only) feasible combination.
    max_portion = max(1, math.ceil(max(required.values())))
    best = None
    for combo in itertools.product(range(max_portion + 1), repeat=len(ores)):
        cost = _real_cost(ores, combo, mineral_ids, required, direct_price)
        if cost is None:
            continue
        if best is None or cost < best:
            best = cost
    return best


def _random_case(rng):
    """A small synthetic ore/requirement scenario, deliberately sized so
    `_brute_force_optimum` can exhaustively enumerate it (<=3 ores, each
    portion size 1 so "portions" and "real units" coincide)."""
    minerals = rng.sample([TRIT, PYE, MEX, 37], rng.randint(1, 2))
    ores = []
    for i in range(rng.randint(1, 3)):
        yields = {m: rng.randint(1, 20) for m in minerals if rng.random() < 0.8}
        if not yields:
            yields = {minerals[0]: rng.randint(1, 20)}
        ores.append(_ore(type_id=i + 1, item=f"Ore{i}", portion_size=1,
                          landed_cost_per_unit=rng.uniform(1, 50), yields=yields))
    # Kept small deliberately: `_brute_force_optimum` enumerates every
    # portions^n_ores combination, so this needs to stay cheap enough to run
    # 200 times in a normal test suite while still being large enough that a
    # relaxation-excluded ore can plausibly be the true optimum.
    required = {m: rng.uniform(1, 20) for m in minerals}
    direct_price = {m: rng.uniform(0.5, 10) for m in minerals if rng.random() < 0.7}
    reachable = all(m in direct_price or any(o.yield_per_portion.get(m, 0) > 0 for o in ores)
                     for m in minerals)
    if not reachable:
        return None
    return ores, minerals, required, direct_price


def test_matches_brute_force_optimum_on_random_small_cases():
    rng = random.Random(20260903)  # fixed seed: deterministic, reproducible failures
    checked = 0
    while checked < 200:
        case = _random_case(rng)
        if case is None:
            continue
        ores, minerals, required, direct_price = case
        checked += 1

        reqs = [_req(m, str(m), required[m]) for m in minerals]
        mineral_options = {m: _mineral(m, str(m), direct_price.get(m)) for m in minerals}
        plan = optimize_shopping_list(reqs, ores, mineral_options)

        # Every requirement must still be covered - the one thing that must
        # never regress, fix or no fix.
        for coverage in plan.coverage:
            assert coverage.delivered + 1e-6 >= coverage.required

        optimum = _brute_force_optimum(ores, minerals, required, direct_price)
        assert optimum is not None, "brute force found no feasible combination at all"
        # Within float tolerance of the TRUE discrete optimum, not merely
        # feasible - this is what the old relax-then-round approach failed at
        # roughly 8% of the time (see optimizer.py's module docstring).
        assert plan.total_cost <= optimum + 1e-6, (
            f"plan cost {plan.total_cost} exceeds true optimum {optimum} "
            f"(case: ores={ores}, required={required}, direct_price={direct_price})"
        )


def test_realistic_scale_solves_quickly():
    """Sanity-checks solve time at the tool's realistic scale: the full
    SDE-derived compressed ore/ice universe is on the order of dozens of
    types (candidate_discovery.build_ore_candidate_universe), and a build's
    mineral requirements never exceed the 8 real EVE minerals. This is
    called at request time (refining/actions.py's
    do_optimize_mineral_shopping_list), so it needs to stay fast even though
    it's now a real MIP rather than a relaxed LP."""
    rng = random.Random(1)
    minerals = [34, 35, 36, 37, 38, 39, 40, 11399]  # the 8 real EVE minerals
    mineral_price = {m: rng.uniform(1, 80) for m in minerals}
    ores = []
    for i in range(60):
        yields = {m: rng.randint(50, 3000) for m in minerals if rng.random() < 0.4}
        if not yields:
            yields = {rng.choice(minerals): rng.randint(50, 3000)}
        # Ore price roughly tracks its refined mineral value (as real market
        # prices do) plus noise - the structure that actually stresses a MIP
        # solver (many near-tied choices), not pure uniform randomness.
        base_value = sum(qty * mineral_price[m] for m, qty in yields.items())
        markup = rng.uniform(0.85, 1.15)
        ores.append(_ore(type_id=i + 1, item=f"Ore{i}", portion_size=100,
                          landed_cost_per_unit=base_value * markup / 100, yields=yields))
    required = {m: rng.uniform(10_000, 2_000_000) for m in minerals}
    reqs = [_req(m, str(m), required[m]) for m in minerals]
    mineral_options = {m: _mineral(m, str(m), mineral_price[m] * rng.uniform(0.95, 1.3)) for m in minerals}

    start = time.monotonic()
    plan = optimize_shopping_list(reqs, ores, mineral_options)
    elapsed = time.monotonic() - start

    for coverage in plan.coverage:
        assert coverage.delivered + 1e-6 >= coverage.required
    # Generous ceiling - a bit above optimizer.py's own `_MIP_TIME_LIMIT_SECONDS`
    # (5s) worst-case solver cutoff, so this fails loudly rather than the
    # solver silently eating its own timeout every run. Typical real solves
    # are well under 1s; this only guards against the realistic-scale case
    # regressing to multi-second territory unnoticed.
    assert elapsed < 8.0, (
        f"realistic-scale solve took {elapsed:.2f}s - see optimizer.py's "
        "module docstring on MIP solve time before assuming this is fine"
    )
