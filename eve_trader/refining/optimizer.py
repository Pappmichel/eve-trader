"""Multi-ore buy-vs-refine optimization for the Mineral Shopping List -
GitHub issue #93 (phase 4/5 of Ore & Minerals).

Answers "what's the cheapest way to end up holding *these* minerals": buy
them outright, buy-and-refine compressed ore/ice, or (usually) a mix -
optimized across EVERY available ore/ice type at once, not one "best ore"
per mineral. Confirmed with the user during planning that this had to be a
real linear program rather than a greedy ISK-per-desired-mineral ranking: a
greedy pick is only optimal when one ore's mineral ratio happens to line up
with the requested mix, and it systematically over-buys whenever the
cheapest source of mineral A drags in far more of mineral B than needed
(exactly the situation a mixed Tritanium/Pyerite/Mexallon build list
creates).

    minimize    sum_i  ore_portions_i x landed_cost_per_portion_i
              + sum_j  direct_buy_j   x mineral_landed_cost_j
    subject to  sum_i  ore_portions_i x yield_ij  +  direct_buy_j  >=  required_j
                for every required mineral j
                ore_portions_i >= 0,  direct_buy_j >= 0

Solved with scipy.optimize.linprog (HiGHS) - the one thing this whole phase
added a new project dependency for.

Three modelling decisions worth knowing, all deliberate:

1. **Portion-size (and direct-mineral) quantities are solved as real
   integers, via `linprog`'s `integrality` parameter - not relaxed to a
   continuous LP and rounded.** Reprocessing is genuinely discrete (you
   refine whole portions - 100 Veldspar at a time - and each material's
   output is floored, see engine.py's apply_reprocessing_yield), which
   makes this properly a mixed-integer program. An earlier version of this
   file relaxed it to a continuous LP and rounded the result UP to the next
   whole portion, then ran a post-hoc heuristic (`_trim_rounded_plan`) that
   only ever *dropped* portions from the ore set the continuous relaxation
   had already given non-zero weight - it could never *add* an ore the
   relaxation left at exactly zero, even when one whole portion of that ore
   was the true cheapest way to cover a small requirement (a different ore
   only looked marginally cheaper *fractionally*, a margin whole-portion
   buying can't use). Confirmed via randomized brute-force comparison before
   the fix: ~8% of small synthetic cases landed more than 0.1% above the
   true discrete optimum, worst case observed 64% over (a plan costing 5757
   ISK against a true optimum of 3514 ISK) - see
   `test_matches_brute_force_optimum_on_random_small_cases` in
   test_refining_optimizer.py, which now asserts the fixed solver lands on
   the true optimum instead. Every plan produced (before and after this
   fix) was still *feasible* - no requirement was ever left under-covered -
   this was purely a cost-optimality gap.

   Direct-mineral purchase quantities are ALSO marked integer, not left
   continuous - real minerals are bought in whole units too, and leaving
   that variable continuous re-opens a smaller version of the same gap: the
   solver would price a fractional "0.5 units direct" at half a unit's ISK,
   while the actual purchase (rounded up to a whole unit afterward) costs a
   full unit - enough of a mismatch, on small requirements, to make the
   solver choose ore portions that are optimal for its own (wrong)
   continuous accounting but not for the real whole-unit cost. Marking both
   variable groups integer makes the solver's objective exactly the real
   cost, closing that gap too (confirmed by the same brute-force test - 0
   mismatches across 500 random cases with both integer, versus a real
   ~9% average case among many that had direct purchases left continuous).

   The LP's own continuous relaxation (both variable groups left
   continuous) is still solved once, purely to report
   `ShoppingListPlan.lp_cost` as a "theoretical floor" next to the real
   (whole-unit) total - it plays no part in building the actual plan
   anymore.

2. **The structure's refining tax is modelled as reduced output, not an ISK
   fee.** EVE takes a structure's reprocessing tax out of the refined
   materials themselves, so for a *quantity* question ("do I end up with
   enough Tritanium?") the honest model is a smaller yield_ij - which is
   what the caller passes in (see actions.do_optimize_mineral_shopping_list).
   This differs from the Ore Shortlist's own treatment (#91 charges it as
   ISK against the sale proceeds); both are the same economics, but there
   the output is sold immediately and here it has to physically cover a
   build list.

3. **Surplus minerals get no credit.** An ore bought for its Mexallon also
   yields Tritanium the build list may not need; that leftover is reported
   (`MineralCoverage.surplus`) but is not valued in the objective. Crediting
   it would mean assuming it gets sold, which turns a shopping list into a
   trading decision - #91's Ore Shortlist is the tool for that question.
"""
from __future__ import annotations

from .. import mineral_supply_lp as supply
from .models import (
    DirectMineralPurchase,
    MineralCoverage,
    MineralOption,
    MineralRequirement,
    OreOption,
    OrePurchase,
    ShoppingListPlan,
)

# Solver noise guard: even with `integrality` set, HiGHS routinely returns
# 3.0000000000000004 or 1e-13 for what is really an exact 3 or 0 - this only
# needs to survive a round()/comparison now, never a round-UP, since there's
# no fractional remainder left to round away.

