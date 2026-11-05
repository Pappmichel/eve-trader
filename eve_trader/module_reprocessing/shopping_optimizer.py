"""Combined ore/ice + module/drone buy-vs-reprocess optimization for Module
Reprocessing's Mineral Shopping List.

A port of Ore & Minerals' own Mineral Shopping List optimizer
(refining/optimizer.py, GitHub issue #93), generalized so the LP's columns
can be ANY reprocessable source - compressed ore/ice and T1/Meta modules/
drones alike - in one combined plan. The LP math itself is unchanged; only
the column type (ReprocessOption instead of OreOption, tagged with a
`category`) is broader. Deliberately a separate module rather than a
generalization of refining/optimizer.py in place: that one is already
shipped and brute-force-tested, and this feature must carry zero risk of
regressing it.

Answers "what's the cheapest way to end up holding *these* minerals": buy
them outright, buy-and-reprocess ore/ice or modules/drones, or (usually) a
mix - optimized across EVERY available source at once, not one "best
source" per mineral. A greedy ISK-per-desired-mineral ranking is only
optimal when one source's mineral ratio happens to line up with the
requested mix, and it systematically over-buys whenever the cheapest source
of mineral A drags in far more of mineral B than needed. Modules make this
worse, not better - a module's mineral mix is usually far less "pure" than
an ore's.

    minimize    sum_i  portions_i  x landed_cost_per_portion_i
              + sum_j  direct_buy_j x mineral_landed_cost_j
    subject to  sum_i  portions_i x yield_ij  +  direct_buy_j  >=  required_j
                for every required mineral j
                portions_i >= 0,  direct_buy_j >= 0
    (i ranges over ore/ice AND module/drone columns alike)

Solved with scipy.optimize.linprog (HiGHS).

Three modelling decisions worth knowing, all deliberate and all identical to
refining/optimizer.py's (see its own docstring for the full history):

1. **Portion quantities (ore AND module) and direct-mineral quantities are
   solved as real integers, via `linprog`'s `integrality` parameter - not
   relaxed to a continuous LP and rounded.** Reprocessing is genuinely
   discrete (you reprocess whole portions - 100 Veldspar or 1 module at a
   time - and each material's output is floored, see refining/engine.py's
   apply_reprocessing_yield), which makes this properly a mixed-integer
   program. Relax-then-round was tried once in refining/optimizer.py and
   confirmed wrong via randomized brute-force comparison: ~8% of small
   synthetic cases landed more than 0.1% above the true discrete optimum,
   worst case 64% over, because rounding can never *add* a source the
   continuous relaxation left at exactly zero even when one whole portion of
   it is the true cheapest way to cover a small requirement. Direct-mineral
   quantities are integer too - leaving them continuous re-opens a smaller
   version of the same gap (the solver would price "0.5 units direct" at
   half a unit's ISK, while the real purchase costs a whole unit).
   `test_matches_brute_force_optimum_on_random_small_cases` in
   test_module_reprocessing_shopping_optimizer.py asserts this solver lands
   on the true discrete optimum over random mixed ore+module cases.

   The continuous relaxation is still solved once, purely to report
   `ModuleShoppingListPlan.lp_cost` as a "theoretical floor" next to the
   real (whole-unit) total - it plays no part in building the actual plan.

2. **The structure's reprocessing tax is modelled as reduced output, not an
   ISK fee.** EVE takes the tax out of the reprocessed materials themselves,
   so for a *quantity* question ("do I end up with enough Tritanium?") the
   honest model is a smaller yield_ij - which is what the caller passes in
   (see actions.do_optimize_module_shopping_list: ore columns net of
   RefiningConfig.refining_tax_rate, module columns net of
   ModuleReprocessingConfig.refining_tax_rate).

3. **Surplus minerals get no credit.** A source bought for its Mexallon also
   yields Tritanium the build list may not need; that leftover is reported
   (`MineralCoverage.surplus`) but is not valued in the objective. Crediting
   it would mean assuming it gets sold, which turns a shopping list into a
   trading decision - the Shortlist pages are the tools for that question.
"""
from __future__ import annotations

from .. import mineral_supply_lp as supply
from .models import (
    DirectMineralPurchase,
    MineralCoverage,
    MineralOption,
    MineralRequirement,
    ModuleShoppingListPlan,
    ReprocessOption,
    ReprocessPurchase,
)

