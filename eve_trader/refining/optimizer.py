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

# Solver noise guard: even with `integrality` set, HiGHS routinely returns
# 3.0000000000000004 or 1e-13 for what is really an exact 3 or 0 - this only
# needs to survive a round()/comparison now, never a round-UP, since there's
# no fractional remainder left to round away.
_EPS = 1e-9

# `_repair_shortfalls` is now a defensive backstop, not a normal code path:
# with both ore-portion and direct-mineral variables solved as true integers
# (see the module docstring's decision 1), the MIP's own hard constraints
# already guarantee every requirement clears, up to _EPS solver noise. It's
# kept in case a future caller feeds in a degenerate case the solver reports
# success on despite float slop, or a genuine MIP time-limit fallback (see
# `_MIP_TIME_LIMIT_SECONDS`) that returned an under-covered incumbent. It
# still terminates the same way as before: adding whole portions strictly
# increases delivery, and an ore whose floored per-portion yield of some
# mineral is 0 can never close that gap but is filtered out before we get
# here.
_MAX_REPAIR_ROUNDS = 8

# Safety net for a real MIP solve, not expected to trigger at this tool's
# realistic scale (see the module docstring and
# test_realistic_scale_solves_quickly): HiGHS' own branch-and-bound can, on a
# pathological cost/yield structure (many near-tied ore choices), take
# multiple seconds rather than the sub-second this is called at request time
# for. Capping it means a genuinely adversarial instance degrades to "best
# plan found so far" (still feasible - HiGHS only reports incumbents that
# satisfy every constraint - just not provably cost-optimal) instead of
# hanging the request.
_MIP_TIME_LIMIT_SECONDS = 5.0


class OptimizationError(Exception):
    """Raised when no plan can satisfy the requirements at all (a mineral no
    ore yields and no market lists, or a solver failure - LP-infeasible or a
    genuine MIP failure with no usable incumbent at all; see
    `optimize_shopping_list`). refining/actions.py converts this to the
    app-wide ActionError - see CLAUDE.md's "ActionError is the one
    user-facing error type"."""


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

    # Solved once, continuous (no `integrality`), purely to report `lp_cost`
    # as a theoretical floor next to the real whole-unit total below - see
    # decision 1's docstring. The actual plan is never derived from this.
    relaxed = linprog(cost, A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs")
    if not relaxed.success:
        raise OptimizationError(f"Could not solve the shopping list ({relaxed.message.strip()}).")
    lp_cost = float(relaxed.fun)

    # ------------------------------------------------------- the real (integer) program
    # Both ore-portion AND direct-mineral columns are integer - see decision
    # 1's docstring for why direct-mineral buying needs this too, not just
    # ore. `_MIP_TIME_LIMIT_SECONDS` bounds worst-case solve time; on a
    # timeout HiGHS still returns its best incumbent (a genuinely feasible,
    # just not provably optimal, solution - see that constant's own comment).
    integrality = np.array([1] * n_ore + [1] * n_direct)
    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs",
                      integrality=integrality, options={"time_limit": _MIP_TIME_LIMIT_SECONDS})
    if result.x is None:
        raise OptimizationError(f"Could not solve the shopping list as a whole-unit plan ({result.message.strip()}).")

    # HiGHS' own integer variables still land at e.g. 2.9999999999996 or
    # 3.0000000000004 rather than an exact 3 - round(), not ceil(): unlike the
    # old relax-then-round-up step, there's no fractional remainder left to
    # round UP, only solver noise to round off.
    portions = {i: max(0, round(result.x[i])) for i in range(n_ore)}
    delivered = _delivered_from_ore(ores, portions, mineral_ids)
    _repair_shortfalls(ores, portions, delivered, mineral_ids, required, direct_price, names)
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

    # Whatever the solved ore quantities still don't cover is bought outright.
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
