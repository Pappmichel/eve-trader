"""Chain planner: one target product (P1-P4) across the planets of one system
and the player's characters - pure, no I/O.

docs/PI_PLAN.md 3.4/3.5, docs/PI_TECHNICAL_DESIGN.md 3.7. `system_plan.py`
answers "what is most profitable in this system"; this module answers "how
do I make product X here", e.g. a P0 -> P4 chain over several planets and
characters. The caller (chain_actions.py) builds the candidate colonies with
`build_options` (engine.best_design restricted to the target's recipe tree,
costed with economics.compute) and the market with `build_market`.

Model (mixed-integer program, `scipy.optimize.linprog(integrality=...)`,
HiGHS, same call style as system_plan.py / refining/optimizer.py):

    x[g,o]   integer   colonies of option o built by characters of group g.
                       A group is all characters with the same CC level and
                       planet count; options exist per CC level, and a group
                       may only build options of its own level.
    z[o]     cont.     run level of factory option o (0..sum_g x[g,o]): a
                       factory colony may run below capacity when it is fed by
                       fewer extraction colonies than it could process. The
                       reported design of such a colony is resized to the rate
                       it really runs at (engine.factories_for_rate).
    r[o,k]   binary    extraction option o is the k-th extraction colony on
                       its planet; its flows are scaled by penalty^(k-1)
                       (system_plan's per-planet rank idea: extractors share
                       deposits; ranks are ordered, at most one per rank).
    f/s/b[t] cont.     per commodity: used internally / sold / bought.
    T        cont.     target units per day.

    exports:  f_t + s_t <= sum(flows out of t)    (surplus may be discarded)
    imports:  f_t + b_t  = sum(flows into t)
    target:   q_i * T   <= sum(flows out of i)    for every "top" type i
    slots:    sum_o x[g,o] <= members(g) * planets(g)
    one CC per character per planet:
              sum_{o on planet p} x[g,o] <= members(g)
    z[o] <= sum_g x[g,o];  sum_k r[o,k] = sum_g x[g,o]

Forcing the target - lexicographic, two solves:

    Phase A  maximise T, buying only what the system cannot make from its own
             materials ("makeable" = some option exports it and all of that
             option's inputs are makeable, recursively from extraction).
             T_A is the most target the slots can make inside the system.
    Phase B  maximise profit (same economics as system_plan: market sale of
             the target and of surplus, market purchase of what is missing,
             customs on every colony's flows, setup amortisation, freight
             only for goods sold or bought) subject to T >= T_A.
             If the target can only be made by also buying makeable inputs
             (phase A repeated with every purchase allowed, see below), T is
             bounded by the slots alone; forcing that maximum would build a
             buy-everything plan however much it loses, so the floor is then
             the output of the smallest top-stage colony and profit decides
             the rest.
             With `allow_buy=True` phase B may also buy makeable
             intermediates when that is cheaper (e.g. it frees slots for
             more top-stage colonies) - every such purchase is reported.

So the plan always produces the most target the system can make, and profit
only decides *how*. Intermediates are never sold at the expense of the target:
selling one below T_A is impossible, and the settlement (`_evaluate`) uses
own output internally first and sells only what is left over once every
consumer in the system is fed (the stage above is saturated). If the system
cannot make the target from its own materials with the given slots, phase A
is repeated allowing every purchase (if `allow_buy`); if the target cannot be
made at all (e.g. a P4 without a Barren/Temperate planet), the plan descends
one level: the "tops" become the target's inputs and T counts target
equivalents (min over inputs of output / quantity per target unit), to be
hauled to a system that can build the top stage.

Every reported figure is recomputed from the integer colony counts and run
levels by `_evaluate`; if HiGHS fails or returns an inconsistent plan, a
greedy plan (add the colony with the best (target, profit) gain) is used
instead (status "fallback"). Normal infeasible input never raises - the plan
carries a status and notes; ValueError only for malformed input.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import linprog

from . import constants as C
from . import economics as econ
from . import engine
from .model import CHAINS, EXTRACTION_CHAINS, Design, Planet, StaticData
from .system_plan import Market

_EPS = 1e-9
_TOL = 1e-6
# Total solver budget per request; the two phases share it. 30 s solves the
# usual one-system cases to optimality (~15 s) and stays below nginx's 60 s
# proxy_read_timeout even with model building on top.
DEFAULT_TIME_LIMIT_S = 30.0
DEFAULT_REPEAT_PENALTY = 0.85
DEFAULT_MAX_EXTRACTION_PER_PLANET = 5
MAX_PLANETS_PER_CHARACTER = C.BASE_PLANETS_PER_CHARACTER + 5
MAX_TOP_LEVELS = 3

_STAGE_CHAINS: dict[int, tuple[str, ...]] = {
    1: ("P0-P1",),
    2: ("P0-P2", "P1-P2"),
    3: ("P2-P3", "P1-P3"),
    4: ("P3-P4", "P2-P4", "P1-P4"),
}


# ------------------------------------------------------------------ inputs
@dataclass(frozen=True)
class CharacterSpec:
    key: str
    name: str
    planets: int        # colony slots (1 + Interplanetary Consolidation)
    cc_level: int       # Command Center Upgrades


@dataclass(frozen=True)
class ChainOption:
    """One candidate colony on one planet at one CC level (per day, full run)."""
    key: str
    planet_id: int
    cc_level: int
    chain: str
    product_type_id: int
    is_extraction: bool
    exports: Mapping[int, float]
    imports: Mapping[int, float]
    setup_cost_per_day: float       # setup amortisation (volume independent)
    customs_cost_per_day: float     # export + import customs at full run
    design: Optional[dict] = None   # Design.to_dict()
    effective_factor: float = 1.0   # Evaluation.effective_factor (to resize designs)
    label: str = ""


# ------------------------------------------------------------------ output
@dataclass(frozen=True)
class Assignment:
    character_key: str
    character_name: str
    cc_level: int
    planet_id: int
    planet_name: str
    option_key: str
    chain: str
    product_type_id: int
    is_extraction: bool
    design: Optional[dict]
    run_level: float          # factories: share of capacity used; extraction: 1
    yield_factor: float       # extraction rank penalty; factories: 1
    units_per_day: float      # product leaving the colony
    exports: dict[int, float]
    imports: dict[int, float]


@dataclass(frozen=True)
class StageSummary:
    type_id: int
    tier: int
    in_tree: bool
    colonies: int             # colonies whose product is this type
    made: float               # units/day leaving colonies
    needed: float             # units/day hauled into colonies
    internal: float
    bought: float
    sold: float
    discarded: float


@dataclass(frozen=True)
class Purchase:
    type_id: int
    units_per_day: float
    cost_per_day: float
    reason_code: str          # no_resource | no_colony | missing_inputs | not_here | no_capacity | cheaper
    reason: str


@dataclass
class ChainPlan:
    status: str               # optimal | time_limit | fallback | infeasible
    target_type_id: int
    mode: str                 # "target": T counts the target; "inputs": its inputs (top stage elsewhere)
    top_type_ids: list[int]
    target_units_per_day: float
    max_target_units_per_day: float
    profit_per_day: float
    profit_per_slot: float
    used_slots: int
    slots: int
    assignments: list[Assignment]
    stages: list[StageSummary]
    purchases: list[Purchase]
    characters: list[dict]
    notes: list[str] = field(default_factory=list)

    @property
    def free_slots(self) -> int:
        return self.slots - self.used_slots


# ------------------------------------------------------------- recipe tree
def tree_types(static: StaticData, target: int) -> dict[int, int]:
    """Every type in the target's recipe tree (P0 included) -> tier."""
    out: dict[int, int] = {}

    def walk(t: int) -> None:
        tier = static.tier(t)
        if tier is None or t in out:
            return
        out[t] = tier
        s = static.schematic_by_output.get(t)
        if s is not None and tier > 0:
            for i, _q in s.inputs:
                walk(i)

    walk(target)
    return out


