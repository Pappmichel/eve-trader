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

1. **Portion-size rounding is handled outside the LP, by rounding UP.**
   Reprocessing is genuinely discrete (you refine whole portions - 100
   Veldspar at a time - and each material's output is floored, see
   engine.py's apply_reprocessing_yield), which would make this an integer
   program. It's relaxed to a continuous LP instead - a fractional portion
   is a reasonable idealization while *searching* for the cheapest mix -
   and each ore's solved quantity is then rounded UP to the next whole
   portion in the plan the user actually buys from. Up, never down:
   rounding down would under-deliver against the stated requirement, which
   is the one thing a shopping list must not do. Because per-material
   output is floored (not just scaled), rounding up isn't *by itself*
   proof the requirement still clears, so the rounded plan is re-verified
   against the real discrete yields and topped up until it does (see
   `_repair_shortfalls`) - `ShoppingListPlan.coverage` carries that proof
   per mineral. Rounding up can also *over*-buy (a requirement worth a
   fiftieth of a portion still costs a whole one), so a cheap
   single-portion descent on the real cost function then drops any portion
   that direct-buying replaces more cheaply (`_trim_rounded_plan`). The
   LP's own continuous optimum is kept as
   `ShoppingListPlan.lp_cost` so the (small) cost of rounding is visible
   rather than hidden.

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

import math

import numpy as np
from scipy.optimize import linprog

from .models import (
    DirectMineralPurchase,
    MineralCoverage,
    MineralOption,
    MineralRequirement,
    OreOption,
    OrePurchase,
    ShoppingListPlan,
)

# Solver noise guard: HiGHS routinely returns 3.0000000000000004 or 1e-13 for
# what is really 3 and 0. Rounding those up naively would buy a whole extra
# portion of an ore the LP didn't actually want.
_EPS = 1e-9

# `_repair_shortfalls` adds whole portions, which strictly increases delivery,
# so it terminates - this cap only bounds a pathological case (an ore whose
# floored per-portion yield of some mineral is 0 can never close that gap, and
# is filtered out before we get here).
_MAX_REPAIR_ROUNDS = 8

# `_trim_rounded_plan` only ever accepts a strictly cheaper plan, so it can't
# cycle; the cap bounds how long a descent runs on a pathologically large plan.
_MAX_TRIM_ROUNDS = 50


class OptimizationError(Exception):
    """Raised when no plan can satisfy the requirements at all (a mineral no
    ore yields and no market lists, or a solver failure). refining/actions.py
    converts this to the app-wide ActionError - see CLAUDE.md's "ActionError
    is the one user-facing error type"."""


def optimize_shopping_list(requirements: list[MineralRequirement], ore_options: list[OreOption],
                            mineral_options: dict[int, MineralOption]) -> ShoppingListPlan:
    """Pure - every price, yield and portion size is pre-fetched by the caller,
    nothing here touches storage/ESI. `mineral_options` is keyed by mineral
    type_id and must cover every requirement (a missing/None landed cost just
    means "can't be bought directly", not an error, as long as some ore
    yields it)."""
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

    direct_price = {m: mineral_options[m].landed_cost_per_unit
                    for m in mineral_ids
                    if m in mineral_options and mineral_options[m].landed_cost_per_unit is not None}

    unreachable = [names[m] for m in mineral_ids
                   if m not in direct_price and not any(o.yield_per_portion.get(m, 0) > 0 for o in ores)]
    if unreachable:
        raise OptimizationError(
            "No way to source " + ", ".join(sorted(unreachable))
            + " - no compressed ore/ice on the shortlist refines into it and it isn't listed in Jita right now."
        )

    direct_ids = [m for m in mineral_ids if m in direct_price]

    # ---------------------------------------------------------------- the LP
    n_ore, n_direct = len(ores), len(direct_ids)
    cost = np.array([o.landed_cost_per_portion for o in ores] + [direct_price[m] for m in direct_ids], dtype=float)

    # linprog only speaks <=, so every ">= required" row is negated.
    a_ub = np.zeros((len(mineral_ids), n_ore + n_direct), dtype=float)
    for row, mineral_id in enumerate(mineral_ids):
        for col, ore in enumerate(ores):
            a_ub[row, col] = -float(ore.yield_per_portion.get(mineral_id, 0))
        if mineral_id in direct_price:
            a_ub[row, n_ore + direct_ids.index(mineral_id)] = -1.0
    b_ub = np.array([-required[m] for m in mineral_ids], dtype=float)

    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs")
    if not result.success:
        raise OptimizationError(f"Could not solve the shopping list ({result.message.strip()}).")
    lp_cost = float(result.fun)

    # ------------------------------------------- round up to whole portions
    portions = {i: max(0, math.ceil(result.x[i] - _EPS)) for i in range(n_ore)}
    delivered = _delivered_from_ore(ores, portions, mineral_ids)
    _repair_shortfalls(ores, portions, delivered, mineral_ids, required, direct_price, names)
    _trim_rounded_plan(ores, portions, mineral_ids, required, direct_price)
    delivered = _delivered_from_ore(ores, portions, mineral_ids)

    ore_purchases = []
    for i, ore in enumerate(ores):
        if portions[i] <= 0:
            continue
        units = portions[i] * ore.portion_size
        ore_purchases.append(OrePurchase(
            type_id=ore.type_id, item=ore.item, family=ore.family, is_ice=ore.is_ice,
            portions=portions[i], units=units, volume_m3=units * ore.volume_m3,
            landed_cost_per_unit=ore.landed_cost_per_unit,
            total_cost=units * ore.landed_cost_per_unit,
        ))
    ore_purchases.sort(key=lambda p: -p.total_cost)

    # Whatever the (rounded-up) ore still doesn't cover is bought outright.
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
            surplus=total - required[mineral_id],
        ))
    coverage.sort(key=lambda c: c.name)

    short = [c.name for c in coverage if c.delivered + _EPS < c.required]
    if short:
        # Belt-and-braces: _repair_shortfalls plus the direct top-up above
        # should make this unreachable. Failing loudly beats handing back a
        # shopping list that quietly doesn't cover the build.
        raise OptimizationError("Could not build a plan that covers " + ", ".join(sorted(short)) + ".")

    ore_cost = sum(p.total_cost for p in ore_purchases)
    direct_cost = sum(p.total_cost for p in direct_purchases)
    all_direct_cost = (sum(required[m] * direct_price[m] for m in mineral_ids)
                       if len(direct_price) == len(mineral_ids) else None)
    total_cost = ore_cost + direct_cost

    return ShoppingListPlan(
        ore_purchases=ore_purchases, direct_purchases=direct_purchases, coverage=coverage,
        ore_cost=ore_cost, direct_cost=direct_cost, total_cost=total_cost, lp_cost=lp_cost,
        all_direct_cost=all_direct_cost,
        savings_vs_all_direct=(all_direct_cost - total_cost) if all_direct_cost is not None else None,
        total_volume_m3=sum(p.volume_m3 for p in ore_purchases),
    )


