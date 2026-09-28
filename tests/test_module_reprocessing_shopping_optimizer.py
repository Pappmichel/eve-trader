"""Tests for eve_trader/module_reprocessing/shopping_optimizer.py - Module
Reprocessing's Mineral Shopping List LP/MIP, which mixes compressed ore/ice
and modules/drones as reprocessing sources in one plan. A port of
test_refining_optimizer.py's meaningful cases, plus mixed ore+module cases.
Fully pure: nothing here touches storage/ESI/Postgres.
"""
import itertools
import math
import random
import time

import pytest

from eve_trader.module_reprocessing.models import MineralOption, MineralRequirement, ReprocessOption
from eve_trader.module_reprocessing.shopping_optimizer import OptimizationError, optimize_shopping_list

TRIT, PYE, MEX = 34, 35, 36


def _ore(type_id=1, item="Compressed Veldspar", family="Veldspar", portion_size=100,
         landed_cost_per_unit=10.0, yields=None, volume_m3=0.15):
    return ReprocessOption(type_id=type_id, item=item, category="ore", family=family, is_ice=False,
                            volume_m3=volume_m3, portion_size=portion_size,
                            landed_cost_per_unit=landed_cost_per_unit, yield_per_portion=yields or {TRIT: 400})


def _module(type_id=100, item="200mm AutoCannon I", portion_size=1, landed_cost_per_unit=500.0,
            yields=None, volume_m3=5.0):
    return ReprocessOption(type_id=type_id, item=item, category="module", family=None, is_ice=False,
                            volume_m3=volume_m3, portion_size=portion_size,
                            landed_cost_per_unit=landed_cost_per_unit, yield_per_portion=yields or {TRIT: 100})


def _mineral(type_id, name, price):
    return MineralOption(type_id=type_id, name=name, landed_cost_per_unit=price)


def _req(type_id, name, qty):
    return MineralRequirement(type_id=type_id, name=name, required_qty=qty)


# ------------------------------------------------------------ ore only
def test_ore_only_buys_ore_when_refining_is_cheaper_than_the_mineral():
    # One portion = 1000 ISK for 400 Tritanium (2.5 ISK/unit) vs. 6 ISK/unit direct.
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 6.0)})
    assert [(p.type_id, p.category, p.family) for p in plan.reprocess_purchases] == [(1, "ore", "Veldspar")]
    assert plan.reprocess_purchases[0].portions == 10
    assert plan.reprocess_purchases[0].units == 1000
    assert plan.direct_purchases == []
    assert plan.total_cost == pytest.approx(10_000.0)


def test_buys_the_mineral_outright_when_every_source_is_more_expensive():
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)],
                                   [_ore(landed_cost_per_unit=10.0), _module(landed_cost_per_unit=500.0)],
                                   {TRIT: _mineral(TRIT, "Tritanium", 1.0)})
    assert plan.reprocess_purchases == []
    assert [p.quantity for p in plan.direct_purchases] == [4000]
    assert plan.total_cost == pytest.approx(4000.0)


# --------------------------------------------------------- modules only
def test_module_only_buys_modules_when_reprocessing_them_is_cheaper():
    # 200 ISK per module for 100 Tritanium (2 ISK/unit) vs. 6 ISK/unit direct.
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 1000)], [_module(landed_cost_per_unit=200.0)],
                                   {TRIT: _mineral(TRIT, "Tritanium", 6.0)})
    assert len(plan.reprocess_purchases) == 1
    purchase = plan.reprocess_purchases[0]
    assert (purchase.category, purchase.family, purchase.is_ice) == ("module", None, False)
    assert purchase.portions == purchase.units == 10
    assert purchase.volume_m3 == pytest.approx(50.0)
    assert plan.total_cost == pytest.approx(2000.0)


# ---------------------------------------------------------- mixed sources
def test_picks_a_module_over_ore_when_the_module_is_the_cheaper_source():
    ore = _ore(landed_cost_per_unit=10.0, yields={TRIT: 400})          # 2.5 ISK/Trit
    module = _module(landed_cost_per_unit=150.0, yields={TRIT: 100})  # 1.5 ISK/Trit
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 1000)], [ore, module],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert [p.category for p in plan.reprocess_purchases] == ["module"]
    assert plan.reprocess_cost == pytest.approx(1500.0)


