"""Shared integer program behind both mineral shopping lists
(refining/optimizer.py and module_reprocessing/shopping_optimizer.py).

    minimize    sum_{i,t} portions_it x cost_per_portion_it
              + sum_{j,t} direct_jt   x unit_cost_jt
              + sum_j     short_j     x penalty
    subject to  sum_{i,t} portions_it x yield_ij + sum_t direct_jt + short_j >= required_j
                0 <= portions_it <= floor(max_units_it / portion_size_i)   (integer)
                0 <= direct_jt   <= max_units_jt                             (integer)
                short_j >= 0

Each source (ore, module) and each mineral's direct purchase is split into
supply tiers (market_depth.SupplyTier): the real listed volume up to the
acceptable price, cheapest first. A tier with unknown depth is uncapped.
Because deeper tiers cost more, the solver takes them only when they still
beat every other source, and switches to other ores/modules once a cheap
source runs out (confirmed with the user 2026-11-04). `short_j` absorbs what
no source can cover at an acceptable price; its penalty is far above any real
way to buy one unit, so it is used only when nothing else is left, and the
callers report it as a shortfall instead of failing.

Pure: callers pass every price, yield and cap.
"""
from __future__ import annotations

import logging
import math
import pickle
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import linprog

from .market_depth import SupplyTier

log = logging.getLogger(__name__)

_EPS = 1e-9

# See refining/optimizer.py's own comment: on a timeout HiGHS returns its best
# feasible incumbent. Tier columns can triple the column count, so the cap
# matters more here than before.
MIP_TIME_LIMIT_SECONDS = 5.0

# HiGHS does not always honour time_limit (see mip_worker.py), so the worker
# process is killed at this deadline. It covers the solver's limit, importing
# scipy on a one-CPU server and HiGHS' usual overrun, and stays far below
# nginx's 60 s.
MIP_PROCESS_DEADLINE_SECONDS = 20.0

_APP_ROOT = Path(__file__).resolve().parent.parent


class SolveError(Exception):
    """The solver returned no plan at all (never for a shortfall)."""


@dataclass
class SupplySource:
    """One reprocessable item: whole portions, each yielding
    `yield_per_portion`, bought from `tiers` (unit costs per unit, not per
    portion)."""
    portion_size: int
    yield_per_portion: dict[int, int]
    tiers: list[SupplyTier]


@dataclass
class SupplySolution:
    # (source index, tier index) -> whole portions bought from that tier
    portions: dict[tuple[int, int], int] = field(default_factory=dict)
    # (mineral id, tier index) -> units bought directly from that tier
    direct: dict[tuple[int, int], int] = field(default_factory=dict)
    # mineral id -> units no source covers at an acceptable price
    short: dict[int, int] = field(default_factory=dict)
    lp_cost: float = 0.0
    # True when the integer solve gave no answer and this plan is the
    # continuous optimum rounded up: it covers the list but may cost slightly
    # more than the best whole-unit plan.
    approximate: bool = False


def _tier_cap_portions(tier: SupplyTier, portion_size: int) -> Optional[int]:
    return None if tier.max_units is None else tier.max_units // portion_size


def _solve_mip_in_child(kwargs: dict) -> Optional[np.ndarray]:
    """The integer solution from a mip_worker process, or None when it found
    none, failed or passed MIP_PROCESS_DEADLINE_SECONDS (subprocess.run kills
    it then)."""
    try:
        done = subprocess.run([sys.executable, "-m", "eve_trader.mip_worker"], input=pickle.dumps(kwargs),
                              capture_output=True, timeout=MIP_PROCESS_DEADLINE_SECONDS, cwd=_APP_ROOT)
    except subprocess.TimeoutExpired:
        log.warning("Mineral shopping list: integer solve killed after %.0f s, using the rounded LP plan.",
                    MIP_PROCESS_DEADLINE_SECONDS)
        return None
    except OSError as e:
        log.warning("Mineral shopping list: could not start the solver process (%s), using the rounded LP plan.", e)
        return None
    if done.returncode != 0:
        log.warning("Mineral shopping list: solver process exited with %s, using the rounded LP plan: %s",
                    done.returncode, done.stderr[-500:].decode(errors="replace"))
        return None
    try:
        return pickle.loads(done.stdout)["x"]
    except (pickle.UnpicklingError, EOFError, KeyError, AttributeError, ValueError) as e:
        log.warning("Mineral shopping list: unreadable solver output (%s), using the rounded LP plan.", e)
        return None