# Solver, time limit and shortfall handling: mineral_supply_lp (shared with
# refining/optimizer.py since 2026-11-04, when both gained market depth).


class OptimizationError(Exception):
    """Raised for an empty requirement list or a solver failure with no usable
    incumbent. A mineral nothing can supply at an acceptable price is reported
    as a shortfall (MineralCoverage.short), not raised.
    module_reprocessing/actions.py converts this to the app-wide ActionError -
    see CLAUDE.md's "ActionError is the one user-facing error type"."""


def optimize_shopping_list(requirements: list[MineralRequirement], options: list[ReprocessOption],
                            mineral_options: dict[int, MineralOption]) -> ModuleShoppingListPlan:
    """Pure - every price, yield, portion size and listed depth is pre-fetched
    by the caller, nothing here touches storage/ESI. `options` is ONE combined
    list of ore/ice and module/drone columns; the solver treats them
    identically. An option or mineral without `tiers` is one uncapped tier at
    its landed price. What no source covers at an acceptable price comes back
    as `MineralCoverage.short` (see mineral_supply_lp)."""
    wanted = [r for r in requirements if r.required_qty > 0]
    if not wanted:
        raise OptimizationError("No mineral requirements to solve for - add at least one mineral and quantity.")

    mineral_ids = [r.type_id for r in wanted]
    required = {r.type_id: float(r.required_qty) for r in wanted}
    names = {r.type_id: r.name for r in wanted}

    # Only sources that actually yield something we asked for are columns in
    # the LP - one whose entire yield is minerals nobody wants is pure cost.
    options_used = [o for o in options
                    if o.portion_size > 0 and any(o.yield_per_portion.get(m, 0) > 0 for m in mineral_ids)]
    sources = [supply.SupplySource(portion_size=o.portion_size, yield_per_portion=o.yield_per_portion,
                                   tiers=supply.option_tiers(o.landed_cost_per_unit, o.tiers, o.hub_region_id))
               for o in options_used]
    direct_tiers = {}
    for m in mineral_ids:
        option = mineral_options.get(m)
        direct_tiers[m] = (supply.mineral_tiers(option.landed_cost_per_unit, option.tiers, option.source,
                                                option.hub_region_id) if option else [])

    try:
        solution = supply.solve(mineral_ids, required, sources, direct_tiers)
    except supply.SolveError as e:
        raise OptimizationError(str(e)) from e

    reprocess_purchases = []
    for i, source in enumerate(options_used):
        total = supply.source_total(solution, i, sources[i])
        if total is None:
            continue
        reprocess_purchases.append(ReprocessPurchase(
            type_id=source.type_id, item=source.item, category=source.category, family=source.family,
            is_ice=source.is_ice, portions=total.portions, units=total.units,
            volume_m3=total.units * source.volume_m3,
            landed_cost_per_unit=total.total_cost / total.units, total_cost=total.total_cost,
            hub_region_id=source.hub_region_id, depth_checked=total.depth_checked,
        ))
    reprocess_purchases.sort(key=lambda p: -p.total_cost)

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
        reprocessed_qty = delivered.get(mineral_id, 0)
        direct_qty = from_direct.get(mineral_id, 0)
        total = reprocessed_qty + direct_qty
        coverage.append(MineralCoverage(
            type_id=mineral_id, name=names[mineral_id], required=required[mineral_id],
            from_reprocessing=reprocessed_qty, from_direct=direct_qty, delivered=total,
            surplus=max(0.0, total - required[mineral_id]), short=solution.short.get(mineral_id, 0),
        ))
    coverage.sort(key=lambda c: c.name)

    reprocess_cost = sum(p.total_cost for p in reprocess_purchases)
    direct_cost = sum(p.total_cost for p in direct_purchases)
    all_direct_cost = supply.all_direct_cost(mineral_ids, required, direct_tiers)
    total_cost = reprocess_cost + direct_cost

    return ModuleShoppingListPlan(
        reprocess_purchases=reprocess_purchases, direct_purchases=direct_purchases, coverage=coverage,
        reprocess_cost=reprocess_cost, direct_cost=direct_cost, total_cost=total_cost, lp_cost=solution.lp_cost,
        all_direct_cost=all_direct_cost,
        savings_vs_all_direct=(all_direct_cost - total_cost) if all_direct_cost is not None else None,
        total_volume_m3=sum(p.volume_m3 for p in reprocess_purchases),
        shortfall=bool(solution.short), approximate=solution.approximate,
    )