def test_combines_ore_and_modules_whose_ratios_suit_different_minerals():
    """Real cross-kind optimization: ore is the cheap Tritanium source, a
    module the cheap Mexallon source - the optimum needs both kinds at once,
    which neither an ore-only nor a module-only tool could produce."""
    ore = _ore(type_id=1, item="TritOre", landed_cost_per_unit=1.0, portion_size=100, yields={TRIT: 1000, MEX: 1})
    module = _module(type_id=100, item="MexModule", landed_cost_per_unit=100.0, yields={TRIT: 1, MEX: 1000})
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 10_000), _req(MEX, "Mexallon", 10_000)],
        [ore, module],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 99.0)},
    )
    by_item = {(p.item, p.category): p.portions for p in plan.reprocess_purchases}
    assert by_item == {("TritOre", "ore"): 10, ("MexModule", "module"): 10}
    assert plan.direct_purchases == []
    assert plan.reprocess_cost == pytest.approx(10 * 100.0 + 10 * 100.0)


def test_mixes_reprocessing_and_direct_purchase_when_that_is_cheapest():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 4000), _req(MEX, "Mexallon", 100)],
        [_ore(yields={TRIT: 400}), _module(landed_cost_per_unit=9999.0, yields={MEX: 1})],
        {TRIT: _mineral(TRIT, "Tritanium", 6.0), MEX: _mineral(MEX, "Mexallon", 50.0)},
    )
    assert [(p.category, p.portions) for p in plan.reprocess_purchases] == [("ore", 10)]
    assert [(p.type_id, p.quantity) for p in plan.direct_purchases] == [(MEX, 100)]
    assert plan.total_cost == pytest.approx(10_000.0 + 5_000.0)


def test_a_single_module_can_cover_a_small_gap_cheaper_than_a_whole_ore_portion():
    """Whole-portion discreteness across kinds: 401 Tritanium with ore in
    400-unit portions (1000 ISK each, 2.5 ISK/Trit) - the module is the
    pricier source per unit (20 ISK for 5 Trit, 4 ISK/Trit), yet one of them
    closes the 1-unit gap far cheaper than a second whole ore portion (or 81
    modules), and nothing is listed to buy directly."""
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 401)],
                                   [_ore(), _module(landed_cost_per_unit=20.0, yields={TRIT: 5})],
                                   {TRIT: _mineral(TRIT, "Tritanium", None)})
    by_category = {p.category: p.portions for p in plan.reprocess_purchases}
    assert by_category == {"ore": 1, "module": 1}
    assert plan.total_cost == pytest.approx(1020.0)


def test_coverage_counts_ore_and_module_deliveries_together():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 500)],
        [_ore(yields={TRIT: 400}), _module(landed_cost_per_unit=150.0, yields={TRIT: 100})],
        {TRIT: _mineral(TRIT, "Tritanium", None)},
    )
    coverage = plan.coverage[0]
    assert coverage.from_reprocessing == sum(
        p.portions * (400 if p.category == "ore" else 100) for p in plan.reprocess_purchases)
    assert coverage.delivered == coverage.from_reprocessing + coverage.from_direct
    assert coverage.delivered >= 500


# ------------------------------------------------ rounding / repair / errors
def test_rounds_up_to_whole_portions_never_down():
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 401)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", None)})
    assert plan.reprocess_purchases[0].portions == 2
    assert plan.coverage[0].delivered == 800
    assert plan.coverage[0].surplus == pytest.approx(399)


def test_buys_the_gap_outright_when_that_beats_a_spare_portion():
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 401)], [_ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert plan.reprocess_purchases[0].portions == 1
    assert [(p.type_id, p.quantity) for p in plan.direct_purchases] == [(TRIT, 1)]
    assert plan.total_cost == pytest.approx(1099.0)


def test_repairs_a_gap_for_a_mineral_with_no_direct_price():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 4000), _req(PYE, "Pyerite", 1000)],
        [_ore(yields={TRIT: 400, PYE: 10})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), PYE: _mineral(PYE, "Pyerite", None)},
    )
    assert plan.direct_purchases == []
    assert {c.type_id: c.delivered for c in plan.coverage}[PYE] >= 1000


def test_direct_purchase_only_when_no_source_reprocesses_into_the_mineral():
    """Neither the ore nor the module yields Mexallon at all - the plan still
    covers it, purely by buying it outright."""
    plan = optimize_shopping_list(
        [_req(MEX, "Mexallon", 250)],
        [_ore(yields={TRIT: 400}), _module(yields={PYE: 50})],
        {MEX: _mineral(MEX, "Mexallon", 40.0)},
    )
    assert plan.reprocess_purchases == []
    assert [(p.type_id, p.quantity) for p in plan.direct_purchases] == [(MEX, 250)]
    assert plan.coverage[0].from_reprocessing == 0
    assert plan.all_direct_cost == pytest.approx(plan.total_cost)