def candidate_stages(static: StaticData, target: int, planet_type_id: int) -> list[tuple[str, int]]:
    """(chain, product) colonies of the target's tree that can exist on this
    planet type: P0->P1 for every P1 whose P0 the type carries, P0->P2 where
    both P0s are here, single-stage factories for every made type, and the
    multi-stage chains (P1-P3, P2-P4, P1-P4) where they fit."""
    out = []
    for t, tier in sorted(tree_types(static, target).items(), key=lambda kv: (kv[1], kv[0])):
        for chain in _STAGE_CHAINS.get(tier, ()):
            if engine.check_feasible(static, planet_type_id, chain, t) is None:
                out.append((chain, t))
    return out


def build_options(static: StaticData, target: int, planets: Sequence[Planet], cc_levels: Sequence[int],
                  assumptions: engine.Assumptions, prices: econ.Prices, settings: econ.MarketSettings,
                  factory_planet_limit: Optional[int] = None, exact_radius: bool = False) -> list[ChainOption]:
    """Candidate colonies for `plan_chain`. Extraction colonies on every
    planet that carries the P0; factory colonies only on the
    `factory_planet_limit` smallest planets (P4 stages: the as many smallest
    Barren/Temperate planets) - a character can place at most one colony
    per planet, so more factory planets than slots per character never help,
    and small planets have the cheapest links."""
    factory_ids: Optional[set] = None
    p4_ids: Optional[set] = None
    if factory_planet_limit is not None:
        by_size = sorted(planets, key=lambda p: (p.radius_km, p.planet_id or 0))
        high_tech = [p for p in by_size if static.has_kind(C.KIND_HIGH_TECH, p.planet_type_id)]
        factory_ids = {p.planet_id for p in by_size[:factory_planet_limit]}
        p4_ids = {p.planet_id for p in high_tech[:factory_planet_limit]}
    stages_by_type: dict[int, list[tuple[str, int]]] = {}
    options: list[ChainOption] = []
    for planet in planets:
        pt = planet.planet_type_id
        if pt not in stages_by_type:
            stages_by_type[pt] = candidate_stages(static, target, pt)
        for chain, product in stages_by_type[pt]:
            if chain not in EXTRACTION_CHAINS and factory_ids is not None:
                allowed = p4_ids if CHAINS[chain][1] == 4 else factory_ids
                if planet.planet_id not in allowed:
                    continue
            for lv in sorted(set(int(v) for v in cc_levels)):
                ev, _why = engine.best_design(static, planet, chain, product, lv, assumptions, exact_radius)
                if ev is None or ev.effective_product_per_hour <= 0:
                    continue
                e = econ.compute(ev, static, prices, settings)
                f = 24.0 * ev.effective_factor
                options.append(ChainOption(
                    key=f"{planet.planet_id}:{lv}:{chain}:{product}", planet_id=int(planet.planet_id),
                    cc_level=lv, chain=chain, product_type_id=product, is_extraction=chain in EXTRACTION_CHAINS,
                    exports={t: q * f for t, q in ev.exports.items()},
                    imports={t: q * f for t, q in ev.imports.items()},
                    setup_cost_per_day=e.setup_per_day,
                    customs_cost_per_day=e.export_tax_per_day + e.import_tax_per_day,
                    design=ev.design.to_dict(), effective_factor=ev.effective_factor,
                    label=f"{static.name(product)} ({chain})",
                ))
    return options


