"""The integer solve runs in a killable worker process (see eve_trader/mip_worker.py):
HiGHS can ignore its own time limit, and a stuck worker must end in a usable plan."""
from __future__ import annotations

import pytest

from eve_trader import mineral_supply_lp as supply
from eve_trader.market_depth import SupplyTier

TRIT, PYE = 34, 35


def _sources():
    return [
        supply.SupplySource(portion_size=100, yield_per_portion={TRIT: 400, PYE: 50},
                            tiers=[SupplyTier(unit_cost=5.0, max_units=100_000), SupplyTier(unit_cost=6.0, max_units=None)]),
        supply.SupplySource(portion_size=100, yield_per_portion={PYE: 300},
                            tiers=[SupplyTier(unit_cost=4.0, max_units=None)]),
    ]


def _solve():
    required = {TRIT: 1_001.0, PYE: 777.0}
    direct = {TRIT: [SupplyTier(unit_cost=4.0, max_units=None)], PYE: [SupplyTier(unit_cost=9.0, max_units=None)]}
    return supply.solve([TRIT, PYE], required, _sources(), direct), required


def _delivered(solution, required):
    delivered = supply.delivered_from_sources(solution, _sources(), list(required))
    for (mineral_id, _t), units in solution.direct.items():
        delivered[mineral_id] += units
    return delivered


@pytest.fixture
def real_worker(monkeypatch):
    monkeypatch.setattr(supply, "_run_mip", supply._solve_mip_in_child)


def test_worker_process_returns_the_whole_unit_plan(real_worker):
    in_child, required = _solve()
    assert not in_child.approximate
    assert not in_child.short
    assert all(_delivered(in_child, required)[m] >= q for m, q in required.items())


def test_worker_matches_the_in_process_solve(real_worker, monkeypatch):
    in_child, _ = _solve()
    monkeypatch.setattr(supply, "_run_mip", lambda kwargs: supply.linprog(**kwargs).x)
    in_process, _ = _solve()
    assert in_child.portions == in_process.portions
    assert in_child.direct == in_process.direct


def test_killed_worker_falls_back_to_the_rounded_lp_plan(real_worker, monkeypatch):
    monkeypatch.setattr(supply, "MIP_PROCESS_DEADLINE_SECONDS", 0.001)
    solution, required = _solve()
    assert solution.approximate
    assert not solution.short
    assert all(_delivered(solution, required)[m] >= q for m, q in required.items())
    assert all(isinstance(v, int) and v > 0 for v in [*solution.portions.values(), *solution.direct.values()])


def test_no_integer_answer_falls_back_within_the_tier_caps(monkeypatch):
    monkeypatch.setattr(supply, "_run_mip", lambda kwargs: None)
    tier = SupplyTier(unit_cost=1.0, max_units=250)   # two whole portions of 100 at most
    source = supply.SupplySource(portion_size=100, yield_per_portion={TRIT: 10}, tiers=[tier])
    solution = supply.solve([TRIT], {TRIT: 15.0}, [source], {})
    assert solution.approximate
    assert solution.portions == {(0, 0): 2}
    assert not solution.short


def test_every_column_has_a_finite_upper_bound(monkeypatch):
    """Bounds follow the requirement, not the listed depth: integer bounds in the
    billions hung HiGHS on aarch64."""
    seen = {}

    def capture(kwargs):
        seen.update(kwargs)
        return supply.linprog(**kwargs).x
    monkeypatch.setattr(supply, "_run_mip", capture)
    required = {TRIT: 400_000_000.0, PYE: 200_000_000.0}
    direct = {TRIT: [SupplyTier(unit_cost=9.0, max_units=None)], PYE: [SupplyTier(unit_cost=9.0, max_units=None)]}
    supply.solve([TRIT, PYE], required, _sources(), direct)
    assert seen["bounds"]
    assert all(hi is not None and hi <= 400_000_000 for _lo, hi in seen["bounds"])


def test_useful_portions_is_the_largest_single_requirement():
    source = supply.SupplySource(portion_size=100, yield_per_portion={TRIT: 400, PYE: 50, 99: 1000}, tiers=[])
    assert supply._useful_portions(source, {TRIT: 1_001.0, PYE: 49.0}) == 3
    assert supply._useful_portions(source, {PYE: 51.0}) == 2
    assert supply._useful_portions(source, {12345: 10.0}) == 0


def test_plan_flags_survive_the_response_model():
    """response_model drops any field a schema does not declare (issue #111):
    these reached the frontend as undefined, so every row showed "qty unchecked"."""
    from eve_trader.api import schemas
    purchase = {"type_id": 1, "landed_cost_per_unit": 1.0, "total_cost": 1.0, "depth_checked": False}
    direct = schemas.DirectMineralPurchase(name="Trit", quantity=1, **purchase)
    assert direct.depth_checked is False
    ore = schemas.OrePurchase(item="Ore", family="F", is_ice=False, portions=1, units=1, volume_m3=1.0, **purchase)
    assert ore.depth_checked is False
    module = schemas.ReprocessPurchase(item="M", category="module", portions=1, units=1, volume_m3=1.0, **purchase)
    assert module.depth_checked is False
    totals = {"direct_purchases": [], "coverage": [], "direct_cost": 0, "total_cost": 0, "lp_cost": 0,
              "all_direct_cost": None, "savings_vs_all_direct": None, "total_volume_m3": 0,
              "shortfall": True, "approximate": True}
    for plan in (schemas.ShoppingListPlan(ore_purchases=[], ore_cost=0, **totals),
                 schemas.ModuleShoppingListPlan(reprocess_purchases=[], reprocess_cost=0, **totals)):
        assert plan.shortfall is True and plan.approximate is True
    coverage = {"type_id": 1, "name": "T", "required": 1.0, "from_direct": 0, "delivered": 0, "surplus": 0.0, "short": 1}
    assert schemas.MineralCoverage(from_ore=0, **coverage).short == 1
    assert schemas.ReprocessMineralCoverage(from_reprocessing=0, **coverage).short == 1


