"""Reaction-layer capacity solver (docs/REACTION_CAPACITY_PLAN.md phase 1).

Pure mixed-integer program over one reaction window: decide which reaction
jobs to start (subject to slots, owned formulas, job size ``fit``, and
inputs that must be on hand at the window start), which products/inputs to
buy, and which formulas to buy when they pay back within
``reaction_formula_payback_cycles``. No planner wiring here - ``solve``
takes a pre-built ``ReactionLayer`` + ``ReactionWindow`` and returns a
``ReactionPlan``. Style matches ``pi/chain_plan.py`` and
``refining/optimizer.py`` (``scipy.optimize.linprog`` + HiGHS integrality).

Window helpers (busy slots, on-hand vs incoming from running jobs) read the
already-synced industry-job / character-slot tables; they never call ESI.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import linprog

from .. import storage
from . import jobs as jobs_mod
from .config import ProductionConfig
from .constants import ACTIVITY_SLOT_CATEGORY, SLOT_CATEGORY_LABELS

_EPS = 1e-6
_TOL = 1e-7
# Unmet demand / extra slot slack must never look cheaper than buying; any
# real buy price in EVE is well under 1e12 ISK/unit, and a window has at
# most a few thousand slots of slack.
_SHORT_PENALTY = 1e12
_SLOT_SLACK_PENALTY = 1e15
_MIP_TIME_LIMIT_SECONDS = 10.0


# ------------------------------------------------------------------ inputs
@dataclass(frozen=True)
class ReactionRecipe:
    """One reaction recipe (normal or alchemy) the solver may run."""
    key: str
    product_type_id: int
    blueprint_type_id: int
    seconds_per_run: float
    fee_per_run: float
    inputs: Mapping[int, float]       # material_id -> qty per run
    outputs: Mapping[int, float]      # type_id -> qty per run (alchemy: target + byproducts)
    fit: int                          # max runs per job this window
    owned_formulas: int
    formula_price: Optional[float]    # None = cannot buy more formulas


@dataclass(frozen=True)
class ReactionLayer:
    """Every reaction recipe the plan reaches, plus their identity set."""
    recipes: Sequence[ReactionRecipe]

    @property
    def product_ids(self) -> frozenset[int]:
        return frozenset(r.product_type_id for r in self.recipes)

    @property
    def reaction_material_ids(self) -> frozenset[int]:
        """Type ids that are themselves reaction products in this layer
        (usable as intermediates for chaining comparisons)."""
        return self.product_ids


@dataclass(frozen=True)
class ReactionWindow:
    """One planning window's capacities, stocks and demand."""
    slots: int
    on_hand: Mapping[int, float]
    incoming: Mapping[int, float]
    demand: Mapping[int, float]
    prices: Mapping[int, float]                 # missing key = cannot buy
    manual_buy: frozenset[int] = frozenset()
    manual_build: frozenset[int] = frozenset()
    # product_type_id -> "buy" | "wait" (D10); missing = cheaper of the two
    chain_choice: Mapping[int, str] = field(default_factory=dict)
    formula_payback_cycles: float = 1.0


# ------------------------------------------------------------------ output
@dataclass(frozen=True)
class RecipeAllocation:
    recipe_key: str
    product_type_id: int
    jobs: int
    runs: int
    runs_per_job: float
    units: Mapping[int, float]
    formulas_to_buy: int


@dataclass(frozen=True)
class ChainingComparison:
    """D10: buy-missing-inputs now vs wait one cycle, for one product."""
    product_type_id: int
    buy_inputs_cost: float
    wait_cost: float
    preferred: str          # "buy" | "wait"
    short_input_ids: tuple[int, ...]


@dataclass(frozen=True)
class ReactionPlan:
    recipes: tuple[RecipeAllocation, ...]
    buy: Mapping[int, float]
    total_cost: float
    slot_shadow_price: float
    formula_shadow_prices: Mapping[str, float]
    warnings: tuple[str, ...]
    chaining: tuple[ChainingComparison, ...] = ()
    unmet_demand: Mapping[int, float] = field(default_factory=dict)
    jobs_used: int = 0
    slots: int = 0