# The MIP time limit lives in mineral_supply_lp.MIP_TIME_LIMIT_SECONDS.


class OptimizationError(Exception):
    """Raised for an empty requirement list or a solver failure with no usable
    incumbent. A mineral nothing can supply at an acceptable price is not an
    error: it is reported as a shortfall (MineralCoverage.short). refining/actions.py converts this to the
    app-wide ActionError - see CLAUDE.md's "ActionError is the one
    user-facing error type"."""


def optimize_shopping_list(requirements: list[MineralRequirement], ore_options: list[OreOption],
                            mineral_options: dict[int, MineralOption]) -> ShoppingListPlan:
    """Pure - every price, yield, portion size and listed depth is pre-fetched
    by the caller, nothing here touches storage/ESI. `mineral_options` is
    keyed by mineral type_id; a missing one just means "can't be bought
    directly". An ore or mineral without `tiers` is one uncapped tier at its
    landed price (depth not checked). What no source covers at an acceptable
    price comes back as `MineralCoverage.short` (see mineral_supply_lp)."""
    wanted = [r for r in requirements if r.required_qty > 0]
    if not wanted:
        raise OptimizationError("No mineral requirements to solve for - add at least one mineral and quantity.")

    mineral_ids = [r.type_id for r in wanted]
    required = {r.type_id: float(r.required_qty) for r in wanted}
    names = {r.type_id: r.name for r in wanted}

    # Only ores that actually yield something we asked for are columns in the
    # LP - an ore whose entire yield is minerals nobody wants is pure cost.
    ores = [o for o in ore_options
            if o.portion_size > 0 and any(o.yield_per_portion.get(m, 0) > 0 for m in mineral_ids)]
    sources = [supply.SupplySource(portion_size=o.portion_size, yield_per_portion=o.yield_per_portion,
                                   tiers=supply.option_tiers(o.landed_cost_per_unit, o.tiers, o.hub_region_id))
               for o in ores]
    direct_tiers = {}
    for m in mineral_ids:
        option = mineral_options.get(m)
        direct_tiers[m] = (supply.mineral_tiers(option.landed_cost_per_unit, option.tiers, option.source,
                                                option.hub_region_id) if option else [])

    try:
        solution = supply.solve(mineral_ids, required, sources, direct_tiers)
    except supply.SolveError as e:
        raise OptimizationError(str(e)) from e

    ore_purchases = []
    for i, ore in enumerate(ores):
        total = supply.source_total(solution, i, sources[i])
        if total is None:
            continue
        ore_purchases.append(OrePurchase(
            type_id=ore.type_id, item=ore.item, family=ore.family, is_ice=ore.is_ice,
            portions=total.portions, units=total.units, volume_m3=total.units * ore.volume_m3,
            landed_cost_per_unit=total.total_cost / total.units, total_cost=total.total_cost,
            hub_region_id=ore.hub_region_id, depth_checked=total.depth_checked,
        ))
    ore_purchases.sort(key=lambda p: -p.total_cost)

    delivered = supply.delivered_from_sources(solution, sources, mineral_ids)
    direct_purchases = []
    from_direct: dict[int, int] = {}
    for mineral_id in mineral_ids:
        for line in supply.direct_totals(solution, mineral_id, direct_tiers[mineral_id]):
            from_direct[mineral_id] = from_direct.get(mineral_id, 0) + line.quantity
            direct_purchases.append(DirectMineralPurchase(
                type_id=mineral_id, name=names[mineral_id], quantity=line.quantity,
                landed_cost_per_unit=line.total_cost / line.quantity, total_cost=line.total_cost,
                source=line.source, hub_region_id=line.hub_region_id, depth_checked=line.depth_checked,
            ))
    direct_purchases.sort(key=lambda p: -p.total_cost)

    coverage = []
    for mineral_id in mineral_ids:
        ore_qty = delivered.get(mineral_id, 0)
        direct_qty = from_direct.get(mineral_id, 0)
        total = ore_qty + direct_qty
        coverage.append(MineralCoverage(
            type_id=mineral_id, name=names[mineral_id], required=required[mineral_id],
            from_ore=ore_qty, from_direct=direct_qty, delivered=total,
            surplus=max(0.0, total - required[mineral_id]), short=solution.short.get(mineral_id, 0),
        ))
    coverage.sort(key=lambda c: c.name)

    ore_cost = sum(p.total_cost for p in ore_purchases)
    direct_cost = sum(p.total_cost for p in direct_purchases)
    all_direct_cost = supply.all_direct_cost(mineral_ids, required, direct_tiers)
    total_cost = ore_cost + direct_cost

    return ShoppingListPlan(
        ore_purchases=ore_purchases, direct_purchases=direct_purchases, coverage=coverage,
        ore_cost=ore_cost, direct_cost=direct_cost, total_cost=total_cost, lp_cost=solution.lp_cost,
        all_direct_cost=all_direct_cost,
        savings_vs_all_direct=(all_direct_cost - total_cost) if all_direct_cost is not None else None,
        total_volume_m3=sum(p.volume_m3 for p in ore_purchases),
        shortfall=bool(solution.short),
    )