def _delivered_from_ore(ores: list[OreOption], portions: dict[int, int], mineral_ids: list[int]) -> dict[int, int]:
    delivered = {m: 0 for m in mineral_ids}
    for i, ore in enumerate(ores):
        if portions[i] <= 0:
            continue
        for mineral_id in mineral_ids:
            delivered[mineral_id] += portions[i] * ore.yield_per_portion.get(mineral_id, 0)
    return delivered


def _plan_cost(ores: list[OreOption], portions: dict[int, int], mineral_ids: list[int],
                required: dict[int, float], direct_price: dict[int, float]) -> float:
    """Real ISK cost of a whole-portion plan: the ore itself plus whatever
    direct buying is still needed to close the gap it leaves."""
    delivered = _delivered_from_ore(ores, portions, mineral_ids)
    cost = sum(portions[i] * o.landed_cost_per_portion for i, o in enumerate(ores))
    for mineral_id in mineral_ids:
        gap = required[mineral_id] - delivered.get(mineral_id, 0)
        if gap > _EPS and mineral_id in direct_price:
            cost += math.ceil(gap - _EPS) * direct_price[mineral_id]
    return cost


def _trim_rounded_plan(ores: list[OreOption], portions: dict[int, int], mineral_ids: list[int],
                        required: dict[int, float], direct_price: dict[int, float]) -> None:
    """Drops whole portions the rounding-up step over-bought, whenever buying
    the difference outright is genuinely cheaper. Mutates `portions` in place.

    This is what keeps decision 1 (relax, then round up) honest at small
    quantities: the LP optimizes a *continuous* mix, so for a requirement far
    below one portion it can happily "buy" 0.02 of a portion - rounded up to a
    whole one, that's a real 275k-ISK ore purchase to cover 7k ISK worth of
    mineral (seen live during #93's verification). The LP can't see this,
    because the whole-portion granularity it would have to reason about is
    exactly what was relaxed away. A single-portion-at-a-time descent on the
    *real* cost function (`_plan_cost`, which prices the leftover gap at the
    direct-buy price) fixes it without reintegerizing the whole program: it
    never drops a portion a mineral with no direct-buy price depends on, and
    it only ever accepts a strictly cheaper plan, so the result can't be worse
    than the naive round-up - just often better."""
    for _ in range(_MAX_TRIM_ROUNDS):
        best_cost = _plan_cost(ores, portions, mineral_ids, required, direct_price)
        improved = False
        for i in sorted((i for i in portions if portions[i] > 0),
                         key=lambda i: -ores[i].landed_cost_per_portion):
            trial = dict(portions)
            trial[i] -= 1
            trial_delivered = _delivered_from_ore(ores, trial, mineral_ids)
            # A mineral nothing lists in Jita can only come from ore - never
            # trim below what covers it.
            if any(m not in direct_price and trial_delivered.get(m, 0) + _EPS < required[m]
                   for m in mineral_ids):
                continue
            trial_cost = _plan_cost(ores, trial, mineral_ids, required, direct_price)
            if trial_cost + _EPS < best_cost:
                portions[i] = trial[i]
                best_cost, improved = trial_cost, True
        if not improved:
            return