@dataclass(frozen=True)
class WindowState:
    """Derived free slots and the on-hand / incoming split at the window."""
    total_slots: int
    busy_slots: int
    free_slots: int
    on_hand_from_jobs: Mapping[int, float]   # product_type_id -> qty ready before window
    incoming_from_jobs: Mapping[int, float]  # product_type_id -> qty still running past window


# ------------------------------------------------------------------ window
def _parse_window_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def reaction_slot_totals(
    cfg: ProductionConfig,
    *,
    character_rows: Optional[list] = None,
) -> tuple[int, int]:
    """(total_reaction_slots, free_right_now) for characters not excluded
    from planning. ``character_rows`` is an optional pre-fetched
    ``character_slot_overview()`` result (tests); None reads storage."""
    rows = character_rows if character_rows is not None else jobs_mod.character_slot_overview()
    label = SLOT_CATEGORY_LABELS["reaction"]
    total = 0
    free = 0
    for row in rows:
        if row.excluded_from_planning or row.job_type != label:
            continue
        total += int(row.total_slots)
        free += int(row.free_slots)
    if cfg.reaction_slot_override is not None:
        total = int(cfg.reaction_slot_override)
        # Override replaces the derived total; free-right-now is not used
        # when a window date is set (busy is recomputed below). Without a
        # window date D12 says every slot counts as free.
        free = total
    return total, free


def window_state(
    cfg: ProductionConfig,
    *,
    character_rows: Optional[list] = None,
    industry_jobs: Optional[list[tuple]] = None,
) -> WindowState:
    """Busy reaction slots and job-output split at ``reaction_window_next``.

    Reads already-synced industry jobs (activity_id = Reaction). Without a
    window date every slot is free and no job output is pre-credited to
    on_hand (D12). A job ending at or before the window start counts as
    on_hand; one ending after blocks its slot and its output is incoming.
    """
    total, _free_now = reaction_slot_totals(cfg, character_rows=character_rows)
    window_dt = _parse_window_dt(cfg.reaction_window_next)
    if window_dt is None:
        # D12: no date -> all slots free, no on-hand credit from jobs.
        return WindowState(
            total_slots=total, busy_slots=0, free_slots=total,
            on_hand_from_jobs={}, incoming_from_jobs={},
        )

    if industry_jobs is None:
        from ..esi_data.access import shared_owner_ids
        char_ids = shared_owner_ids("industry_jobs", "production", "character")
        corp_ids = shared_owner_ids("industry_jobs", "production", "corporation")
        industry_jobs = storage.list_industry_jobs(
            owner_character_ids=char_ids, owner_corporation_ids=corp_ids,
        )

    busy = 0
    on_hand: dict[int, float] = {}
    incoming: dict[int, float] = {}
    for (_job_id, activity_id, blueprint_type_id, product_type_id, _name, runs,
         _loc, status, end_date, _start, _installer) in industry_jobs:
        if status not in ("active", "paused", "ready"):
            continue
        if ACTIVITY_SLOT_CATEGORY.get(activity_id) != "reaction":
            continue
        end_dt = _parse_window_dt(end_date)
        qty = 0.0
        if product_type_id is not None and runs:
            per_run = storage.get_product_quantity(
                blueprint_type_id, activity_id, product_type_id)
            if per_run is not None:
                qty = float(per_run) * float(runs)
        if end_dt is not None and end_dt <= window_dt:
            if product_type_id is not None and qty:
                on_hand[product_type_id] = on_hand.get(product_type_id, 0.0) + qty
            continue
        # Still running at the window -> occupies a slot; output is incoming.
        busy += 1
        if product_type_id is not None and qty:
            incoming[product_type_id] = incoming.get(product_type_id, 0.0) + qty

    free = max(0, total - busy)
    return WindowState(
        total_slots=total, busy_slots=busy, free_slots=free,
        on_hand_from_jobs=on_hand, incoming_from_jobs=incoming,
    )


