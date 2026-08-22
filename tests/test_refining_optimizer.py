"""Tests for eve_trader/refining/optimizer.py - GitHub issue #93's multi-ore
buy-vs-refine LP. Fully pure: the optimizer takes pre-fetched prices/yields,
so nothing here touches storage/ESI/Postgres (same shape as
test_refining_pricing.py's monkeypatched-input tests).
"""
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