def build_market(static: StaticData, prices: econ.Prices, settings: econ.MarketSettings) -> Market:
    """Net sale value / purchase cost / hub freight per unit (system_plan's Market)."""
    sell = {t: econ.output_unit_value(t, prices, settings) for t in static.commodities}
    buy = {t: econ.input_unit_cost(t, prices, settings) for t in static.commodities}
    return Market(
        sell_value={t: v for t, v in sell.items() if v is not None},
        buy_cost={t: v for t, v in buy.items() if v is not None},
        freight_per_unit={t: c.volume * settings.freight_per_m3 for t, c in static.commodities.items()},
    )


# ------------------------------------------------------------------ model
@dataclass
class _Group:
    cc_level: int
    planets: int
    members: list[CharacterSpec]

    @property
    def slots(self) -> int:
        return self.planets * len(self.members)


def _groups(characters: Sequence[CharacterSpec]) -> list[_Group]:
    by_key: dict[tuple[int, int], _Group] = {}
    for c in characters:
        k = (c.cc_level, c.planets)
        if k not in by_key:
            by_key[k] = _Group(c.cc_level, c.planets, [])
        by_key[k].members.append(c)
    return list(by_key.values())


def _makeable(options: Sequence[ChainOption]) -> set[int]:
    """Types the system can make from its own materials (fixed point)."""
    made: set[int] = set()
    changed = True
    while changed:
        changed = False
        for o in options:
            if all(i in made for i in o.imports):
                for t in o.exports:
                    if t not in made:
                        made.add(t)
                        changed = True
    return made


def _top_levels(static: StaticData, target: int) -> list[dict[int, float]]:
    """Level 0: the target itself; level k+1: the inputs of level k's
    multi-tier types, with units needed per target unit."""
    levels = [{target: 1.0}]
    while len(levels) < MAX_TOP_LEVELS:
        cur = levels[-1]
        nxt: dict[int, float] = {}
        expanded = False
        for t, q in cur.items():
            s = static.schematic_by_output.get(t)
            if s is None or (static.tier(t) or 0) < 2:
                nxt[t] = nxt.get(t, 0.0) + q
                continue
            expanded = True
            for i, qi in s.inputs:
                nxt[i] = nxt.get(i, 0.0) + q * qi / s.output_qty
        if not expanded:
            break
        levels.append(nxt)
    return levels


@dataclass
class _Sol:
    counts: dict[tuple[int, int], int]       # (group, option) -> colonies
    runs: dict[int, float]                   # factory option -> total run level
    scales: dict[int, list[float]]           # extraction option -> yield factor per copy


@dataclass
class _Eval:
    feasible: bool
    profit: float
    target: float
    flows: dict[int, tuple[float, float, float, float, float, float]]  # p, n, f, s, b, discarded


