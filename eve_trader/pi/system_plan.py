"""System plan: best colony combination inside one solar system - pure.

docs/PI_PLAN.md 3.5 step 3, docs/PI_TECHNICAL_DESIGN.md 3.7. The caller has
already turned every feasible chain/product on every PI planet of the system
into an `Option` (one colony, steady-state units/day in and out, plus its
volume-independent cost per day). This module picks how many colonies of each
option to build for `slots` colony slots spread over `characters`
characters, so that colonies may feed each other inside the system.

Model (mixed-integer program, `scipy.optimize.linprog` with `integrality`,
HiGHS - the same solver call style as refining/optimizer.py, deliberately not
`milp`):

    columns:  one per "unit" (see below), plus internal f_t, sold s_t and
              bought b_t (continuous, >= 0) per commodity type t
    maximise  sum_t s_t*(sell_t - freight_t) - sum_t b_t*(buy_t + freight_t)
              - sum_u x_u*fixed_u
    s.t.      f_t + s_t <= sum_u x_u*exports_u[t]       (made in-system)
              f_t + b_t  = sum_u x_u*imports_u[t]       (needed in-system)
              sum_u x_u <= slots
              sum_u on planet p x_u <= characters       [P-37]
              s_t <= max_sell_t; s_t = 0 if not sellable; b_t = 0 if not buyable

Three modelling decisions worth knowing:

1. **Exports are "<=", not "=".** Output that can neither be used in-system
   nor sold (no market, or above the `max_sell_per_day` cap) is discarded
   rather than making the plan infeasible. It still pays its customs tax,
   because `fixed_cost_per_day` already includes every export's tax - so a
   colony whose output goes nowhere is simply never worth building. Internal
   goods are never charged hub freight or market prices; the solver decides
   per type whether a unit is better used internally or sold (an internal
   unit is implicitly valued at its opportunity cost, matching chains.py).

2. **Repeated extraction on one planet: per-planet ranks, all extraction
   options together.** A factory colony is a plain integer column (0 ..
   characters). An extraction option instead gets one binary column per rank
   k = 1..K (K = characters, or `max_extraction_per_planet` if lower), whose
   flows are the option's flows x penalty^(k-1). Per planet and rank at most
   one extraction colony may take rank k, and rank k is only used if rank k-1
   is (ordering constraints), so the planet's k-th extraction colony - of
   whatever option - yields penalty^(k-1). This keeps the program linear.
   It is deliberately conservative: in EVE each P0 has its own deposit map,
   so two colonies extracting *different* P0s do not really compete. But an
   `Option` does not say which P0(s) it extracts (an option may extract two),
   so per-planet is the only grouping the data supports, and over-penalising
   is the safe side of the error. Since ranks are interchangeable, the solver
   naturally gives rank 1 to the most valuable extraction colony.
   The fixed cost of a rank-k colony is not scaled by default (customs on the
   reduced flow are overstated - again conservative); if the caller fills
   `Option.setup_cost_per_day` (the volume-independent part of
   `fixed_cost_per_day`), the remaining customs part is scaled by the same
   penalty factor.

3. **Robustness [P-38].** HiGHS gets a time limit; on a timeout its best
   incumbent is used (status "time_limit"). If it errors or returns no
   solution, a greedy plan is used instead (status "fallback"): repeatedly
   add the colony with the best marginal profit, evaluated with the same
   in-system settlement as the final report. Every reported figure (flows,
   profit) is recomputed from the integer colony counts by `_evaluate`, never
   read off the solver's continuous columns, so the solver path and the
   greedy path report on exactly the same basis.

"Best single uses" is the comparison the System page shows next to the plan:
the same greedy fill with internal flow switched off (every import bought,
every export sold), i.e. what the slots earn if each colony stands alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import linprog

# Solver noise guard (see refining/optimizer.py's own `_EPS`).
_EPS = 1e-9
_DEFAULT_TIME_LIMIT_SECONDS = 5.0


@dataclass(frozen=True)
class Option:
    """One candidate colony on one planet (steady state, per day)."""
    key: str                      # unique id, e.g. f"{planet_id}:{chain}:{product}"
    planet_id: int
    chain: str
    product_type_id: int
    is_extraction: bool
    exports: Mapping[int, float]  # type_id -> units/day leaving the colony
    imports: Mapping[int, float]  # type_id -> units/day hauled in
    fixed_cost_per_day: float     # customs on all exports+imports + setup amortisation
    label: str = ""
    # Optional: the volume-independent part of fixed_cost_per_day. Only used
    # to scale the customs part of a penalised extraction rank (decision 2).
    setup_cost_per_day: Optional[float] = None


@dataclass(frozen=True)
class Market:
    sell_value: Mapping[int, float]        # ISK/unit received at the hub (net); missing -> cannot sell
    buy_cost: Mapping[int, float]          # ISK/unit paid at the hub (incl. fees); missing -> cannot buy
    freight_per_unit: Mapping[int, float]  # hub freight per unit, for goods sold or bought
    max_sell_per_day: Mapping[int, float] = field(default_factory=dict)  # missing = unlimited


@dataclass(frozen=True)
class ChosenColony:
    key: str
    planet_id: int
    chain: str
    product_type_id: int
    label: str
    count: int
    # Yield factor of each built copy: (1.0,) for one colony, (1.0, 0.85) for
    # two extraction colonies on the same planet, (1.0, 1.0) for two factories.
    yield_factors: tuple[float, ...]


@dataclass(frozen=True)
class TypeFlow:
    type_id: int
    produced: float    # units/day exported by the chosen colonies
    consumed: float    # units/day imported by the chosen colonies
    internal: float    # produced in-system and consumed in-system
    sold: float
    bought: float
    discarded: float   # produced, neither used nor sold


@dataclass
class SystemPlan:
    status: str                         # "optimal" | "time_limit" | "fallback" | "infeasible"
    colonies: list[ChosenColony]
    flows: list[TypeFlow]
    profit_per_day: float
    profit_per_slot: float
    used_slots: int
    slots: int
    best_single_uses: list[ChosenColony]
    best_single_uses_profit_per_day: float
    chain_gain_per_day: float           # profit_per_day - best_single_uses_profit_per_day
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- units

@dataclass(frozen=True)
class _Unit:
    """One column of the program: an option at a yield rank."""
    option_index: int
    rank: int          # 0 for a factory column (integer, repeatable), k >= 1 for extraction rank k
    scale: float
    fixed: float


def _scaled_fixed(option: Option, scale: float) -> float:
    if scale >= 1.0 - _EPS or option.setup_cost_per_day is None:
        return float(option.fixed_cost_per_day)
    setup = float(option.setup_cost_per_day)
    return setup + (float(option.fixed_cost_per_day) - setup) * scale


def _build_units(options: Sequence[Option], max_rank: int, penalty: float) -> list[_Unit]:
    units: list[_Unit] = []
    for i, opt in enumerate(options):
        if opt.is_extraction:
            for k in range(1, max_rank + 1):
                scale = penalty ** (k - 1)
                units.append(_Unit(i, k, scale, _scaled_fixed(opt, scale)))
        else:
            units.append(_Unit(i, 0, 1.0, float(opt.fixed_cost_per_day)))
    return units


# ---------------------------------------------------------------- evaluation

def _settle_type(produced: float, needed: float, v: Optional[float], c: Optional[float],
                 cap: float, internal: bool) -> Optional[tuple[float, float, float]]:
    """Best (internal, sold, bought) for one type given the colony counts.

    Solves the per-type part of the program exactly: maximise v*s - c*b with
    f + s <= produced, f + b = needed, s <= cap. `v`/`c` are None when the
    type cannot be sold/bought. Returns None if the needed input cannot be
    covered (not buyable and not made in-system).
    """
    sellable = v is not None and v > 0 and cap > 0
    if not internal:
        if needed > _EPS and c is None:
            return None
        sold = min(produced, cap) if sellable else 0.0
        return 0.0, sold, needed
    if c is None:
        if produced < needed - 1e-7:
            return None
        sold = min(produced - needed, cap) if sellable else 0.0
        return needed, max(sold, 0.0), 0.0
    if sellable and v > c:
        # Selling own output and buying the input back is better (odd market).
        sold = min(produced, cap)
        f = min(produced - sold, needed)
        return f, sold, needed - f
    f = min(produced, needed) if c >= 0 else 0.0
    sold = min(produced - f, cap) if sellable else 0.0
    return f, sold, needed - f


@dataclass
class _Eval:
    feasible: bool
    profit: float
    flows: list[TypeFlow]


def _evaluate(options: Sequence[Option], picked: Sequence[_Unit], market: Market,
              values: dict[int, Optional[float]], costs: dict[int, Optional[float]],
              internal: bool = True) -> _Eval:
    produced: dict[int, float] = {}
    needed: dict[int, float] = {}
    fixed = 0.0
    for u in picked:
        opt = options[u.option_index]
        for t, q in opt.exports.items():
            produced[t] = produced.get(t, 0.0) + q * u.scale
        for t, q in opt.imports.items():
            needed[t] = needed.get(t, 0.0) + q * u.scale
        fixed += u.fixed
    profit = -fixed
    flows: list[TypeFlow] = []
    for t in sorted(set(produced) | set(needed)):
        p, n = produced.get(t, 0.0), needed.get(t, 0.0)
        cap = float(market.max_sell_per_day.get(t, math.inf))
        settled = _settle_type(p, n, values.get(t), costs.get(t), cap, internal)
        if settled is None:
            return _Eval(False, -math.inf, [])
        f, s, b = settled
        if s > 0:
            profit += s * values[t]
        if b > 0:
            profit -= b * costs[t]
        flows.append(TypeFlow(t, p, n, f, s, b, max(p - f - s, 0.0)))
    return _Eval(True, profit, flows)


# -------------------------------------------------------------------- greedy

def _greedy(options: Sequence[Option], units: Sequence[_Unit], market: Market,
            values, costs, slots: int, characters: int, internal: bool) -> list[_Unit]:
    """Add the unit with the best marginal profit until nothing helps.

    Respects slots, the per-planet character limit and the extraction rank
    order (the next extraction colony on a planet always takes the next rank).
    """
    picked: list[_Unit] = []
    per_planet: dict[int, int] = {}
    ranks_used: dict[int, int] = {}
    factory_units = [u for u in units if u.rank == 0]
    ext_by_option_rank = {(u.option_index, u.rank): u for u in units if u.rank > 0}
    ext_options = sorted({u.option_index for u in units if u.rank > 0})
    current = 0.0
    while len(picked) < slots:
        best: Optional[tuple[float, _Unit]] = None
        candidates = list(factory_units)
        for i in ext_options:
            nxt = ext_by_option_rank.get((i, ranks_used.get(options[i].planet_id, 0) + 1))
            if nxt is not None:
                candidates.append(nxt)
        for u in candidates:
            planet = options[u.option_index].planet_id
            if per_planet.get(planet, 0) >= characters:
                continue
            ev = _evaluate(options, picked + [u], market, values, costs, internal)
            if not ev.feasible:
                continue
            gain = ev.profit - current
            if gain > 1e-6 and (best is None or gain > best[0]):
                best = (gain, u)
        if best is None:
            break
        u = best[1]
        picked.append(u)
        current += best[0]
        planet = options[u.option_index].planet_id
        per_planet[planet] = per_planet.get(planet, 0) + 1
        if u.rank > 0:
            ranks_used[planet] = u.rank
    return picked


# ----------------------------------------------------------------------- MILP

def _solve_milp(options: Sequence[Option], units: Sequence[_Unit], types: list[int],
                values, costs, market: Market, slots: int, characters: int,
                time_limit_s: float) -> tuple[Optional[list[int]], str]:
    """Returns (count per unit, status) or (None, reason)."""
    nu, nt = len(units), len(types)
    col_f, col_s, col_b = nu, nu + nt, nu + 2 * nt
    n = nu + 3 * nt
    t_index = {t: j for j, t in enumerate(types)}

    c = np.zeros(n)
    for j, u in enumerate(units):
        c[j] = u.fixed                      # minimise cost - revenue
    bounds: list[tuple[float, Optional[float]]] = []
    for u in units:
        bounds.append((0, 1) if u.rank > 0 else (0, characters))
    bounds += [(0, None)] * nt              # internal
    for t in types:
        v = values.get(t)
        cap = market.max_sell_per_day.get(t)
        if v is None or v <= 0:
            bounds.append((0, 0))
        else:
            c[col_s + t_index[t]] = -v
            bounds.append((0, None if cap is None else max(float(cap), 0.0)))
    for t in types:
        cost = costs.get(t)
        if cost is None:
            bounds.append((0, 0))
        else:
            c[col_b + t_index[t]] = cost
            bounds.append((0, None))

    a_ub_rows: list[np.ndarray] = []
    b_ub: list[float] = []
    a_eq = np.zeros((nt, n))
    b_eq = np.zeros(nt)
    exp_rows = np.zeros((nt, n))
    for j, u in enumerate(units):
        opt = options[u.option_index]
        for t, q in opt.exports.items():
            exp_rows[t_index[t], j] -= q * u.scale
        for t, q in opt.imports.items():
            a_eq[t_index[t], j] += q * u.scale
    for k in range(nt):
        exp_rows[k, col_f + k] = 1.0
        exp_rows[k, col_s + k] = 1.0
        a_eq[k, col_f + k] = -1.0
        a_eq[k, col_b + k] = -1.0
    a_ub_rows.extend(exp_rows)
    b_ub.extend([0.0] * nt)

    row = np.zeros(n)
    row[:nu] = 1.0
    a_ub_rows.append(row)
    b_ub.append(float(slots))

    planets = sorted({options[u.option_index].planet_id for u in units})
    max_rank = max((u.rank for u in units), default=0)
    for p in planets:
        row = np.zeros(n)
        rank_rows = {k: np.zeros(n) for k in range(1, max_rank + 1)}
        for j, u in enumerate(units):
            if options[u.option_index].planet_id != p:
                continue
            row[j] = 1.0
            if u.rank > 0:
                rank_rows[u.rank][j] = 1.0
        a_ub_rows.append(row)
        b_ub.append(float(characters))
        for k in range(1, max_rank + 1):
            if not rank_rows[k].any():
                continue
            a_ub_rows.append(rank_rows[k])          # one colony per rank
            b_ub.append(1.0)
            if k >= 2:
                a_ub_rows.append(rank_rows[k] - rank_rows[k - 1])  # rank k needs rank k-1
                b_ub.append(0.0)

    integrality = np.array([1] * nu + [0] * (3 * nt))
    result = linprog(
        c,
        A_ub=np.vstack(a_ub_rows), b_ub=np.array(b_ub),
        A_eq=a_eq if nt else None, b_eq=b_eq if nt else None,
        bounds=bounds, method="highs", integrality=integrality,
        options={"time_limit": float(time_limit_s)},
    )
    if result.x is None:
        return None, (result.message or "no solution").strip()
    counts = [max(0, int(round(result.x[j]))) for j in range(nu)]
    return counts, ("optimal" if result.status == 0 else "time_limit")


# ---------------------------------------------------------------- public API

def _chosen(options: Sequence[Option], picked: Sequence[_Unit]) -> list[ChosenColony]:
    by_option: dict[int, list[float]] = {}
    for u in picked:
        by_option.setdefault(u.option_index, []).append(u.scale)
    out = []
    for i, scales in by_option.items():
        opt = options[i]
        out.append(ChosenColony(opt.key, opt.planet_id, opt.chain, opt.product_type_id,
                                opt.label, len(scales), tuple(sorted(scales, reverse=True))))
    out.sort(key=lambda c: (c.planet_id, c.key))
    return out


def _validate(options: Sequence[Option], slots: int, characters: int, penalty: float,
              max_extraction_per_planet: Optional[int]) -> None:
    if slots < 0:
        raise ValueError("slots must be >= 0")
    if characters < 0:
        raise ValueError("characters must be >= 0")
    if not 0 < penalty <= 1:
        raise ValueError("extraction_repeat_penalty must be in (0, 1]")
    if max_extraction_per_planet is not None and max_extraction_per_planet < 0:
        raise ValueError("max_extraction_per_planet must be >= 0")
    seen: set[str] = set()
    for opt in options:
        if opt.key in seen:
            raise ValueError(f"duplicate option key {opt.key!r}")
        seen.add(opt.key)
        for q in list(opt.exports.values()) + list(opt.imports.values()):
            if q < 0 or not math.isfinite(q):
                raise ValueError(f"option {opt.key!r} has a negative or non-finite flow")
        if not math.isfinite(opt.fixed_cost_per_day):
            raise ValueError(f"option {opt.key!r} has a non-finite fixed cost")


def plan_system(options: Sequence[Option], market: Market, slots: int, characters: int,
                extraction_repeat_penalty: float = 0.85,
                max_extraction_per_planet: Optional[int] = None,
                time_limit_s: float = _DEFAULT_TIME_LIMIT_SECONDS) -> SystemPlan:
    """Best colony combination for `slots` colonies by `characters` characters."""
    _validate(options, slots, characters, extraction_repeat_penalty, max_extraction_per_planet)

    def empty(note: str) -> SystemPlan:
        return SystemPlan("infeasible", [], [], 0.0, 0.0, 0, slots, [], 0.0, 0.0, [note])

    if not options:
        return empty("No colony options for this system.")
    if slots == 0:
        return empty("No colony slots available.")
    if characters == 0:
        return empty("No characters available.")

    max_rank = characters if max_extraction_per_planet is None else min(characters, max_extraction_per_planet)
    units = _build_units(options, max_rank, extraction_repeat_penalty)
    types = sorted({t for o in options for t in list(o.exports) + list(o.imports)})
    values: dict[int, Optional[float]] = {}
    costs: dict[int, Optional[float]] = {}
    for t in types:
        freight = float(market.freight_per_unit.get(t, 0.0))
        values[t] = float(market.sell_value[t]) - freight if t in market.sell_value else None
        costs[t] = float(market.buy_cost[t]) + freight if t in market.buy_cost else None

    notes: list[str] = []
    picked: Optional[list[_Unit]] = None
    status = "fallback"
    try:
        counts, solver_status = _solve_milp(options, units, types, values, costs, market,
                                            slots, characters, time_limit_s)
    except Exception as exc:  # noqa: BLE001 - any solver failure degrades to greedy [P-38]
        counts, solver_status = None, f"solver error: {exc}"
    if counts is not None:
        candidate = [u for u, n in zip(units, counts) for _ in range(n)]
        if _evaluate(options, candidate, market, values, costs).feasible:
            picked, status = candidate, solver_status
        else:
            solver_status = "solver returned an inconsistent plan"
    if picked is None:
        notes.append(f"Optimiser unavailable ({solver_status}); showing a greedy plan instead.")
        picked = _greedy(options, units, market, values, costs, slots, characters, internal=True)
    elif status == "time_limit":
        notes.append(f"Optimiser hit its {time_limit_s:g} s time limit; the plan is the best found, "
                     "not proven optimal.")

    ev = _evaluate(options, picked, market, values, costs)
    single = _greedy(options, units, market, values, costs, slots, characters, internal=False)
    single_ev = _evaluate(options, single, market, values, costs, internal=False)
    colonies = _chosen(options, picked)
    used = len(picked)

    for col in colonies:
        if any(f < 1.0 - _EPS for f in col.yield_factors):
            notes.append(f"Planet {col.planet_id}: repeated extraction colony ({col.label or col.key}); "
                         "a yield penalty is applied because extractors share deposits.")
    if used == 0:
        notes.append("No colony is profitable in this system with the current prices and taxes.")
    elif used < slots:
        notes.append(f"Only {used} of {slots} slots are used; more colonies would not add profit "
                     "(planet or character limits, or no profitable option left).")
    profit = ev.profit if used else 0.0
    single_profit = single_ev.profit if single else 0.0
    return SystemPlan(
        status=status,
        colonies=colonies,
        flows=ev.flows,
        profit_per_day=profit,
        profit_per_slot=profit / used if used else 0.0,
        used_slots=used,
        slots=slots,
        best_single_uses=_chosen(options, single),
        best_single_uses_profit_per_day=single_profit,
        chain_gain_per_day=profit - single_profit,
        notes=notes,
    )
