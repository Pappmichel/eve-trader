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

import math

import numpy as np
from scipy.optimize import linprog

from .models import (
    DirectMineralPurchase,
    MineralCoverage,
    MineralOption,
    MineralRequirement,
    ModuleShoppingListPlan,
    ReprocessOption,
    ReprocessPurchase,
)

# Solver noise guard - see refining/optimizer.py's own _EPS comment.
_EPS = 1e-9

# Defensive backstop only - see refining/optimizer.py's own
# _MAX_REPAIR_ROUNDS comment (the MIP's hard constraints already guarantee
# coverage; this exists for a time-limit incumbent or float slop).
_MAX_REPAIR_ROUNDS = 8

# Same safety net as refining/optimizer.py's _MIP_TIME_LIMIT_SECONDS: a
# pathological instance degrades to HiGHS' best feasible incumbent instead of
# hanging the request. Unlike there, this cap is realistically reachable
# here: the column count is the full ore universe PLUS the module shortlist
# (hundreds of near-tied module columns), and a 60-ore + 400-module instance
# measured ~4-5s in a slow sandbox. The incumbent HiGHS returns on a timeout
# was still within ~0.01% of the continuous LP floor in those measurements
# (HiGHS' own default optimality gap is 0.01% anyway), so the cap costs
# essentially nothing in plan quality - see test_realistic_scale_solves_quickly,
# which asserts exactly that.
_MIP_TIME_LIMIT_SECONDS = 5.0


class OptimizationError(Exception):
    """Raised when no plan can satisfy the requirements at all (a mineral no
    source yields and no market lists, or a solver failure).
    module_reprocessing/actions.py converts this to the app-wide ActionError -
    see CLAUDE.md's "ActionError is the one user-facing error type"."""


def optimize_shopping_list(requirements: list[MineralRequirement], options: list[ReprocessOption],
                            mineral_options: dict[int, MineralOption]) -> ModuleShoppingListPlan:
    """Pure - every price, yield and portion size is pre-fetched by the caller,
    nothing here touches storage/ESI. `options` is ONE combined list of ore/
    ice and module/drone columns; the solver treats them identically.
    `mineral_options` is keyed by mineral type_id and must cover every
    requirement (a missing/None landed cost just means "can't be bought
    directly", not an error, as long as some source yields it)."""
    wanted = [r for r in requirements if r.required_qty > 0]
    if not wanted:
        raise OptimizationError("No mineral requirements to solve for - add at least one mineral and quantity.")

    mineral_ids = [r.type_id for r in wanted]
    required = {r.type_id: float(r.required_qty) for r in wanted}
    names = {r.type_id: r.name for r in wanted}

    # Only sources that actually yield something we asked for are columns in
    # the LP - one whose entire yield is minerals nobody wants is pure cost.
    sources = [o for o in options
               if o.portion_size > 0 and any(o.yield_per_portion.get(m, 0) > 0 for m in mineral_ids)]

    direct_price = {m: mineral_options[m].landed_cost_per_unit
                    for m in mineral_ids
                    if m in mineral_options and mineral_options[m].landed_cost_per_unit is not None}

    unreachable = [names[m] for m in mineral_ids
                   if m not in direct_price and not any(o.yield_per_portion.get(m, 0) > 0 for o in sources)]
    if unreachable:
        raise OptimizationError(
            "No way to source " + ", ".join(sorted(unreachable))
            + " - no compressed ore/ice or shortlisted module reprocesses into it and it isn't listed"
            " in Jita right now."
        )

    direct_ids = [m for m in mineral_ids if m in direct_price]

    # ---------------------------------------------------------------- the LP
    n_src, n_direct = len(sources), len(direct_ids)
    cost = np.array([o.landed_cost_per_portion for o in sources] + [direct_price[m] for m in direct_ids],
                    dtype=float)

    # linprog only speaks <=, so every ">= required" row is negated.
    a_ub = np.zeros((len(mineral_ids), n_src + n_direct), dtype=float)
    for row, mineral_id in enumerate(mineral_ids):
        for col, source in enumerate(sources):
            a_ub[row, col] = -float(source.yield_per_portion.get(mineral_id, 0))
        if mineral_id in direct_price:
            a_ub[row, n_src + direct_ids.index(mineral_id)] = -1.0
    b_ub = np.array([-required[m] for m in mineral_ids], dtype=float)

    # Solved once, continuous, purely to report `lp_cost` - see decision 1.
    relaxed = linprog(cost, A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs")
    if not relaxed.success:
        raise OptimizationError(f"Could not solve the shopping list ({relaxed.message.strip()}).")
    lp_cost = float(relaxed.fun)

    # ------------------------------------------------------- the real (integer) program
    integrality = np.array([1] * n_src + [1] * n_direct)
    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs",
                      integrality=integrality, options={"time_limit": _MIP_TIME_LIMIT_SECONDS})
    if result.x is None:
        raise OptimizationError(f"Could not solve the shopping list as a whole-unit plan ({result.message.strip()}).")

    # round(), not ceil() - only solver noise to round off (see
    # refining/optimizer.py's own comment on this same line).
    portions = {i: max(0, round(result.x[i])) for i in range(n_src)}
    delivered = _delivered_from_sources(sources, portions, mineral_ids)
    _repair_shortfalls(sources, portions, delivered, mineral_ids, required, direct_price, names)
    delivered = _delivered_from_sources(sources, portions, mineral_ids)

    reprocess_purchases = []
    for i, source in enumerate(sources):
        if portions[i] <= 0:
            continue
        units = portions[i] * source.portion_size
        reprocess_purchases.append(ReprocessPurchase(
            type_id=source.type_id, item=source.item, category=source.category, family=source.family,
            is_ice=source.is_ice, portions=portions[i], units=units, volume_m3=units * source.volume_m3,
            landed_cost_per_unit=source.landed_cost_per_unit,
            total_cost=units * source.landed_cost_per_unit,
            hub_region_id=source.hub_region_id,
        ))
    reprocess_purchases.sort(key=lambda p: -p.total_cost)

    # Whatever the solved reprocessing quantities still don't cover is bought outright.
    direct_purchases = []
    from_direct: dict[int, int] = {}
    for mineral_id in mineral_ids:
        shortfall = required[mineral_id] - delivered.get(mineral_id, 0)
        if shortfall <= _EPS or mineral_id not in direct_price:
            continue
        quantity = math.ceil(shortfall - _EPS)
        from_direct[mineral_id] = quantity
        direct_purchases.append(DirectMineralPurchase(
            type_id=mineral_id, name=names[mineral_id], quantity=quantity,
            landed_cost_per_unit=direct_price[mineral_id],
            total_cost=quantity * direct_price[mineral_id],
            source=mineral_options[mineral_id].source if mineral_id in mineral_options else None,
            hub_region_id=mineral_options[mineral_id].hub_region_id if mineral_id in mineral_options else None,
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
            surplus=total - required[mineral_id],
        ))
    coverage.sort(key=lambda c: c.name)

    short = [c.name for c in coverage if c.delivered + _EPS < c.required]
    if short:
        # Belt-and-braces - failing loudly beats handing back a shopping list
        # that quietly doesn't cover the build.
        raise OptimizationError("Could not build a plan that covers " + ", ".join(sorted(short)) + ".")

    reprocess_cost = sum(p.total_cost for p in reprocess_purchases)
    direct_cost = sum(p.total_cost for p in direct_purchases)
    all_direct_cost = (sum(required[m] * direct_price[m] for m in mineral_ids)
                       if len(direct_price) == len(mineral_ids) else None)
    total_cost = reprocess_cost + direct_cost

    return ModuleShoppingListPlan(
        reprocess_purchases=reprocess_purchases, direct_purchases=direct_purchases, coverage=coverage,
        reprocess_cost=reprocess_cost, direct_cost=direct_cost, total_cost=total_cost, lp_cost=lp_cost,
        all_direct_cost=all_direct_cost,
        savings_vs_all_direct=(all_direct_cost - total_cost) if all_direct_cost is not None else None,
        total_volume_m3=sum(p.volume_m3 for p in reprocess_purchases),
    )