class _Model:
    def __init__(self, options: Sequence[ChainOption], groups: Sequence[_Group], unlimited: bool,
                 max_rank: int, penalty: float, values: dict, costs: dict):
        self.options, self.groups, self.unlimited = options, groups, unlimited
        self.max_rank, self.penalty = max_rank, penalty
        self.values, self.costs = values, costs
        self.types = sorted({t for o in options for t in list(o.exports) + list(o.imports)})
        self.x: dict[tuple[int, int], int] = {}
        self.z: dict[int, int] = {}
        self.r: dict[tuple[int, int], int] = {}
        n = 0
        for gi, g in enumerate(groups):
            for oi, o in enumerate(options):
                if o.cc_level == g.cc_level:
                    self.x[(gi, oi)] = n
                    n += 1
        for oi, o in enumerate(options):
            if not o.is_extraction:
                self.z[oi] = n
                n += 1
            elif not unlimited:
                for k in range(1, max_rank + 1):
                    self.r[(oi, k)] = n
                    n += 1
        self.n_dec = n
        nt = len(self.types)
        self.col_f, self.col_s, self.col_b = n, n + nt, n + 2 * nt
        self.col_t = n + 3 * nt
        self.n = self.col_t + 1
        self.t_index = {t: j for j, t in enumerate(self.types)}

        # flow columns: (col, option index, scale)
        self.flow_cols: list[tuple[int, int, float]] = []
        self.fixed = np.zeros(self.n)
        for (gi, oi), col in self.x.items():
            o = options[oi]
            if not o.is_extraction:
                self.fixed[col] = o.setup_cost_per_day
            elif unlimited:
                self.fixed[col] = o.setup_cost_per_day + o.customs_cost_per_day
                self.flow_cols.append((col, oi, 1.0))
        for oi, col in self.z.items():
            self.fixed[col] = options[oi].customs_cost_per_day
            self.flow_cols.append((col, oi, 1.0))
        for (oi, k), col in self.r.items():
            o, scale = options[oi], penalty ** (k - 1)
            self.fixed[col] = o.setup_cost_per_day + o.customs_cost_per_day * scale
            self.flow_cols.append((col, oi, scale))

        self.integrality = np.zeros(self.n)
        for col in list(self.x.values()) + list(self.r.values()):
            self.integrality[col] = 1

    # -------------------------------------------------------------- rows
    def _rows(self, tops: Mapping[int, float]):
        n, nt, opts = self.n, len(self.types), self.options
        exp = np.zeros((nt, n))
        imp = np.zeros((nt, n))
        for col, oi, scale in self.flow_cols:
            for t, q in opts[oi].exports.items():
                exp[self.t_index[t], col] += q * scale
            for t, q in opts[oi].imports.items():
                imp[self.t_index[t], col] += q * scale
        ub: list[np.ndarray] = []
        bub: list[float] = []
        for k in range(nt):
            row = -exp[k].copy()
            row[self.col_f + k] += 1.0
            row[self.col_s + k] += 1.0
            ub.append(row)
            bub.append(0.0)
        for t, q in tops.items():
            row = -exp[self.t_index[t]].copy()
            row[self.col_t] = q
            ub.append(row)
            bub.append(0.0)
        for gi, g in enumerate(self.groups):
            row = np.zeros(n)
            for (g2, _oi), col in self.x.items():
                if g2 == gi:
                    row[col] = 1.0
            ub.append(row)
            bub.append(float(g.slots))
            if not self.unlimited:
                for p in sorted({o.planet_id for o in opts}):
                    row = np.zeros(n)
                    for (g2, oi), col in self.x.items():
                        if g2 == gi and opts[oi].planet_id == p:
                            row[col] = 1.0
                    if row.any():
                        ub.append(row)
                        bub.append(float(len(g.members)))
        for oi, zcol in self.z.items():
            row = np.zeros(n)
            row[zcol] = 1.0
            for (_g, o2), col in self.x.items():
                if o2 == oi:
                    row[col] -= 1.0
            ub.append(row)
            bub.append(0.0)
        eq: list[np.ndarray] = []
        beq: list[float] = []
        for k in range(nt):
            row = imp[k].copy()
            row[self.col_f + k] -= 1.0
            row[self.col_b + k] -= 1.0
            eq.append(row)
            beq.append(0.0)
        if not self.unlimited:
            ext = [oi for oi, o in enumerate(opts) if o.is_extraction]
            for oi in ext:
                row = np.zeros(n)
                for k in range(1, self.max_rank + 1):
                    row[self.r[(oi, k)]] = 1.0
                for (_g, o2), col in self.x.items():
                    if o2 == oi:
                        row[col] -= 1.0
                eq.append(row)
                beq.append(0.0)
            for p in sorted({opts[oi].planet_id for oi in ext}):
                prev = None
                for k in range(1, self.max_rank + 1):
                    row = np.zeros(n)
                    for oi in ext:
                        if opts[oi].planet_id == p:
                            row[self.r[(oi, k)]] = 1.0
                    ub.append(row)
                    bub.append(1.0)
                    if prev is not None:
                        ub.append(row - prev)
                        bub.append(0.0)
                    prev = row
        return np.vstack(ub), np.array(bub), (np.vstack(eq) if eq else None), (np.array(beq) if eq else None)

    def _bounds(self, buyable: Callable[[int], bool], t_floor: Optional[float]):
        bounds: list[tuple[float, Optional[float]]] = [(0.0, None)] * self.n
        for (gi, _oi), col in self.x.items():
            g = self.groups[gi]
            bounds[col] = (0.0, float(g.slots if self.unlimited else len(g.members)))
        for col in self.r.values():
            bounds[col] = (0.0, 1.0)
        for t, k in self.t_index.items():
            v = self.values.get(t)
            bounds[self.col_s + k] = (0.0, None) if v is not None and v > 0 else (0.0, 0.0)
            bounds[self.col_b + k] = (0.0, None) if buyable(t) else (0.0, 0.0)
        bounds[self.col_t] = (0.0 if t_floor is None else t_floor, None)
        return bounds

    def solve(self, tops: Mapping[int, float], buyable: Callable[[int], bool], phase: str,
              t_floor: Optional[float], time_limit_s: float) -> tuple[Optional[np.ndarray], str]:
        a_ub, b_ub, a_eq, b_eq = self._rows(tops)
        c = np.zeros(self.n)
        if phase == "A":
            c[self.col_t] = -1.0
        else:
            c[:] = self.fixed
            for t, k in self.t_index.items():
                v, cost = self.values.get(t), self.costs.get(t)
                if v is not None and v > 0:
                    c[self.col_s + k] = -v
                if cost is not None:
                    c[self.col_b + k] = cost
        result = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq,
                         bounds=self._bounds(buyable, t_floor), method="highs",
                         integrality=self.integrality, options={"time_limit": max(0.1, float(time_limit_s))})
        if result.x is None:
            return None, (result.message or "no solution").strip()
        return result.x, ("optimal" if result.status == 0 else "time_limit")

    def to_sol(self, xv: np.ndarray) -> _Sol:
        counts = {key: int(round(xv[col])) for key, col in self.x.items() if round(xv[col]) > 0}
        runs: dict[int, float] = {}
        scales: dict[int, list[float]] = {}
        for oi, o in enumerate(self.options):
            copies = sum(n for (_g, o2), n in counts.items() if o2 == oi)
            if not o.is_extraction:
                if copies:
                    runs[oi] = min(float(copies), max(0.0, float(xv[self.z[oi]])))
            elif self.unlimited:
                if copies:
                    scales[oi] = [1.0] * copies
            else:
                ranks = [self.penalty ** (k - 1) for k in range(1, self.max_rank + 1)
                         if xv[self.r[(oi, k)]] > 0.5]
                if ranks or copies:
                    if len(ranks) != copies:
                        raise ValueError("inconsistent extraction ranks")
                    scales[oi] = sorted(ranks, reverse=True)
        return _Sol(counts, runs, scales)