def fit_runs(seconds_per_run: float, cycle_days: float) -> int:
    """Runs that fit in one job of at most ``cycle_days`` days."""
    if seconds_per_run <= 0 or cycle_days <= 0:
        return 0
    return int(math.floor(cycle_days * 86400.0 / seconds_per_run + _TOL))


# ------------------------------------------------------------------ solver
def solve(layer: ReactionLayer, window: ReactionWindow) -> ReactionPlan:
    """Minimise reaction-layer cost for one window. See plan section 3."""
    if not layer.recipes:
        return ReactionPlan(
            recipes=(), buy={}, total_cost=0.0, slot_shadow_price=0.0,
            formula_shadow_prices={}, warnings=(), slots=window.slots,
        )

    # Apply stored chain choices as fixed buy=0 bounds before the primary solve.
    forced_zero_buy: set[int] = set()
    for product_id, choice in window.chain_choice.items():
        if choice != "wait":
            continue
        for recipe in layer.recipes:
            if recipe.product_type_id != product_id:
                continue
            for material_id in recipe.inputs:
                if material_id in layer.reaction_material_ids:
                    forced_zero_buy.add(material_id)

    primary = _solve_mip(layer, window, forced_zero_buy=forced_zero_buy)
    plan = _finalize(layer, window, primary)

    chaining = _chaining_comparisons(layer, window, plan, primary)
    return ReactionPlan(
        recipes=plan.recipes,
        buy=plan.buy,
        total_cost=plan.total_cost,
        slot_shadow_price=plan.slot_shadow_price,
        formula_shadow_prices=plan.formula_shadow_prices,
        warnings=plan.warnings,
        chaining=chaining,
        unmet_demand=plan.unmet_demand,
        jobs_used=plan.jobs_used,
        slots=plan.slots,
    )


@dataclass
class _RawSolve:
    runs: dict[str, float]
    jobs: dict[str, int]
    new_formulas: dict[str, int]
    buy: dict[int, float]
    short: dict[int, float]
    slot_slack: float
    objective: float
    success: bool
    message: str


def _material_ids(layer: ReactionLayer, window: ReactionWindow) -> list[int]:
    ids: set[int] = set(window.demand) | set(window.on_hand) | set(window.incoming) | set(window.prices)
    for recipe in layer.recipes:
        ids.update(recipe.inputs)
        ids.update(recipe.outputs)
        ids.add(recipe.product_type_id)
    return sorted(ids)