def test_unreadable_worker_output_is_treated_as_no_answer(real_worker, monkeypatch):
    class Done:
        returncode = 0
        stdout = b"not a pickle"
        stderr = b""
    monkeypatch.setattr(supply.subprocess, "run", lambda *a, **k: Done())
    assert _solve()[0].approximate


# ------------------------------------------------------------- by-products
GAS = 16634  # Atmospheric Gases: what Zeolites yield beside Pyerite


def _gas_sources(credit_value: float = 0.0):
    with_gas = {PYE: 300, GAS: 100}
    return [
        supply.SupplySource(portion_size=100, yield_per_portion=with_gas,
                            tiers=[SupplyTier(unit_cost=10.0, max_units=None)],
                            credit_per_portion=supply.byproduct_credit(with_gas, [PYE], {GAS: credit_value})),
        supply.SupplySource(portion_size=100, yield_per_portion={PYE: 300},
                            tiers=[SupplyTier(unit_cost=8.0, max_units=None)]),
    ]


def _bought(solution):
    out = {}
    for (i, _t), portions in solution.portions.items():
        out[i] = out.get(i, 0) + portions
    return out


def test_byproduct_credit_values_only_outputs_not_on_the_list():
    assert supply.byproduct_credit({PYE: 300, GAS: 100, TRIT: 7}, [PYE, TRIT], {GAS: 5.0, PYE: 99.0}) == 500.0
    assert supply.byproduct_credit({PYE: 300, GAS: 100}, [PYE], {GAS: None}) == 0.0


def test_without_credit_the_byproduct_source_loses():
    direct = {PYE: [SupplyTier(unit_cost=100.0, max_units=None)]}
    solution = supply.solve([PYE], {PYE: 300.0}, _gas_sources(0.0), direct)
    assert _bought(solution) == {1: 1}


def test_credit_flips_the_choice_to_the_source_with_valuable_byproducts():
    # 1,000 ISK per portion minus 100 gas x 5 ISK = 500 net, cheaper than 800.
    direct = {PYE: [SupplyTier(unit_cost=100.0, max_units=None)]}
    solution = supply.solve([PYE], {PYE: 300.0}, _gas_sources(5.0), direct)
    assert _bought(solution) == {0: 1}


def test_floor_never_buys_portions_beyond_the_need_when_byproducts_pay_for_them():
    # Each portion's gas is worth far more than the portion; Tritanium is also
    # on the list, so 1,000 portions would still be "useful". The floor keeps
    # the plan at the one portion Pyerite needs and buys Tritanium directly.
    yields = {PYE: 300, TRIT: 1, GAS: 100}
    source = supply.SupplySource(portion_size=100, yield_per_portion=yields,
                                 tiers=[SupplyTier(unit_cost=10.0, max_units=None)],
                                 credit_per_portion=supply.byproduct_credit(yields, [PYE, TRIT], {GAS: 50.0}))
    direct = {PYE: [SupplyTier(unit_cost=100.0, max_units=None)], TRIT: [SupplyTier(unit_cost=0.5, max_units=None)]}
    solution = supply.solve([PYE, TRIT], {PYE: 300.0, TRIT: 1000.0}, [source], direct)
    assert _bought(solution) == {0: 1}
    assert solution.direct == {(TRIT, 0): 999}


def test_byproduct_lines_sum_quantities_and_name_their_sources():
    sources = _gas_sources(5.0)
    solution = supply.SupplySolution(portions={(0, 0): 3})
    lines = supply.byproduct_lines(solution, sources, ["Zeolites", "Other"], [PYE], {GAS: 5.0}, {GAS: "Gas"})
    assert len(lines) == 1
    assert (lines[0].quantity, lines[0].unit_value, lines[0].total_value, lines[0].sources) == \
        (300, 5.0, 1500.0, ["Zeolites"])
    unpriced = supply.byproduct_lines(solution, sources, ["Zeolites", "Other"], [PYE], {}, {})
    assert unpriced[0].unit_value is None and unpriced[0].total_value is None


@pytest.mark.parametrize("credit", [False, True])
def test_landed_prices_add_up_to_the_plan_cost(credit):
    sources = _gas_sources(5.0)
    direct = {PYE: [SupplyTier(unit_cost=100.0, max_units=None)]}
    solution = supply.SupplySolution(portions={(0, 0): 2})
    prices = supply.landed_price_by_mineral(solution, sources, [PYE], direct, credit=credit)
    gross = 2 * 100 * 10.0
    expected = gross - 2 * 500.0 if credit else gross
    assert prices[PYE] * 600 == pytest.approx(expected)