def test_unsourceable_mineral_raises():
    with pytest.raises(OptimizationError, match="No way to source Morphite"):
        optimize_shopping_list([_req(11399, "Morphite", 10)], [_ore(), _module()],
                                {11399: _mineral(11399, "Morphite", None)})


def test_empty_requirements_raises():
    with pytest.raises(OptimizationError, match="No mineral requirements"):
        optimize_shopping_list([], [_ore()], {})


def test_zero_quantity_requirements_are_ignored():
    with pytest.raises(OptimizationError, match="No mineral requirements"):
        optimize_shopping_list([_req(TRIT, "Tritanium", 0)], [_ore()], {TRIT: _mineral(TRIT, "Tritanium", 1.0)})


def test_source_yielding_nothing_requested_is_never_bought():
    useless = _module(type_id=9, item="Useless", landed_cost_per_unit=0.0001, yields={MEX: 500})
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 4000)], [useless, _ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert [p.item for p in plan.reprocess_purchases] == ["Compressed Veldspar"]


def test_source_with_zero_portion_size_is_skipped():
    broken = _module(type_id=7, item="Broken", portion_size=0, landed_cost_per_unit=0.01)
    plan = optimize_shopping_list([_req(TRIT, "Tritanium", 400)], [broken, _ore()],
                                   {TRIT: _mineral(TRIT, "Tritanium", 99.0)})
    assert [p.item for p in plan.reprocess_purchases] == ["Compressed Veldspar"]


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


def test_totals_and_volume_add_up_across_both_kinds():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 10_000), _req(MEX, "Mexallon", 1000)],
        [_ore(volume_m3=0.15, yields={TRIT: 1000}, landed_cost_per_unit=1.0),
         _module(volume_m3=5.0, landed_cost_per_unit=10.0, yields={MEX: 100})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 99.0)},
    )
    assert {p.category for p in plan.reprocess_purchases} == {"ore", "module"}
    assert plan.total_cost == pytest.approx(plan.reprocess_cost + plan.direct_cost)
    assert plan.total_volume_m3 == pytest.approx(1000 * 0.15 + 10 * 5.0)


def test_purchases_are_sorted_most_expensive_first():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 40_000), _req(MEX, "Mexallon", 400)],
        [_ore(type_id=1, item="A", landed_cost_per_unit=1.0, yields={TRIT: 400}),
         _module(type_id=2, item="B", landed_cost_per_unit=1.0, yields={MEX: 4})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 99.0)},
    )
    costs = [p.total_cost for p in plan.reprocess_purchases]
    assert costs == sorted(costs, reverse=True)


def test_coverage_is_reported_for_every_requested_mineral_sorted_by_name():
    plan = optimize_shopping_list(
        [_req(TRIT, "Tritanium", 400), _req(MEX, "Mexallon", 10)],
        [_ore(yields={TRIT: 400, MEX: 10})],
        {TRIT: _mineral(TRIT, "Tritanium", 99.0), MEX: _mineral(MEX, "Mexallon", 99.0)},
    )
    assert [c.name for c in plan.coverage] == ["Mexallon", "Tritanium"]


# ---------------------------------------------------------------------------
# Integer-vs-continuous correctness - see shopping_optimizer.py's decision 1
# and test_refining_optimizer.py's identical regression test. Brute-forces
# every small random mixed ore+module case's true discrete optimum and
# asserts the solver lands on it, not merely on a feasible plan.

def _real_cost(sources, portions, mineral_ids, required, direct_price):
    delivered = {m: 0 for m in mineral_ids}
    cost = 0.0
    for qty, source in zip(portions, sources):
        cost += qty * source.landed_cost_per_portion
        for m in mineral_ids:
            delivered[m] += qty * source.yield_per_portion.get(m, 0)
    for m in mineral_ids:
        gap = required[m] - delivered[m]
        if gap > 1e-9:
            if m not in direct_price:
                return None
            cost += math.ceil(gap - 1e-9) * direct_price[m]
    return cost


def _brute_force_optimum(sources, mineral_ids, required, direct_price):
    max_portion = max(1, math.ceil(max(required.values())))
    best = None
    for combo in itertools.product(range(max_portion + 1), repeat=len(sources)):
        cost = _real_cost(sources, combo, mineral_ids, required, direct_price)
        if cost is None:
            continue
        if best is None or cost < best:
            best = cost
    return best