def _solve_mip(
    layer: ReactionLayer,
    window: ReactionWindow,
    *,
    forced_zero_buy: set[int] | frozenset[int] = frozenset(),
    slots_override: Optional[int] = None,
    extra_formula: Optional[str] = None,
) -> _RawSolve:
    recipes = list(layer.recipes)
    n_r = len(recipes)
    materials = _material_ids(layer, window)
    products = sorted(set(window.demand) | set(layer.product_ids))
    # Variable layout: runs[0..n_r) | jobs[0..n_r) | new_f[0..n_r) | buy[m] | short[p] | slot_slack
    n_m = len(materials)
    n_p = len(products)
    mat_index = {m: i for i, m in enumerate(materials)}
    prod_index = {p: i for i, p in enumerate(products)}
    i_runs = 0
    i_jobs = n_r
    i_new = 2 * n_r
    i_buy = 3 * n_r
    i_short = i_buy + n_m
    i_slack = i_short + n_p
    n_vars = i_slack + 1

    slots = int(slots_override) if slots_override is not None else int(window.slots)
    payback = max(window.formula_payback_cycles, _EPS)

    c = np.zeros(n_vars, dtype=float)
    for i, recipe in enumerate(recipes):
        c[i_runs + i] = float(recipe.fee_per_run)
        # Tiny per-job cost so HiGHS prefers the minimum jobs that cover
        # runs (the real objective has no job term; post-rounding also
        # tightens). 1e-3 ISK cannot flip a buy-vs-build decision.
        c[i_jobs + i] = 1e-3
        price = recipe.formula_price
        if price is not None and price > 0:
            c[i_new + i] = float(price) / payback
    for m in materials:
        price = window.prices.get(m)
        if price is not None:
            c[i_buy + mat_index[m]] = float(price)
    for p in products:
        c[i_short + prod_index[p]] = _SHORT_PENALTY
    c[i_slack] = _SLOT_SLACK_PENALTY

    bounds: list[tuple[Optional[float], Optional[float]]] = [(0, None)] * n_vars
    for i, recipe in enumerate(recipes):
        if recipe.fit <= 0:
            bounds[i_runs + i] = (0, 0)
            bounds[i_jobs + i] = (0, 0)
            bounds[i_new + i] = (0, 0)
            continue
        if recipe.product_type_id in window.manual_buy:
            bounds[i_runs + i] = (0, 0)
            bounds[i_jobs + i] = (0, 0)
            bounds[i_new + i] = (0, 0)
        if recipe.formula_price is None or recipe.formula_price <= 0:
            bounds[i_new + i] = (0, 0)
    for m in materials:
        if m not in window.prices or window.prices[m] is None:
            bounds[i_buy + mat_index[m]] = (0, 0)
        if m in forced_zero_buy:
            bounds[i_buy + mat_index[m]] = (0, 0)
        if m in window.manual_build:
            # Manual Build never buys the product itself; inputs may still be bought.
            bounds[i_buy + mat_index[m]] = (0, 0)

    a_rows: list[list[float]] = []
    b_rows: list[float] = []

    # Input availability at the window (no chaining from this window's output).
    for m in materials:
        row = [0.0] * n_vars
        for i, recipe in enumerate(recipes):
            qty = float(recipe.inputs.get(m, 0.0))
            if qty:
                row[i_runs + i] = qty
        row[i_buy + mat_index[m]] = -1.0
        a_rows.append(row)
        b_rows.append(float(window.on_hand.get(m, 0.0)))

    # Coverage per reaction product.
    for p in products:
        row = [0.0] * n_vars
        for i, recipe in enumerate(recipes):
            out_qty = float(recipe.outputs.get(p, 0.0))
            in_qty = float(recipe.inputs.get(p, 0.0))
            # out * runs - in * runs + buy + short >= demand - on_hand - incoming
            # => (-out + in) * runs - buy - short <= -(demand - on_hand - incoming)
            row[i_runs + i] = in_qty - out_qty
        row[i_buy + mat_index[p]] = -1.0
        row[i_short + prod_index[p]] = -1.0
        a_rows.append(row)
        need = (float(window.demand.get(p, 0.0))
                - float(window.on_hand.get(p, 0.0))
                - float(window.incoming.get(p, 0.0)))
        b_rows.append(-need)

    # Job size: runs[r] - fit[r] * jobs[r] <= 0
    for i, recipe in enumerate(recipes):
        row = [0.0] * n_vars
        row[i_runs + i] = 1.0
        row[i_jobs + i] = -float(max(recipe.fit, 0))
        a_rows.append(row)
        b_rows.append(0.0)

    # Slots: sum jobs - slack <= slots
    row = [0.0] * n_vars
    for i in range(n_r):
        row[i_jobs + i] = 1.0
    row[i_slack] = -1.0
    a_rows.append(row)
    b_rows.append(float(slots))

    # Formulas: jobs[r] - new_formulas[r] <= owned (+1 when probing shadow)
    for i, recipe in enumerate(recipes):
        row = [0.0] * n_vars
        row[i_jobs + i] = 1.0
        row[i_new + i] = -1.0
        a_rows.append(row)
        owned = int(recipe.owned_formulas)
        if extra_formula == recipe.key:
            owned += 1
        b_rows.append(float(owned))

    a_ub = np.array(a_rows, dtype=float)
    b_ub = np.array(b_rows, dtype=float)
    integrality = np.zeros(n_vars, dtype=int)
    for i in range(n_r):
        integrality[i_jobs + i] = 1
        integrality[i_new + i] = 1

    result = linprog(
        c, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs",
        integrality=integrality, options={"time_limit": _MIP_TIME_LIMIT_SECONDS},
    )
    if result.x is None:
        # Absolute fallback: buy everything, no jobs.
        buy = {p: max(0.0, float(window.demand.get(p, 0.0))
                      - float(window.on_hand.get(p, 0.0))
                      - float(window.incoming.get(p, 0.0)))
               for p in products}
        obj = sum(buy[p] * float(window.prices.get(p, _SHORT_PENALTY)) for p in products)
        return _RawSolve(
            runs={r.key: 0.0 for r in recipes},
            jobs={r.key: 0 for r in recipes},
            new_formulas={r.key: 0 for r in recipes},
            buy=buy, short={}, slot_slack=0.0,
            objective=obj, success=False,
            message=result.message.strip() if result.message else "solve failed",
        )

    x = result.x
    runs = {recipes[i].key: max(0.0, float(x[i_runs + i])) for i in range(n_r)}
    jobs = {recipes[i].key: max(0, int(round(float(x[i_jobs + i])))) for i in range(n_r)}
    new_f = {recipes[i].key: max(0, int(round(float(x[i_new + i])))) for i in range(n_r)}
    buy = {m: max(0.0, float(x[i_buy + mat_index[m]])) for m in materials
           if float(x[i_buy + mat_index[m]]) > _EPS}
    short = {p: max(0.0, float(x[i_short + prod_index[p]])) for p in products
             if float(x[i_short + prod_index[p]]) > _EPS}
    return _RawSolve(
        runs=runs, jobs=jobs, new_formulas=new_f, buy=buy, short=short,
        slot_slack=max(0.0, float(x[i_slack])),
        objective=float(result.fun) if result.success or result.x is not None else 0.0,
        success=bool(result.success),
        message=result.message.strip() if result.message else "",
    )