def _repair_shortfalls(ores: list[OreOption], portions: dict[int, int], delivered: dict[int, int],
                        mineral_ids: list[int], required: dict[int, float],
                        direct_price: dict[int, float], names: dict[int, str]) -> None:
    """Closes any gap left for a mineral that CAN'T be bought directly, by
    adding whole portions of whichever ore supplies it most cheaply. Only
    reachable because per-material yields are floored: the LP solved against
    those same floored per-portion numbers, so rounding portions up normally
    already over-delivers - this exists so "normally" isn't load-bearing.
    Mutates `portions`/`delivered` in place."""
    for _ in range(_MAX_REPAIR_ROUNDS):
        gaps = [m for m in mineral_ids
                if m not in direct_price and delivered.get(m, 0) + _EPS < required[m]]
        if not gaps:
            return
        for mineral_id in gaps:
            candidates = [(i, o) for i, o in enumerate(ores) if o.yield_per_portion.get(mineral_id, 0) > 0]
            if not candidates:
                raise OptimizationError(f"No compressed ore/ice yields {names[mineral_id]}.")
            i, ore = min(candidates,
                         key=lambda pair: pair[1].landed_cost_per_portion / pair[1].yield_per_portion[mineral_id])
            gap = required[mineral_id] - delivered.get(mineral_id, 0)
            extra = max(1, math.ceil(gap / ore.yield_per_portion[mineral_id] - _EPS))
            portions[i] += extra
            delivered.update(_delivered_from_ore(ores, portions, mineral_ids))