# ------------------------------------------------------------- evaluation
def _evaluate(options: Sequence[ChainOption], sol: _Sol, values: dict, costs: dict,
              buyable: Callable[[int], bool], tops: Mapping[int, float]) -> _Eval:
    produced: dict[int, float] = {}
    needed: dict[int, float] = {}
    fixed = 0.0
    for oi, run in sol.runs.items():
        o = options[oi]
        copies = sum(n for (_g, o2), n in sol.counts.items() if o2 == oi)
        fixed += copies * o.setup_cost_per_day + run * o.customs_cost_per_day
        for t, q in o.exports.items():
            produced[t] = produced.get(t, 0.0) + q * run
        for t, q in o.imports.items():
            needed[t] = needed.get(t, 0.0) + q * run
    for oi, scales in sol.scales.items():
        o = options[oi]
        for sc in scales:
            fixed += o.setup_cost_per_day + o.customs_cost_per_day * sc
            for t, q in o.exports.items():
                produced[t] = produced.get(t, 0.0) + q * sc
    profit = -fixed
    flows: dict[int, tuple[float, float, float, float, float, float]] = {}
    for t in sorted(set(produced) | set(needed)):
        p, n = produced.get(t, 0.0), needed.get(t, 0.0)
        f = min(p, n)
        b = n - f
        if b > _TOL * max(1.0, n):
            if not buyable(t):
                return _Eval(False, -math.inf, 0.0, {})
        else:
            b = 0.0
        v = values.get(t)
        s = (p - f) if v is not None and v > 0 else 0.0
        if s > 0:
            profit += s * v
        if b > 0:
            profit -= b * costs[t]
        flows[t] = (p, n, f, s, b, max(p - f - s, 0.0))
    target = min((produced.get(t, 0.0) / q for t, q in tops.items()), default=0.0)
    return _Eval(True, profit, target, flows)


def _run_levels(options: Sequence[ChainOption], counts: dict[tuple[int, int], int],
                scales: dict[int, list[float]], buyable: Callable[[int], bool], tier: Callable[[int], int]) -> dict[int, float]:
    """Greedy run levels: factories bottom-up, each limited by the internal
    supply of every input it cannot buy."""
    avail: dict[int, float] = {}
    for oi, sc in scales.items():
        for t, q in options[oi].exports.items():
            avail[t] = avail.get(t, 0.0) + q * sum(sc)
    copies: dict[int, int] = {}
    for (_g, oi), n in counts.items():
        if not options[oi].is_extraction:
            copies[oi] = copies.get(oi, 0) + n
    runs: dict[int, float] = {}
    for oi in sorted(copies, key=lambda i: (tier(options[i].product_type_id), i)):
        o, m = options[oi], copies[oi]
        u = 1.0
        for t, q in o.imports.items():
            if not buyable(t) and q > 0:
                u = min(u, avail.get(t, 0.0) / (m * q))
        run = m * max(0.0, u)
        for t, q in o.imports.items():
            avail[t] = max(0.0, avail.get(t, 0.0) - run * q)
        for t, q in o.exports.items():
            avail[t] = avail.get(t, 0.0) + run * q
        runs[oi] = run
    return runs


def _greedy(options, groups, unlimited, max_rank, penalty, values, costs, buyable, tops, tier) -> _Sol:
    counts: dict[tuple[int, int], int] = {}
    scales: dict[int, list[float]] = {}
    used = [0] * len(groups)
    per_gp: dict[tuple[int, int], int] = {}
    ext_on_planet: dict[int, int] = {}

    def evaluate(cnt, scl):
        runs = _run_levels(options, cnt, scl, buyable, tier)
        sol = _Sol(cnt, {o: r for o, r in runs.items()}, scl)
        return sol, _evaluate(options, sol, values, costs, buyable, tops)

    current = _Eval(True, 0.0, 0.0, {})
    best_sol = _Sol({}, {}, {})
    while True:
        best = None
        for gi, g in enumerate(groups):
            if used[gi] >= g.slots:
                continue
            for oi, o in enumerate(options):
                if o.cc_level != g.cc_level:
                    continue
                if not unlimited and per_gp.get((gi, o.planet_id), 0) >= len(g.members):
                    continue
                cnt = dict(counts)
                cnt[(gi, oi)] = cnt.get((gi, oi), 0) + 1
                scl = {k: list(v) for k, v in scales.items()}
                if o.is_extraction:
                    rank = 1 if unlimited else ext_on_planet.get(o.planet_id, 0) + 1
                    if rank > max_rank:
                        continue
                    scl.setdefault(oi, []).append(penalty ** (rank - 1))
                sol, ev = evaluate(cnt, scl)
                if not ev.feasible:
                    continue
                gain_t, gain_p = ev.target - current.target, ev.profit - current.profit
                if gain_t > _TOL or (gain_t > -_TOL and gain_p > 1e-6):
                    key = (round(ev.target, 6), ev.profit)
                    if best is None or key > best[0]:
                        best = (key, gi, oi, sol, ev)
        if best is None:
            return best_sol
        _k, gi, oi, sol, ev = best
        counts, scales, current, best_sol = dict(sol.counts), {k: list(v) for k, v in sol.scales.items()}, ev, sol
        used[gi] += 1
        o = options[oi]
        per_gp[(gi, o.planet_id)] = per_gp.get((gi, o.planet_id), 0) + 1
        if o.is_extraction:
            ext_on_planet[o.planet_id] = ext_on_planet.get(o.planet_id, 0) + 1