def _round_runs(
    layer: ReactionLayer,
    window: ReactionWindow,
    raw: _RawSolve,
) -> tuple[dict[str, int], dict[str, int], dict[int, float], list[str]]:
    """Ceil runs per recipe; set jobs to ceil(runs / fit). Grow by one only
    when a slot (and formula) is free, otherwise drop the excess run and buy
    the missing units. Jobs are tightened down from the MIP's value - the
    objective has no per-job cost, so HiGHS may leave jobs at the slot cap."""
    warnings: list[str] = []
    runs_i = {k: int(math.ceil(v - _TOL)) if v > _EPS else 0 for k, v in raw.runs.items()}
    buy = dict(raw.buy)
    by_key = {r.key: r for r in layer.recipes}
    jobs_i: dict[str, int] = {r.key: 0 for r in layer.recipes}

    # First pass: ideal job count from rounded runs (ignore slot/formula caps).
    ideal: dict[str, int] = {}
    for key, runs in runs_i.items():
        recipe = by_key[key]
        if runs <= 0 or recipe.fit <= 0:
            runs_i[key] = 0
            ideal[key] = 0
            continue
        ideal[key] = int(math.ceil(runs / recipe.fit - _TOL))

    # Assign jobs under the global slot cap, preferring recipes the MIP
    # already scheduled (higher raw.jobs first) so capacity stays with the
    # cost-optimal set.
    order = sorted(
        by_key.keys(),
        key=lambda k: (-raw.jobs.get(k, 0), -ideal.get(k, 0), k),
    )
    slots_left = int(window.slots)
    for key in order:
        recipe = by_key[key]
        need = ideal.get(key, 0)
        if need <= 0:
            continue
        formula_cap = int(recipe.owned_formulas) + int(raw.new_formulas.get(key, 0))
        take = min(need, slots_left, formula_cap)
        jobs_i[key] = take
        slots_left -= take
        cap_runs = take * recipe.fit
        runs = runs_i.get(key, 0)
        if runs > cap_runs:
            dropped = runs - cap_runs
            runs_i[key] = cap_runs
            out_qty = float(recipe.outputs.get(recipe.product_type_id, 0.0)) * dropped
            if out_qty > _EPS:
                buy[recipe.product_type_id] = buy.get(recipe.product_type_id, 0.0) + out_qty
            warnings.append(
                f"Rounded runs for {key} exceeded fit x jobs; "
                f"dropped {dropped} run(s) and bought {out_qty:g} units"
            )
    return runs_i, jobs_i, buy, warnings