# Module attribute so tests can run the solve in-process (a worker per solve
# would make the brute-force tests take minutes).
_run_mip = _solve_mip_in_child


def _useful_portions(source: SupplySource, required: dict[int, float]) -> int:
    """Portions that already cover every requirement this source yields. Buying
    more only adds cost, so this upper bound never cuts off an optimum. The bound
    size matters to HiGHS: with bounds in the billions (listed market depth) it
    hung on aarch64, ignoring its time limit (see mip_worker.py and CLAUDE.md)."""
    return max((math.ceil(required[m] / qty) for m, qty in source.yield_per_portion.items()
                if m in required and qty > 0), default=0)


def solve(mineral_ids: list[int], required: dict[int, float], sources: list[SupplySource],
          direct_tiers: dict[int, list[SupplyTier]]) -> SupplySolution:
    columns: list[tuple[str, object, int]] = []   # (kind, key, tier index)
    costs: list[float] = []
    upper: list[float] = []
    for i, source in enumerate(sources):
        useful = _useful_portions(source, required)
        for t, tier in enumerate(source.tiers):
            cap = _tier_cap_portions(tier, source.portion_size)
            if cap == 0:
                continue
            columns.append(("source", i, t))
            costs.append(tier.unit_cost * source.portion_size)
            upper.append(useful if cap is None else min(cap, useful))
    for mineral_id in mineral_ids:
        useful = math.ceil(required[mineral_id])
        for t, tier in enumerate(direct_tiers.get(mineral_id, [])):
            if tier.max_units == 0:
                continue
            columns.append(("direct", mineral_id, t))
            costs.append(tier.unit_cost)
            upper.append(useful if tier.max_units is None else min(tier.max_units, useful))
    n_real = len(columns)

    # Shortfall columns: one unit must cost more than any real way to get a
    # unit of anything (a whole portion yields at least one unit).
    penalty = 10.0 * max(costs, default=1.0) + 1.0
    costs += [penalty] * len(mineral_ids)
    upper += [math.ceil(required[m]) for m in mineral_ids]

    row_of = {m: r for r, m in enumerate(mineral_ids)}
    a_ub = np.zeros((len(mineral_ids), len(costs)), dtype=float)
    for col, (kind, key, _t) in enumerate(columns):
        if kind == "source":
            for mineral_id, qty in sources[key].yield_per_portion.items():
                if mineral_id in row_of:
                    a_ub[row_of[mineral_id], col] = -float(qty)
        else:
            a_ub[row_of[key], col] = -1.0
    for r in range(len(mineral_ids)):
        a_ub[r, n_real + r] = -1.0
    b_ub = np.array([-required[m] for m in mineral_ids], dtype=float)
    bounds = [(0, u) for u in upper]
    cost = np.array(costs, dtype=float)

    relaxed = linprog(cost, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs",
                      options={"time_limit": MIP_TIME_LIMIT_SECONDS})
    if not relaxed.success:
        raise SolveError(f"Could not solve the shopping list ({relaxed.message.strip()}).")
    relaxed_short = float(sum(relaxed.x[n_real:]))
    lp_cost = float(relaxed.fun) - penalty * relaxed_short

    # Shortfall is whole units too: a continuous one would cover a fractional
    # requirement remainder for a fraction of the penalty instead of buying a unit.
    integrality = np.array([1] * (n_real + len(mineral_ids)))
    x = _run_mip({"c": cost, "A_ub": a_ub, "b_ub": b_ub, "bounds": bounds, "method": "highs",
                  "integrality": integrality, "options": {"time_limit": MIP_TIME_LIMIT_SECONDS}})
    approximate = x is None
    if approximate:
        # Rounding up stays within every cap (caps are whole numbers) and still
        # covers every requirement. At most one fractional column per mineral
        # in a basic solution, so the extra cost is a few portions.
        x = np.ceil(relaxed.x - _EPS)

    solution = SupplySolution(lp_cost=lp_cost, approximate=approximate)
    delivered = {m: 0 for m in mineral_ids}
    for col, (kind, key, t) in enumerate(columns):
        value = max(0, round(x[col]))
        if value <= 0:
            continue
        if kind == "source":
            solution.portions[(key, t)] = value
            for mineral_id, qty in sources[key].yield_per_portion.items():
                if mineral_id in delivered:
                    delivered[mineral_id] += value * qty
        else:
            solution.direct[(key, t)] = value
            delivered[key] += value
    for mineral_id in mineral_ids:
        gap = required[mineral_id] - delivered[mineral_id]
        if gap > _EPS:
            solution.short[mineral_id] = math.ceil(gap - _EPS)
    return solution