# ------------------------------------------------------------- reporting
def _resized_design(static: StaticData, o: ChainOption, level: float) -> Optional[dict]:
    """A factory colony running below capacity needs fewer factories."""
    if o.design is None or o.is_extraction or level >= 1.0 - 1e-6 or level <= _EPS:
        return o.design
    try:
        d = Design.from_dict(o.design)
        source_tier, _ = CHAINS[d.chain]
        made = frozenset(engine.made_in(static, d))
        per_hour = o.exports.get(o.product_type_id, 0.0) * level / (24.0 * max(o.effective_factor, _EPS))
        counts = engine.factories_for_rate(static, d.product_type_id, source_tier, per_hour, made)
    except (ValueError, KeyError):
        return o.design
    resized = Design(d.chain, d.product_type_id, d.planet_type_id, d.cc_level,
                     tuple(sorted(counts.items())), d.ecus, d.launchpads, d.storages)
    return resized.to_dict()


def _assignments(static, options, groups, sol: _Sol, planet_names) -> list[Assignment]:
    per_copy: dict[int, list[tuple[float, float]]] = {}   # option -> [(run level, yield factor)]
    for oi, sc in sol.scales.items():
        per_copy[oi] = [(1.0, f) for f in sc]
    for oi, run in sol.runs.items():
        copies = sum(n for (_g, o2), n in sol.counts.items() if o2 == oi)
        levels = []
        left = run
        for _ in range(copies):
            lv = max(0.0, min(1.0, left))
            levels.append((lv, 1.0))
            left -= lv
        per_copy[oi] = levels
    out: list[Assignment] = []
    for gi, g in enumerate(groups):
        items = []
        for (g2, oi), n in sorted(sol.counts.items()):
            if g2 != gi:
                continue
            for _ in range(n):
                items.append((options[oi].planet_id, oi, per_copy[oi].pop(0)))
        items.sort(key=lambda it: (it[0], it[1]))
        # Round robin over identical characters: a planet's colonies (at
        # most one per member) land on distinct characters, and every
        # character gets at most ceil(items / members) <= planets colonies.
        for idx, (pid, oi, (level, yf)) in enumerate(items):
            ch = g.members[idx % len(g.members)]
            o = options[oi]
            factor = level * yf
            out.append(Assignment(
                character_key=ch.key, character_name=ch.name, cc_level=ch.cc_level, planet_id=pid,
                planet_name=planet_names.get(pid, str(pid)), option_key=o.key, chain=o.chain,
                product_type_id=o.product_type_id, is_extraction=o.is_extraction,
                design=_resized_design(static, o, level), run_level=level, yield_factor=yf,
                units_per_day=o.exports.get(o.product_type_id, 0.0) * factor,
                exports={t: q * factor for t, q in o.exports.items()},
                imports={t: q * factor for t, q in o.imports.items()},
            ))
    order = {}
    for g in groups:
        for i, m in enumerate(g.members):
            order[m.key] = len(order)
    out.sort(key=lambda a: (order.get(a.character_key, 0), a.planet_id, a.option_key))
    return out


def _purchase_reason(static: StaticData, t: int, makeable: set, options: Sequence[ChainOption],
                     planets: Sequence[Planet], slots_full: bool = False) -> tuple[str, str]:
    name = static.name(t)
    if t in makeable and slots_full:
        return "no_capacity", (f"{name} can be made in this system, but every slot (or every planet per "
                               "character) is already used by the chain")
    if t in makeable:
        return "cheaper", (f"{name} can be made in this system, but buying it is cheaper "
                           "(or frees slots for the stages above)")
    tier = static.tier(t) or 0
    s = static.schematic_by_output.get(t)
    if tier == 1 and s is not None:
        p0 = s.inputs[0][0]
        if not any(p0 in C.PLANET_RESOURCES.get(p.planet_type_id, ()) for p in planets):
            return "no_resource", f"{static.name(p0)} is on no planet of this system"
        return "no_colony", f"No {name} extraction colony fits these Command Center levels and planet sizes"
    if not any(t in o.exports for o in options):
        return "not_here", f"{name} cannot be made on any planet of this system"
    if s is not None:
        missing = [static.name(i) for i, _q in s.inputs if i not in makeable]
        if missing:
            return "missing_inputs", f"{name} needs {', '.join(missing)}, which cannot be made here"
    return "missing_inputs", f"{name} cannot be made here from the system's own materials"