def _recompute_buy_after_runs(
    layer: ReactionLayer,
    window: ReactionWindow,
    runs: Mapping[str, int],
    buy: dict[int, float],
) -> dict[int, float]:
    """Rebuild buy quantities from rounded runs so input and coverage hold."""
    by_key = {r.key: r for r in layer.recipes}
    produced: dict[int, float] = {}
    consumed: dict[int, float] = {}
    for key, n_runs in runs.items():
        if n_runs <= 0:
            continue
        recipe = by_key[key]
        for m, qty in recipe.inputs.items():
            consumed[m] = consumed.get(m, 0.0) + float(qty) * n_runs
        for m, qty in recipe.outputs.items():
            produced[m] = produced.get(m, 0.0) + float(qty) * n_runs

    result: dict[int, float] = {}
    materials = set(consumed) | set(window.demand) | set(layer.product_ids) | set(buy)
    for m in materials:
        if m in window.manual_build or m not in window.prices:
            continue
        on_hand = float(window.on_hand.get(m, 0.0))
        incoming = float(window.incoming.get(m, 0.0))
        demand = float(window.demand.get(m, 0.0))
        need_input = max(0.0, consumed.get(m, 0.0) - on_hand)
        cover_gap = max(
            0.0,
            demand + consumed.get(m, 0.0)
            - produced.get(m, 0.0) - on_hand - incoming,
        )
        if m in layer.product_ids or m in window.demand:
            need = max(need_input, cover_gap)
        else:
            need = need_input
        if need > _EPS:
            result[m] = need
    return result


def _objective_of(
    layer: ReactionLayer,
    window: ReactionWindow,
    runs: Mapping[str, int],
    jobs: Mapping[str, int],
    new_formulas: Mapping[str, int],
    buy: Mapping[int, float],
    short: Mapping[int, float],
    slot_slack: float,
) -> float:
    payback = max(window.formula_payback_cycles, _EPS)
    by_key = {r.key: r for r in layer.recipes}
    total = 0.0
    for key, n_runs in runs.items():
        total += by_key[key].fee_per_run * n_runs
    for key, n_new in new_formulas.items():
        price = by_key[key].formula_price
        if price is not None and n_new:
            total += float(price) * n_new / payback
    for m, qty in buy.items():
        price = window.prices.get(m)
        if price is not None:
            total += float(price) * qty
    for qty in short.values():
        total += _SHORT_PENALTY * qty
    total += _SLOT_SLACK_PENALTY * slot_slack
    return total


def _unmet_after(
    layer: ReactionLayer,
    window: ReactionWindow,
    runs: Mapping[str, int],
    buy: Mapping[int, float],
) -> dict[int, float]:
    by_key = {r.key: r for r in layer.recipes}
    produced: dict[int, float] = {}
    consumed: dict[int, float] = {}
    for key, n_runs in runs.items():
        if n_runs <= 0:
            continue
        recipe = by_key[key]
        for m, qty in recipe.inputs.items():
            consumed[m] = consumed.get(m, 0.0) + float(qty) * n_runs
        for m, qty in recipe.outputs.items():
            produced[m] = produced.get(m, 0.0) + float(qty) * n_runs
    unmet: dict[int, float] = {}
    for p in set(window.demand) | set(layer.product_ids):
        have = (produced.get(p, 0.0)
                + float(window.on_hand.get(p, 0.0))
                + float(window.incoming.get(p, 0.0))
                + float(buy.get(p, 0.0)))
        need = float(window.demand.get(p, 0.0)) + consumed.get(p, 0.0)
        gap = need - have
        if gap > _EPS:
            unmet[p] = gap
    return unmet