def all_direct_cost(mineral_ids: list[int], required: dict[int, float],
                    direct_tiers: dict[int, list[SupplyTier]]) -> Optional[float]:
    """Baseline: buy every required mineral outright, cheapest tier first.
    None when the listed depth cannot cover a requirement."""
    total = 0.0
    for mineral_id in mineral_ids:
        need = math.ceil(required[mineral_id] - _EPS)
        for tier in sorted(direct_tiers.get(mineral_id, []), key=lambda t: t.unit_cost):
            take = need if tier.max_units is None else min(need, tier.max_units)
            total += take * tier.unit_cost
            need -= take
            if need <= 0:
                break
        if need > 0:
            return None
    return total


# ------------------------------------------------------------ adapter helpers
def option_tiers(landed_cost_per_unit: float, tiers: Optional[list[SupplyTier]],
                 hub_region_id: Optional[int] = None) -> list[SupplyTier]:
    """An option's tiers; without depth data, one uncapped tier at its price."""
    if tiers is not None:
        return list(tiers)
    return [SupplyTier(unit_cost=landed_cost_per_unit, max_units=None, hub_region_id=hub_region_id)]


def mineral_tiers(landed_cost_per_unit: Optional[float], tiers: Optional[list[SupplyTier]],
                  source: Optional[str], hub_region_id: Optional[int]) -> list[SupplyTier]:
    if tiers is not None:
        return list(tiers)
    if landed_cost_per_unit is None:
        return []
    return [SupplyTier(unit_cost=landed_cost_per_unit, max_units=None, source=source, hub_region_id=hub_region_id)]


@dataclass
class SourceTotal:
    portions: int
    units: int
    total_cost: float
    depth_checked: bool


def source_total(solution: SupplySolution, index: int, source: SupplySource) -> Optional[SourceTotal]:
    portions = units = 0
    total_cost = 0.0
    checked = True
    for t, tier in enumerate(source.tiers):
        bought = solution.portions.get((index, t), 0)
        if bought <= 0:
            continue
        portions += bought
        units += bought * source.portion_size
        total_cost += bought * source.portion_size * tier.unit_cost
        checked = checked and tier.depth_checked
    if portions <= 0:
        return None
    return SourceTotal(portions=portions, units=units, total_cost=total_cost, depth_checked=checked)


@dataclass
class DirectTotal:
    source: Optional[str]
    hub_region_id: Optional[int]
    quantity: int
    total_cost: float
    depth_checked: bool


def direct_totals(solution: SupplySolution, mineral_id: int, tiers: list[SupplyTier]) -> list[DirectTotal]:
    """One line per (source, hub) the mineral is bought from directly."""
    grouped: dict[tuple, DirectTotal] = {}
    for t, tier in enumerate(tiers):
        bought = solution.direct.get((mineral_id, t), 0)
        if bought <= 0:
            continue
        key = (tier.source, tier.hub_region_id)
        line = grouped.setdefault(key, DirectTotal(tier.source, tier.hub_region_id, 0, 0.0, True))
        line.quantity += bought
        line.total_cost += bought * tier.unit_cost
        line.depth_checked = line.depth_checked and tier.depth_checked
    return list(grouped.values())


def delivered_from_sources(solution: SupplySolution, sources: list[SupplySource],
                           mineral_ids: list[int]) -> dict[int, int]:
    delivered = {m: 0 for m in mineral_ids}
    for (i, _t), bought in solution.portions.items():
        for mineral_id, qty in sources[i].yield_per_portion.items():
            if mineral_id in delivered:
                delivered[mineral_id] += bought * qty
    return delivered