def _validate(static, target, planets, characters, options, penalty, max_extraction_per_planet, time_limit_s):
    if target not in static.commodities or (static.tier(target) or 0) < 1:
        raise ValueError("target must be a P1-P4 commodity")
    if not 0 < penalty <= 1:
        raise ValueError("extraction_repeat_penalty must be in (0, 1]")
    if max_extraction_per_planet < 1:
        raise ValueError("max_extraction_per_planet must be >= 1")
    if not time_limit_s > 0:
        raise ValueError("time_limit_s must be > 0")
    ids = set()
    for p in planets:
        if p.planet_id is None or p.planet_id in ids:
            raise ValueError("planets need unique planet ids")
        ids.add(p.planet_id)
    keys = set()
    for c in characters:
        if c.key in keys:
            raise ValueError(f"duplicate character key {c.key!r}")
        keys.add(c.key)
        if not isinstance(c.planets, int) or not 1 <= c.planets <= MAX_PLANETS_PER_CHARACTER:
            raise ValueError(f"character {c.name!r}: planets must be 1-{MAX_PLANETS_PER_CHARACTER}")
        if c.cc_level not in C.CC_LEVELS:
            raise ValueError(f"character {c.name!r}: CC level must be 0-5")
    okeys = set()
    for o in options:
        if o.key in okeys:
            raise ValueError(f"duplicate option key {o.key!r}")
        okeys.add(o.key)
        if o.planet_id not in ids:
            raise ValueError(f"option {o.key!r} is on an unknown planet")
        if o.chain not in CHAINS:
            raise ValueError(f"option {o.key!r} has an unknown chain")
        for q in list(o.exports.values()) + list(o.imports.values()):
            if q < 0 or not math.isfinite(q):
                raise ValueError(f"option {o.key!r} has a negative or non-finite flow")
        if not (math.isfinite(o.setup_cost_per_day) and math.isfinite(o.customs_cost_per_day)):
            raise ValueError(f"option {o.key!r} has a non-finite cost")
        if o.is_extraction and o.imports:
            raise ValueError(f"extraction option {o.key!r} cannot import")