def _delivered_from_sources(sources: list[ReprocessOption], portions: dict[int, int],
                             mineral_ids: list[int]) -> dict[int, int]:
    delivered = {m: 0 for m in mineral_ids}
    for i, source in enumerate(sources):
        if portions[i] <= 0:
            continue
        for mineral_id in mineral_ids:
            delivered[mineral_id] += portions[i] * source.yield_per_portion.get(mineral_id, 0)
    return delivered


def _repair_shortfalls(sources: list[ReprocessOption], portions: dict[int, int], delivered: dict[int, int],
                        mineral_ids: list[int], required: dict[int, float],
                        direct_price: dict[int, float], names: dict[int, str]) -> None:
    """Closes any gap left for a mineral that CAN'T be bought directly, by
    adding whole portions of whichever source (ore or module) supplies it
    most cheaply. Same defensive backstop as refining/optimizer.py's own
    _repair_shortfalls. Mutates `portions`/`delivered` in place."""
    for _ in range(_MAX_REPAIR_ROUNDS):
        gaps = [m for m in mineral_ids
                if m not in direct_price and delivered.get(m, 0) + _EPS < required[m]]
        if not gaps:
            return
        for mineral_id in gaps:
            candidates = [(i, o) for i, o in enumerate(sources) if o.yield_per_portion.get(mineral_id, 0) > 0]
            if not candidates:
                raise OptimizationError(f"Nothing reprocessable yields {names[mineral_id]}.")
            i, source = min(candidates,
                            key=lambda pair: pair[1].landed_cost_per_portion / pair[1].yield_per_portion[mineral_id])
            gap = required[mineral_id] - delivered.get(mineral_id, 0)
            extra = max(1, math.ceil(gap / source.yield_per_portion[mineral_id] - _EPS))
            portions[i] += extra
            delivered.update(_delivered_from_sources(sources, portions, mineral_ids))