def _finalize(
    layer: ReactionLayer,
    window: ReactionWindow,
    raw: _RawSolve,
) -> ReactionPlan:
    runs_i, jobs_i, buy, round_warnings = _round_runs(layer, window, raw)
    buy = _recompute_buy_after_runs(layer, window, runs_i, buy)
    unmet = _unmet_after(layer, window, runs_i, buy)
    # Anything still unmet after buy rebuild counts as short (no price / manual build).
    short = dict(unmet)

    slot_slack = 0.0
    jobs_used = sum(jobs_i.values())
    if jobs_used > window.slots:
        slot_slack = float(jobs_used - window.slots)

    warnings: list[str] = list(round_warnings)
    if not raw.success and raw.message:
        warnings.append(f"Solver: {raw.message}")
    if slot_slack > _EPS or raw.slot_slack > _EPS:
        for recipe in layer.recipes:
            if recipe.product_type_id in window.manual_build and jobs_i.get(recipe.key, 0) > 0:
                warnings.append(
                    f"Manual Build of type {recipe.product_type_id} exceeds reaction slot capacity"
                )
                break
        else:
            if slot_slack > _EPS:
                warnings.append("Reaction jobs exceed slot capacity")
    for p, qty in unmet.items():
        warnings.append(f"Unmet demand for type {p}: {qty:g} units")

    new_f = {k: v for k, v in raw.new_formulas.items() if v > 0}
    # Drop formula purchases for recipes that ended up with 0 jobs after rounding.
    new_f = {k: min(v, max(0, jobs_i.get(k, 0) - next(
        (r.owned_formulas for r in layer.recipes if r.key == k), 0
    ))) for k, v in new_f.items()}
    new_f = {k: v for k, v in new_f.items() if v > 0}

    total = _objective_of(layer, window, runs_i, jobs_i, new_f, buy, short, slot_slack)

    allocations = []
    by_key = {r.key: r for r in layer.recipes}
    for recipe in layer.recipes:
        n_jobs = jobs_i.get(recipe.key, 0)
        n_runs = runs_i.get(recipe.key, 0)
        if n_jobs <= 0 and n_runs <= 0 and new_f.get(recipe.key, 0) <= 0:
            continue
        units = {m: float(qty) * n_runs for m, qty in recipe.outputs.items() if n_runs}
        allocations.append(RecipeAllocation(
            recipe_key=recipe.key,
            product_type_id=recipe.product_type_id,
            jobs=n_jobs,
            runs=n_runs,
            runs_per_job=(n_runs / n_jobs) if n_jobs else 0.0,
            units=units,
            formulas_to_buy=new_f.get(recipe.key, 0),
        ))

    slot_shadow = _slot_shadow_price(layer, window, total)
    formula_shadows = _formula_shadow_prices(layer, window, total)

    return ReactionPlan(
        recipes=tuple(allocations),
        buy=buy,
        total_cost=total,
        slot_shadow_price=slot_shadow,
        formula_shadow_prices=formula_shadows,
        warnings=tuple(dict.fromkeys(warnings)),  # stable unique
        unmet_demand=unmet,
        jobs_used=jobs_used,
        slots=window.slots,
    )


def _slot_shadow_price(
    layer: ReactionLayer, window: ReactionWindow, base_cost: float,
) -> float:
    """ISK one more slot would save this window (finite difference)."""
    probe = _solve_mip(layer, window, slots_override=window.slots + 1)
    plan = _finalize_cost_only(layer, window, probe, slots=window.slots + 1)
    return max(0.0, base_cost - plan)


def _formula_shadow_prices(
    layer: ReactionLayer, window: ReactionWindow, base_cost: float,
) -> dict[str, float]:
    shadows: dict[str, float] = {}
    for recipe in layer.recipes:
        if recipe.fit <= 0:
            continue
        probe = _solve_mip(layer, window, extra_formula=recipe.key)
        cost = _finalize_cost_only(layer, window, probe)
        save = base_cost - cost
        if save > _EPS:
            shadows[recipe.key] = save
    return shadows