# ------------------------------------------------------------- public API
def plan_chain(static: StaticData, target: int, planets: Sequence[Planet], characters: Sequence[CharacterSpec],
               options: Sequence[ChainOption], market: Market, allow_buy: bool = True,
               extraction_repeat_penalty: float = DEFAULT_REPEAT_PENALTY,
               max_extraction_per_planet: int = DEFAULT_MAX_EXTRACTION_PER_PLANET,
               unlimited_planets: bool = False, time_limit_s: float = DEFAULT_TIME_LIMIT_S) -> ChainPlan:
    """Which character puts which colony on which planet so that `target`
    is made inside the system as far as possible (see the module docstring).

    `unlimited_planets`: every planet stands for any number of planets of
    its type (the generic view without a system) - no one-CC-per-planet
    limit and no extraction repeat penalty."""
    _validate(static, target, planets, characters, options, extraction_repeat_penalty,
              max_extraction_per_planet, time_limit_s)
    deadline = time.monotonic() + time_limit_s
    planet_names = {p.planet_id: p.name or str(p.planet_id) for p in planets}
    slots = sum(c.planets for c in characters)
    notes: list[str] = []
    target_tier = static.tier(target) or 0
    if target_tier == 4 and not any(static.has_kind(C.KIND_HIGH_TECH, p.planet_type_id) for p in planets):
        notes.append("No Barren or Temperate planet: P4 (High-Tech Production Plant) cannot be made here - "
                     "the plan makes its inputs, to be hauled to a system that can build the top stage.")

    def char_rows(assignments: Sequence[Assignment]) -> list[dict]:
        used: dict[str, int] = {}
        for a in assignments:
            used[a.character_key] = used.get(a.character_key, 0) + 1
        return [{"key": c.key, "name": c.name, "cc_level": c.cc_level, "planets": c.planets,
                 "used": used.get(c.key, 0)} for c in characters]

    def empty(note: str, mode: str = "target", tops: Optional[list] = None) -> ChainPlan:
        return ChainPlan("infeasible", target, mode, tops or [target], 0.0, 0.0, 0.0, 0.0, 0, slots,
                         [], [], [], char_rows([]), notes + [note])

    if not characters:
        return empty("No characters available.")
    groups = _groups(characters)
    levels_present = {g.cc_level for g in groups}
    usable = [o for o in options if o.cc_level in levels_present]
    if not usable:
        return empty("No colony of this chain fits on any planet with these Command Center levels.")

    values: dict[int, Optional[float]] = {}
    costs: dict[int, Optional[float]] = {}
    for t in {t for o in usable for t in list(o.exports) + list(o.imports)}:
        freight = float(market.freight_per_unit.get(t, 0.0))
        values[t] = float(market.sell_value[t]) - freight if t in market.sell_value else None
        costs[t] = float(market.buy_cost[t]) + freight if t in market.buy_cost else None
    makeable = _makeable(usable)

    def strict(t: int) -> bool:
        return costs.get(t) is not None and t not in makeable

    def relaxed(t: int) -> bool:
        return costs.get(t) is not None

    final_policy = relaxed if allow_buy else strict
    n_chars = len(characters)
    max_rank = max(1, min(n_chars, max_extraction_per_planet))
    model = _Model(usable, groups, unlimited_planets, max_rank, extraction_repeat_penalty, values, costs)

    def budget() -> float:
        return max(0.2, min(deadline - time.monotonic(), time_limit_s / 2))

    def tier(t: int) -> int:
        return static.tier(t) or 0

    # ---- phase A: the most target (or target equivalents) the slots can make
    chosen_tops: Optional[dict[int, float]] = None
    phase_a_policy = strict
    t_max = 0.0
    solver_notes: list[str] = []
    for depth, tops in enumerate(_top_levels(static, target)):
        tops_eff = {t: q for t, q in tops.items() if any(t in o.exports for o in usable)}
        if not tops_eff:
            continue
        for policy in ((strict, relaxed) if allow_buy else (strict,)):
            try:
                xv, st = model.solve(tops_eff, policy, "A", None, budget())
            except Exception as exc:  # noqa: BLE001 - solver failure degrades to greedy
                xv, st = None, f"solver error: {exc}"
            if xv is None:
                sol = _greedy(usable, groups, unlimited_planets, max_rank, extraction_repeat_penalty,
                              values, costs, policy, tops_eff, tier)
                tv = _evaluate(usable, sol, values, costs, policy, tops_eff).target
                solver_notes.append(f"Optimiser unavailable ({st}); a greedy estimate was used.")
            else:
                tv = float(xv[model.col_t])
            if tv > _TOL:
                chosen_tops, phase_a_policy, t_max = tops_eff, policy, tv
                break
        if chosen_tops is not None:
            if depth > 0:
                names = ", ".join(static.name(t) for t in sorted(chosen_tops))
                missing = sorted(t for t in tops if t not in chosen_tops)
                notes.append(f"{static.name(target)} cannot be made here; the plan makes its inputs "
                             f"({names}) for a top stage elsewhere."
                             + (f" Not made here either: {', '.join(static.name(t) for t in missing)}."
                                if missing else ""))
            if phase_a_policy is relaxed:
                notes.append("The system cannot make the target from its own materials with these slots; "
                             "the plan buys part of the inputs.")
            break
    if chosen_tops is None:
        return empty("The chain cannot be made in this system with these characters "
                     "(nothing of its tree can be made or bought).")
    mode = "target" if target in chosen_tops else "inputs"

    # ---- phase B: most profitable plan that keeps the target at its maximum
    if phase_a_policy is strict:
        floor = max(0.0, t_max * (1 - 1e-6) - 1e-6)
    else:
        # Everything may be bought, so T is bounded only by the slots: forcing
        # that maximum would build a buy-everything plan however much it
        # loses. Require the output of the smallest top-stage colony instead;
        # profit decides whether to make more.
        unit = min(min((o.exports[t] / q for o in usable if o.exports.get(t, 0.0) > _EPS), default=0.0)
                   for t, q in chosen_tops.items())
        floor = max(0.0, min(t_max, unit) * (1 - 1e-6) - 1e-6)
    policy = final_policy if phase_a_policy is strict else relaxed
    status = "fallback"
    sol: Optional[_Sol] = None
    try:
        xv, st = model.solve(chosen_tops, policy, "B", floor, budget())
        if xv is not None:
            cand = model.to_sol(xv)
            ev = _evaluate(usable, cand, values, costs, policy, chosen_tops)
            if ev.feasible and ev.target >= floor - 1e-4 * max(1.0, t_max):
                sol, status = cand, st
            else:
                st = "solver returned an inconsistent plan"
    except Exception as exc:  # noqa: BLE001
        st = f"solver error: {exc}"
    if sol is None:
        solver_notes.append(f"Optimiser unavailable ({st}); showing a greedy plan instead.")
        sol = _greedy(usable, groups, unlimited_planets, max_rank, extraction_repeat_penalty,
                      values, costs, policy, chosen_tops, tier)
    elif status == "time_limit":
        solver_notes.append(f"Optimiser hit its {time_limit_s:g} s time limit; the plan is the best found, "
                            "not proven optimal.")
    notes.extend(dict.fromkeys(solver_notes))

    ev = _evaluate(usable, sol, values, costs, policy, chosen_tops)
    if not ev.feasible:
        return empty("No feasible plan found.", mode, sorted(chosen_tops))
    assignments = _assignments(static, usable, groups, sol, planet_names)
    used = len(assignments)

    tree = tree_types(static, target)
    colonies_by_product: dict[int, int] = {}
    for a in assignments:
        colonies_by_product[a.product_type_id] = colonies_by_product.get(a.product_type_id, 0) + 1
    stages = [StageSummary(t, tier(t), t in tree, colonies_by_product.get(t, 0), p, n, f, b, s, d)
              for t, (p, n, f, s, b, d) in sorted(ev.flows.items(), key=lambda kv: (-tier(kv[0]), kv[0]))]
    capacity = sum(g.slots if unlimited_planets else min(g.slots, len(g.members) * len(planets)) for g in groups)
    full = used >= capacity
    purchases = []
    for t, (_p, _n, _f, _s, b, _d) in sorted(ev.flows.items(), key=lambda kv: (-tier(kv[0]), kv[0])):
        if b > 0:
            code, why = _purchase_reason(static, t, makeable, usable, planets, full)
            purchases.append(Purchase(t, b, b * float(costs[t] or 0.0), code, why))

    if any(a.yield_factor < 1.0 - _EPS for a in assignments):
        notes.append("Several extraction colonies share a planet; a yield penalty of "
                     f"{extraction_repeat_penalty:g} per extra colony is applied (shared deposits).")
    idle = [a for a in assignments if not a.is_extraction and a.run_level < 1.0 - 1e-6]
    if idle:
        notes.append("Some factory colonies run below capacity (not enough input); their designs are "
                     "resized to the rate they really run at.")
    if used < slots:
        notes.append(f"{used} of {slots} slots used; more colonies would neither add {static.name(target)} "
                     "nor profit (planet, character or Command Center limits).")
    if ev.profit < 0:
        notes.append("This chain loses ISK at current prices; selling at a lower tier may pay more.")
    return ChainPlan(
        status=status, target_type_id=target, mode=mode, top_type_ids=sorted(chosen_tops),
        target_units_per_day=ev.target, max_target_units_per_day=t_max,
        profit_per_day=ev.profit, profit_per_slot=ev.profit / used if used else 0.0,
        used_slots=used, slots=slots, assignments=assignments, stages=stages, purchases=purchases,
        characters=char_rows(assignments), notes=notes,
    )


__all__ = [
    "Assignment", "ChainOption", "ChainPlan", "CharacterSpec", "Purchase", "StageSummary",
    "build_market", "build_options", "candidate_stages", "plan_chain", "tree_types",
]