def _random_case(rng):
    """<=3 sources, randomly ore or module, each portion size 1 so brute force
    can enumerate every combination exhaustively."""
    minerals = rng.sample([TRIT, PYE, MEX, 37], rng.randint(1, 2))
    sources = []
    for i in range(rng.randint(1, 3)):
        yields = {m: rng.randint(1, 20) for m in minerals if rng.random() < 0.8}
        if not yields:
            yields = {minerals[0]: rng.randint(1, 20)}
        make = _ore if rng.random() < 0.5 else _module
        sources.append(make(type_id=i + 1, item=f"Src{i}", portion_size=1,
                             landed_cost_per_unit=rng.uniform(1, 50), yields=yields))
    required = {m: rng.uniform(1, 20) for m in minerals}
    direct_price = {m: rng.uniform(0.5, 10) for m in minerals if rng.random() < 0.7}
    reachable = all(m in direct_price or any(s.yield_per_portion.get(m, 0) > 0 for s in sources)
                     for m in minerals)
    if not reachable:
        return None
    return sources, minerals, required, direct_price


def test_matches_brute_force_optimum_on_random_small_cases():
    rng = random.Random(20260928)  # fixed seed: deterministic, reproducible failures
    checked = 0
    while checked < 200:
        case = _random_case(rng)
        if case is None:
            continue
        sources, minerals, required, direct_price = case
        checked += 1

        reqs = [_req(m, str(m), required[m]) for m in minerals]
        mineral_options = {m: _mineral(m, str(m), direct_price.get(m)) for m in minerals}
        plan = optimize_shopping_list(reqs, sources, mineral_options)

        for coverage in plan.coverage:
            assert coverage.delivered + 1e-6 >= coverage.required

        optimum = _brute_force_optimum(sources, minerals, required, direct_price)
        assert optimum is not None, "brute force found no feasible combination at all"
        assert plan.total_cost <= optimum + 1e-6, (
            f"plan cost {plan.total_cost} exceeds true optimum {optimum} "
            f"(case: sources={sources}, required={required}, direct_price={direct_price})"
        )


def test_realistic_scale_solves_quickly():
    """This tool's column count is larger than Ore & Minerals' own: the full
    ore/ice universe (dozens of types) PLUS the module shortlist (hundreds,
    see ModuleReprocessingConfig.max_active_shortlist_items), against at most
    the 8 real minerals. Called at request time, so it must stay bounded.

    At this scale HiGHS' branch-and-bound can genuinely run into
    shopping_optimizer._MIP_TIME_LIMIT_SECONDS (measured ~4-5s on a slow
    sandbox with 400 module columns) - so beyond "fast enough" this also
    asserts the incumbent it returns is still essentially optimal (within
    0.1% of the continuous LP floor, measured ~0.01%), not just feasible."""
    rng = random.Random(1)
    minerals = [34, 35, 36, 37, 38, 39, 40, 11399]
    mineral_price = {m: rng.uniform(1, 80) for m in minerals}
    sources = []
    for i in range(60):
        yields = {m: rng.randint(50, 3000) for m in minerals if rng.random() < 0.4}
        if not yields:
            yields = {rng.choice(minerals): rng.randint(50, 3000)}
        base_value = sum(qty * mineral_price[m] for m, qty in yields.items())
        sources.append(_ore(type_id=i + 1, item=f"Ore{i}", portion_size=100,
                             landed_cost_per_unit=base_value * rng.uniform(0.85, 1.15) / 100, yields=yields))
    for i in range(400):
        yields = {m: rng.randint(1, 400) for m in minerals if rng.random() < 0.35}
        if not yields:
            yields = {rng.choice(minerals): rng.randint(1, 400)}
        base_value = sum(qty * mineral_price[m] for m, qty in yields.items())
        sources.append(_module(type_id=10_000 + i, item=f"Module{i}", portion_size=1,
                                landed_cost_per_unit=base_value * rng.uniform(0.85, 1.15), yields=yields))
    required = {m: rng.uniform(10_000, 2_000_000) for m in minerals}
    reqs = [_req(m, str(m), required[m]) for m in minerals]
    mineral_options = {m: _mineral(m, str(m), mineral_price[m] * rng.uniform(0.95, 1.3)) for m in minerals}

    start = time.monotonic()
    plan = optimize_shopping_list(reqs, sources, mineral_options)
    elapsed = time.monotonic() - start

    for coverage in plan.coverage:
        assert coverage.delivered + 1e-6 >= coverage.required
    assert {p.category for p in plan.reprocess_purchases} == {"ore", "module"}
    assert plan.total_cost <= plan.lp_cost * 1.001
    # A bit above shopping_optimizer.py's own 5s MIP time limit - see
    # test_refining_optimizer.py's identical assertion.
    assert elapsed < 8.0, f"realistic-scale solve took {elapsed:.2f}s"