def _finalize_cost_only(
    layer: ReactionLayer, window: ReactionWindow, raw: _RawSolve,
    *, slots: Optional[int] = None,
) -> float:
    slot_cap = window.slots if slots is None else slots
    # Round against the probed slot count so an extra-slot probe can keep
    # the extra job the MIP scheduled.
    probed = ReactionWindow(
        slots=slot_cap, on_hand=window.on_hand, incoming=window.incoming,
        demand=window.demand, prices=window.prices,
        manual_buy=window.manual_buy, manual_build=window.manual_build,
        chain_choice=window.chain_choice,
        formula_payback_cycles=window.formula_payback_cycles,
    )
    runs_i, jobs_i, buy, _ = _round_runs(layer, probed, raw)
    buy = _recompute_buy_after_runs(layer, window, runs_i, buy)
    unmet = _unmet_after(layer, window, runs_i, buy)
    slot_slack = max(0.0, float(sum(jobs_i.values()) - slot_cap))
    new_f = {k: v for k, v in raw.new_formulas.items() if v > 0}
    return _objective_of(layer, window, runs_i, jobs_i, new_f, buy, unmet, slot_slack)


def _chaining_comparisons(
    layer: ReactionLayer,
    window: ReactionWindow,
    plan: ReactionPlan,
    primary: _RawSolve,
) -> tuple[ChainingComparison, ...]:
    """D10: for each product whose reaction inputs were bought, compare
    buy-inputs-now vs wait (those buys fixed to 0)."""
    # Which reaction-layer inputs did the primary solve buy?
    bought_reaction_inputs = {
        m for m, qty in plan.buy.items()
        if qty > _EPS and m in layer.reaction_material_ids
    }
    if not bought_reaction_inputs:
        # Also treat a tight input constraint with zero buy as "short" when
        # on_hand alone couldn't feed a full build - detected via recipes
        # that consume a reaction material with on_hand below what runs used.
        pass

    affected: dict[int, set[int]] = {}
    by_key = {r.key: r for r in layer.recipes}
    for alloc in plan.recipes:
        recipe = by_key[alloc.recipe_key]
        short_inputs = {
            m for m in recipe.inputs
            if m in layer.reaction_material_ids and (
                plan.buy.get(m, 0.0) > _EPS
                or (float(window.on_hand.get(m, 0.0)) + _EPS
                    < float(recipe.inputs[m]) * alloc.runs
                    and alloc.runs > 0)
            )
        }
        # Binding buy of a reaction input that this product consumes.
        short_inputs |= {
            m for m in recipe.inputs
            if m in bought_reaction_inputs
        }
        if short_inputs:
            affected.setdefault(recipe.product_type_id, set()).update(short_inputs)

    # Products whose demand would need reaction inputs beyond on_hand even
    # before the solve - covers the "inputs are short at the window" case
    # when the primary solve already chose to buy the *product* instead.
    for product_id, demand in window.demand.items():
        if demand <= _EPS or product_id in affected:
            continue
        product_recipes = [r for r in layer.recipes if r.product_type_id == product_id]
        if not product_recipes:
            continue
        # Approximate full-build input need from the cheapest-fee recipe's ratios.
        recipe = min(product_recipes, key=lambda r: r.fee_per_run)
        out = float(recipe.outputs.get(product_id, 0.0)) or 1.0
        runs_needed = max(0.0, demand - float(window.on_hand.get(product_id, 0.0))
                          - float(window.incoming.get(product_id, 0.0))) / out
        short_inputs = set()
        for m, qty in recipe.inputs.items():
            if m not in layer.reaction_material_ids:
                continue
            if float(window.on_hand.get(m, 0.0)) + _EPS < float(qty) * runs_needed:
                short_inputs.add(m)
        if short_inputs:
            affected[product_id] = short_inputs

    comparisons: list[ChainingComparison] = []
    buy_cost = plan.total_cost
    for product_id, short_ids in sorted(affected.items()):
        wait_raw = _solve_mip(layer, window, forced_zero_buy=set(short_ids))
        wait_cost = _finalize_cost_only(layer, window, wait_raw)
        choice = window.chain_choice.get(product_id)
        if choice in ("buy", "wait"):
            preferred = choice
        else:
            preferred = "buy" if buy_cost <= wait_cost + _EPS else "wait"
        comparisons.append(ChainingComparison(
            product_type_id=product_id,
            buy_inputs_cost=buy_cost,
            wait_cost=wait_cost,
            preferred=preferred,
            short_input_ids=tuple(sorted(short_ids)),
        ))
    return tuple(comparisons)
