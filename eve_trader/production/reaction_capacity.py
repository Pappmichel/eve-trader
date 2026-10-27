"""Reaction-layer capacity solver (docs/REACTION_CAPACITY_PLAN.md phase 1b).

Pure mixed-integer program over **two** reaction windows (D13): W1 is the
next window (executed), W2 is a continuous preview. Intermediates reacted
in W1 can feed composites in W2. Subject to slots, owned formulas, job size
``fit``, and inputs that must be on hand at each window's start. Style
matches ``pi/chain_plan.py`` and ``refining/optimizer.py``
(``scipy.optimize.linprog`` + HiGHS integrality).

Shadow prices come from one LP with integer jobs fixed (duals), not from
probe MIPs. Window helpers read already-synced industry-job / character-slot
tables; they never call ESI. No planner wiring here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import linprog

from .. import storage
from . import jobs as jobs_mod
from .config import ProductionConfig
from .constants import ACTIVITY_SLOT_CATEGORY, SLOT_CATEGORY_LABELS

_EPS = 1e-6
_TOL = 1e-7
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
    inputs: Mapping[int, float]
    outputs: Mapping[int, float]
    fit: int
    owned_formulas: int
    formula_price: Optional[float]


@dataclass(frozen=True)
class ReactionLayer:
    """Every reaction recipe the plan reaches, plus their identity set."""
    recipes: Sequence[ReactionRecipe]

    @property
    def product_ids(self) -> frozenset[int]:
        return frozenset(r.product_type_id for r in self.recipes)

    @property
    def reaction_material_ids(self) -> frozenset[int]:
        return self.product_ids


@dataclass(frozen=True)
class ReactionWindow:
    """Capacities, stocks and demand for the two-window solve (D13/D14)."""
    slots: int                          # slots1 (W1 free slots)
    on_hand: Mapping[int, float]
    demand: Mapping[int, float]         # demand1
    prices: Mapping[int, float]         # missing key = cannot buy
    incoming1: Mapping[int, float] = field(default_factory=dict)
    incoming2: Mapping[int, float] = field(default_factory=dict)
    slots2: Optional[int] = None        # None = same as slots
    demand_factor: float = 1.0          # D14: demand2 = demand1 * factor
    manual_buy: frozenset[int] = frozenset()
    manual_build: frozenset[int] = frozenset()
    # product_type_id -> "buy" | "wait"; missing = buy (D10)
    chain_choice: Mapping[int, str] = field(default_factory=dict)
    formula_payback_cycles: float = 1.0

    @property
    def slots_w2(self) -> int:
        return int(self.slots if self.slots2 is None else self.slots2)


# ------------------------------------------------------------------ output
@dataclass(frozen=True)
class RecipeAllocation:
    recipe_key: str
    product_type_id: int
    jobs: int
    runs: float
    runs_per_job: float
    units: Mapping[int, float]
    formulas_to_buy: int


@dataclass(frozen=True)
class ChainingComparison:
    """D10: buy-now vs wait (defer W1 demand to W2) for one product."""
    product_type_id: int
    buy_now_cost: float
    wait_cost: float
    isk_saved_by_waiting: float
    units_delayed: float
    preferred: str                      # stored choice, else "buy"
    short_input_ids: tuple[int, ...]


@dataclass(frozen=True)
class NextWindowPreview:
    """W2 continuous preview (not rounded, not executed)."""
    recipes: tuple[RecipeAllocation, ...]
    buy: Mapping[int, float]
    jobs_used: float


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
    next_window: Optional[NextWindowPreview] = None
    deferred: Mapping[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class WindowState:
    """Derived free slots and on-hand / incoming split for W1 and W2."""
    total_slots: int
    busy_slots: int
    free_slots: int
    busy_slots_w2: int
    free_slots_w2: int
    on_hand_from_jobs: Mapping[int, float]
    incoming1_from_jobs: Mapping[int, float]
    incoming2_from_jobs: Mapping[int, float]

    @property
    def incoming_from_jobs(self) -> Mapping[int, float]:
        """Alias for W1 incoming (jobs ending after W1, before or after W2)."""
        out: dict[int, float] = dict(self.incoming1_from_jobs)
        for tid, qty in self.incoming2_from_jobs.items():
            out[tid] = out.get(tid, 0.0) + qty
        return out


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
    """(total_reaction_slots, free_right_now) for characters not excluded."""
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
        free = total
    return total, free


def _excluded_installer_names(character_rows: list) -> frozenset[str]:
    label = SLOT_CATEGORY_LABELS["reaction"]
    return frozenset(
        row.character_name for row in character_rows
        if row.excluded_from_planning and row.job_type == label
    )


def window_state(
    cfg: ProductionConfig,
    *,
    character_rows: Optional[list] = None,
    industry_jobs: Optional[list[tuple]] = None,
) -> WindowState:
    """Busy reaction slots and job-output split at W1 / W2 starts.

    Jobs whose installer is excluded from planning never count as busy
    (finding 1.4.5). Without a window date every slot is free (D12).
    """
    rows = character_rows if character_rows is not None else jobs_mod.character_slot_overview()
    total, _free_now = reaction_slot_totals(cfg, character_rows=rows)
    window_dt = _parse_window_dt(cfg.reaction_window_next)
    if window_dt is None:
        return WindowState(
            total_slots=total, busy_slots=0, free_slots=total,
            busy_slots_w2=0, free_slots_w2=total,
            on_hand_from_jobs={}, incoming1_from_jobs={}, incoming2_from_jobs={},
        )

    w2_dt = window_dt + timedelta(days=float(cfg.reaction_window_interval_days))
    excluded = _excluded_installer_names(rows)

    if industry_jobs is None:
        from ..esi_data.access import shared_owner_ids
        char_ids = shared_owner_ids("industry_jobs", "production", "character")
        corp_ids = shared_owner_ids("industry_jobs", "production", "corporation")
        industry_jobs = storage.list_industry_jobs(
            owner_character_ids=char_ids, owner_corporation_ids=corp_ids,
        )

    busy1 = 0
    busy2 = 0
    on_hand: dict[int, float] = {}
    incoming1: dict[int, float] = {}
    incoming2: dict[int, float] = {}
    for (_job_id, activity_id, blueprint_type_id, product_type_id, _name, runs,
         _loc, status, end_date, _start, installer) in industry_jobs:
        if status not in ("active", "paused", "ready"):
            continue
        if ACTIVITY_SLOT_CATEGORY.get(activity_id) != "reaction":
            continue
        if installer and installer in excluded:
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
        # Still running at W1.
        busy1 += 1
        if end_dt is not None and end_dt <= w2_dt:
            if product_type_id is not None and qty:
                incoming1[product_type_id] = incoming1.get(product_type_id, 0.0) + qty
            continue
        # Still running at W2 as well.
        busy2 += 1
        if product_type_id is not None and qty:
            incoming2[product_type_id] = incoming2.get(product_type_id, 0.0) + qty

    return WindowState(
        total_slots=total,
        busy_slots=busy1, free_slots=max(0, total - busy1),
        busy_slots_w2=busy2, free_slots_w2=max(0, total - busy2),
        on_hand_from_jobs=on_hand,
        incoming1_from_jobs=incoming1,
        incoming2_from_jobs=incoming2,
    )


def fit_runs(seconds_per_run: float, cycle_days: float) -> int:
    """Runs that fit in one job of at most ``cycle_days`` days."""
    if seconds_per_run <= 0 or cycle_days <= 0:
        return 0
    return int(math.floor(cycle_days * 86400.0 / seconds_per_run + _TOL))


# ------------------------------------------------------------------ solver
def solve(layer: ReactionLayer, window: ReactionWindow) -> ReactionPlan:
    """Minimise two-window reaction-layer cost. See plan section 3."""
    if not layer.recipes:
        return ReactionPlan(
            recipes=(), buy={}, total_cost=0.0, slot_shadow_price=0.0,
            formula_shadow_prices={}, warnings=(), slots=window.slots,
        )

    # D10: stored "wait" allows defer up to demand1; default / "buy" = 0.
    allow_defer = {
        p for p, choice in window.chain_choice.items() if choice == "wait"
    }
    primary = _solve_mip(layer, window, allow_defer=allow_defer)
    plan = _finalize(layer, window, primary)
    chaining = _chaining_comparisons(layer, window, plan)
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
        next_window=plan.next_window,
        deferred=plan.deferred,
    )


@dataclass
class _RawSolve:
    runs1: dict[str, float]
    runs2: dict[str, float]
    jobs1: dict[str, int]
    jobs2: dict[str, int]
    new_formulas: dict[str, int]
    buy1: dict[int, float]
    buy2: dict[int, float]
    short1: dict[int, float]
    short2: dict[int, float]
    defer: dict[int, float]
    slot_slack1: float
    slot_slack2: float
    objective: float
    success: bool
    message: str
    # Constraint index bookkeeping for dual extraction (filled when needed).
    slot1_row: int = -1
    formula_rows: dict[str, tuple[int, int]] = field(default_factory=dict)
    ineq_marginals: Optional[np.ndarray] = None


def _material_ids(layer: ReactionLayer, window: ReactionWindow) -> list[int]:
    ids: set[int] = (
        set(window.demand) | set(window.on_hand) | set(window.prices)
        | set(window.incoming1) | set(window.incoming2)
    )
    for recipe in layer.recipes:
        ids.update(recipe.inputs)
        ids.update(recipe.outputs)
        ids.add(recipe.product_type_id)
    return sorted(ids)


def _solve_mip(
    layer: ReactionLayer,
    window: ReactionWindow,
    *,
    allow_defer: set[int] | frozenset[int] = frozenset(),
    force_defer: Optional[Mapping[int, float]] = None,
    slots1_override: Optional[int] = None,
    fix_jobs: Optional[tuple[Mapping[str, int], Mapping[str, int]]] = None,
    continuous: bool = False,
) -> _RawSolve:
    """Build and solve the two-window MIP (or its LP relaxation).

    ``force_defer`` sets exact defer values (chaining probe).
    ``fix_jobs`` + ``continuous=True`` is the shadow-price LP.
    """
    recipes = list(layer.recipes)
    n_r = len(recipes)
    materials = _material_ids(layer, window)
    products = sorted(set(window.demand) | set(layer.product_ids))
    n_m = len(materials)
    n_p = len(products)
    mat_index = {m: i for i, m in enumerate(materials)}
    prod_index = {p: i for i, p in enumerate(products)}

    # Layout:
    # runs1[n_r] | runs2[n_r] | jobs1[n_r] | jobs2[n_r] | new_f[n_r]
    # | buy1[n_m] | buy2[n_m] | short1[n_p] | short2[n_p] | defer[n_p]
    # | slack1 | slack2
    i_runs1 = 0
    i_runs2 = n_r
    i_jobs1 = 2 * n_r
    i_jobs2 = 3 * n_r
    i_new = 4 * n_r
    i_buy1 = 5 * n_r
    i_buy2 = i_buy1 + n_m
    i_short1 = i_buy2 + n_m
    i_short2 = i_short1 + n_p
    i_defer = i_short2 + n_p
    i_slack1 = i_defer + n_p
    i_slack2 = i_slack1 + 1
    n_vars = i_slack2 + 1

    slots1 = int(slots1_override) if slots1_override is not None else int(window.slots)
    slots2 = int(window.slots_w2)
    payback = max(window.formula_payback_cycles, _EPS)
    factor = max(0.0, float(window.demand_factor))

    c = np.zeros(n_vars, dtype=float)
    for i, recipe in enumerate(recipes):
        c[i_runs1 + i] = float(recipe.fee_per_run)
        c[i_runs2 + i] = float(recipe.fee_per_run)
        c[i_jobs1 + i] = 1e-3
        c[i_jobs2 + i] = 1e-3
        if recipe.formula_price is not None and recipe.formula_price > 0:
            c[i_new + i] = float(recipe.formula_price) / payback
    for m in materials:
        price = window.prices.get(m)
        if price is not None:
            c[i_buy1 + mat_index[m]] = float(price)
            c[i_buy2 + mat_index[m]] = float(price)
    for p in products:
        c[i_short1 + prod_index[p]] = _SHORT_PENALTY
        c[i_short2 + prod_index[p]] = _SHORT_PENALTY
    c[i_slack1] = _SLOT_SLACK_PENALTY
    c[i_slack2] = _SLOT_SLACK_PENALTY

    bounds: list[tuple[Optional[float], Optional[float]]] = [(0.0, None)] * n_vars
    for i, recipe in enumerate(recipes):
        if recipe.fit <= 0:
            bounds[i_runs1 + i] = (0.0, 0.0)
            bounds[i_runs2 + i] = (0.0, 0.0)
            bounds[i_jobs1 + i] = (0.0, 0.0)
            bounds[i_jobs2 + i] = (0.0, 0.0)
            bounds[i_new + i] = (0.0, 0.0)
            continue
        if recipe.product_type_id in window.manual_buy:
            bounds[i_runs1 + i] = (0.0, 0.0)
            bounds[i_runs2 + i] = (0.0, 0.0)
            bounds[i_jobs1 + i] = (0.0, 0.0)
            bounds[i_jobs2 + i] = (0.0, 0.0)
            bounds[i_new + i] = (0.0, 0.0)
        if recipe.formula_price is None or recipe.formula_price <= 0:
            bounds[i_new + i] = (0.0, 0.0)
    for m in materials:
        if m not in window.prices:
            bounds[i_buy1 + mat_index[m]] = (0.0, 0.0)
            bounds[i_buy2 + mat_index[m]] = (0.0, 0.0)
        if m in window.manual_build:
            bounds[i_buy1 + mat_index[m]] = (0.0, 0.0)
            bounds[i_buy2 + mat_index[m]] = (0.0, 0.0)
    for p in products:
        dem = float(window.demand.get(p, 0.0))
        if force_defer is not None and p in force_defer:
            d = float(force_defer[p])
            bounds[i_defer + prod_index[p]] = (d, d)
        elif p in allow_defer:
            bounds[i_defer + prod_index[p]] = (0.0, dem)
        else:
            bounds[i_defer + prod_index[p]] = (0.0, 0.0)

    if fix_jobs is not None:
        jobs1_fix, jobs2_fix = fix_jobs
        for i, recipe in enumerate(recipes):
            j1 = float(jobs1_fix.get(recipe.key, 0))
            j2 = float(jobs2_fix.get(recipe.key, 0))
            bounds[i_jobs1 + i] = (j1, j1)
            bounds[i_jobs2 + i] = (j2, j2)

    a_rows: list[list[float]] = []
    b_rows: list[float] = []

    def _new_row() -> list[float]:
        return [0.0] * n_vars

    # --- Inputs W1: sum in*runs1 - buy1 <= on_hand
    for m in materials:
        row = _new_row()
        for i, recipe in enumerate(recipes):
            qty = float(recipe.inputs.get(m, 0.0))
            if qty:
                row[i_runs1 + i] = qty
        row[i_buy1 + mat_index[m]] = -1.0
        a_rows.append(row)
        b_rows.append(float(window.on_hand.get(m, 0.0)))

    # --- Coverage W1: S2[p] + short1 >= 0
    # on_hand + buy1 + inc1 + sum(out-in)*runs1 - (demand1 - defer) + short1 >= 0
    # -buy1 - sum(out-in)*runs1 - defer - short1 <= on_hand + inc1 - demand1
    for p in products:
        row = _new_row()
        for i, recipe in enumerate(recipes):
            net = float(recipe.outputs.get(p, 0.0)) - float(recipe.inputs.get(p, 0.0))
            if net:
                row[i_runs1 + i] = -net
        row[i_buy1 + mat_index[p]] = -1.0
        row[i_defer + prod_index[p]] = -1.0
        row[i_short1 + prod_index[p]] = -1.0
        a_rows.append(row)
        b_rows.append(
            float(window.on_hand.get(p, 0.0))
            + float(window.incoming1.get(p, 0.0))
            - float(window.demand.get(p, 0.0))
        )

    # --- Inputs W2: sum in*runs2 <= S2 + buy2
    # sum in*runs2 - on_hand - buy1 - inc1 - sum(out-in)*runs1 + (demand1 - defer) - buy2 <= 0
    # For non-products demand1=defer=0.
    for m in materials:
        row = _new_row()
        for i, recipe in enumerate(recipes):
            in_qty = float(recipe.inputs.get(m, 0.0))
            net = float(recipe.outputs.get(m, 0.0)) - float(recipe.inputs.get(m, 0.0))
            if in_qty:
                row[i_runs2 + i] = in_qty
            if net:
                row[i_runs1 + i] = -net
        row[i_buy1 + mat_index[m]] = -1.0
        row[i_buy2 + mat_index[m]] = -1.0
        if m in prod_index:
            # +(demand1 - defer) on the LHS of <= 0 form:
            # ... + demand1 - defer ... <= on_hand + inc1
            # so -defer on LHS, move demand1 to RHS:
            row[i_defer + prod_index[m]] = -1.0
            rhs = (
                float(window.on_hand.get(m, 0.0))
                + float(window.incoming1.get(m, 0.0))
                - float(window.demand.get(m, 0.0))
            )
        else:
            rhs = (
                float(window.on_hand.get(m, 0.0))
                + float(window.incoming1.get(m, 0.0))
            )
        a_rows.append(row)
        b_rows.append(rhs)

    # --- Coverage W2 (defer cancels after substituting S2):
    # buy1 + buy2 + sum net*runs1 + sum net*runs2 + short2
    #   >= demand2 + demand1 - on_hand - inc1 - inc2
    # As <= :
    # -buy1 -buy2 -sum net*runs1 -sum net*runs2 -short2
    #   <= on_hand + inc1 + inc2 - demand1 - demand2
    for p in products:
        row = _new_row()
        for i, recipe in enumerate(recipes):
            net = float(recipe.outputs.get(p, 0.0)) - float(recipe.inputs.get(p, 0.0))
            if net:
                row[i_runs1 + i] = -net
                row[i_runs2 + i] = -net
        row[i_buy1 + mat_index[p]] = -1.0
        row[i_buy2 + mat_index[p]] = -1.0
        row[i_short2 + prod_index[p]] = -1.0
        demand1 = float(window.demand.get(p, 0.0))
        demand2 = demand1 * factor
        a_rows.append(row)
        b_rows.append(
            float(window.on_hand.get(p, 0.0))
            + float(window.incoming1.get(p, 0.0))
            + float(window.incoming2.get(p, 0.0))
            - demand1 - demand2
        )

    # Job size both windows
    for i, recipe in enumerate(recipes):
        fit = float(max(recipe.fit, 0))
        row = _new_row()
        row[i_runs1 + i] = 1.0
        row[i_jobs1 + i] = -fit
        a_rows.append(row)
        b_rows.append(0.0)
        row = _new_row()
        row[i_runs2 + i] = 1.0
        row[i_jobs2 + i] = -fit
        a_rows.append(row)
        b_rows.append(0.0)

    # Slots W1 / W2
    slot1_row = len(a_rows)
    row = _new_row()
    for i in range(n_r):
        row[i_jobs1 + i] = 1.0
    row[i_slack1] = -1.0
    a_rows.append(row)
    b_rows.append(float(slots1))

    row = _new_row()
    for i in range(n_r):
        row[i_jobs2 + i] = 1.0
    row[i_slack2] = -1.0
    a_rows.append(row)
    b_rows.append(float(slots2))

    # Formulas: jobs[r,w] - new_f[r] <= owned  (both windows)
    formula_rows: dict[str, tuple[int, int]] = {}
    for i, recipe in enumerate(recipes):
        r1 = len(a_rows)
        row = _new_row()
        row[i_jobs1 + i] = 1.0
        row[i_new + i] = -1.0
        a_rows.append(row)
        b_rows.append(float(recipe.owned_formulas))
        r2 = len(a_rows)
        row = _new_row()
        row[i_jobs2 + i] = 1.0
        row[i_new + i] = -1.0
        a_rows.append(row)
        b_rows.append(float(recipe.owned_formulas))
        formula_rows[recipe.key] = (r1, r2)

    a_ub = np.array(a_rows, dtype=float)
    b_ub = np.array(b_rows, dtype=float)

    if continuous or fix_jobs is not None:
        integrality = None
    else:
        integrality = np.zeros(n_vars, dtype=int)
        for i in range(n_r):
            integrality[i_jobs1 + i] = 1
            integrality[i_jobs2 + i] = 1
            integrality[i_new + i] = 1

    kwargs = dict(c=c, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if integrality is not None:
        kwargs["integrality"] = integrality
        kwargs["options"] = {"time_limit": _MIP_TIME_LIMIT_SECONDS}
    result = linprog(**kwargs)

    empty = _RawSolve(
        runs1={r.key: 0.0 for r in recipes}, runs2={r.key: 0.0 for r in recipes},
        jobs1={r.key: 0 for r in recipes}, jobs2={r.key: 0 for r in recipes},
        new_formulas={r.key: 0 for r in recipes},
        buy1={}, buy2={}, short1={}, short2={}, defer={},
        slot_slack1=0.0, slot_slack2=0.0,
        objective=0.0, success=False,
        message=result.message.strip() if result.message else "solve failed",
        slot1_row=slot1_row, formula_rows=formula_rows,
    )
    if result.x is None:
        return empty

    x = result.x

    def _pos_map_mat(offset: int) -> dict[int, float]:
        return {
            m: max(0.0, float(x[offset + mat_index[m]]))
            for m in materials
            if float(x[offset + mat_index[m]]) > _EPS
        }

    def _pos_map_prod(offset: int) -> dict[int, float]:
        return {
            p: max(0.0, float(x[offset + prod_index[p]]))
            for p in products
            if float(x[offset + prod_index[p]]) > _EPS
        }

    marg = None
    ineq = getattr(result, "ineqlin", None)
    if ineq is not None and getattr(ineq, "marginals", None) is not None:
        marg = np.asarray(ineq.marginals, dtype=float)

    return _RawSolve(
        runs1={recipes[i].key: max(0.0, float(x[i_runs1 + i])) for i in range(n_r)},
        runs2={recipes[i].key: max(0.0, float(x[i_runs2 + i])) for i in range(n_r)},
        jobs1={recipes[i].key: max(0, int(round(float(x[i_jobs1 + i])))) for i in range(n_r)},
        jobs2={recipes[i].key: max(0, int(round(float(x[i_jobs2 + i])))) for i in range(n_r)},
        new_formulas={
            recipes[i].key: max(0, int(round(float(x[i_new + i])))) for i in range(n_r)
        },
        buy1=_pos_map_mat(i_buy1),
        buy2=_pos_map_mat(i_buy2),
        short1=_pos_map_prod(i_short1),
        short2=_pos_map_prod(i_short2),
        defer=_pos_map_prod(i_defer),
        slot_slack1=max(0.0, float(x[i_slack1])),
        slot_slack2=max(0.0, float(x[i_slack2])),
        objective=float(result.fun) if result.x is not None else 0.0,
        success=bool(result.success),
        message=result.message.strip() if result.message else "",
        slot1_row=slot1_row,
        formula_rows=formula_rows,
        ineq_marginals=marg,
    )


def _round_w1(
    layer: ReactionLayer,
    window: ReactionWindow,
    raw: _RawSolve,
) -> tuple[dict[str, int], dict[str, int], dict[int, float], list[str]]:
    """Ceil W1 runs; tighten jobs to ceil(runs/fit). Drop excess if no slot."""
    warnings: list[str] = []
    runs_i = {
        k: int(math.ceil(v - _TOL)) if v > _EPS else 0 for k, v in raw.runs1.items()
    }
    buy = dict(raw.buy1)
    by_key = {r.key: r for r in layer.recipes}
    jobs_i: dict[str, int] = {r.key: 0 for r in layer.recipes}
    ideal: dict[str, int] = {}
    for key, runs in runs_i.items():
        recipe = by_key[key]
        if runs <= 0 or recipe.fit <= 0:
            runs_i[key] = 0
            ideal[key] = 0
            continue
        ideal[key] = int(math.ceil(runs / recipe.fit - _TOL))

    order = sorted(
        by_key.keys(),
        key=lambda k: (-raw.jobs1.get(k, 0), -ideal.get(k, 0), k),
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
                f"Rounded W1 runs for {key} exceeded fit x jobs; "
                f"dropped {dropped} run(s) and bought {out_qty:g} units"
            )
    return runs_i, jobs_i, buy, warnings


def _recompute_buy1(
    layer: ReactionLayer,
    window: ReactionWindow,
    runs1: Mapping[str, float],
    defer: Mapping[int, float],
) -> dict[int, float]:
    by_key = {r.key: r for r in layer.recipes}
    produced: dict[int, float] = {}
    consumed: dict[int, float] = {}
    for key, n_runs in runs1.items():
        if n_runs <= 0:
            continue
        recipe = by_key[key]
        for m, qty in recipe.inputs.items():
            consumed[m] = consumed.get(m, 0.0) + float(qty) * n_runs
        for m, qty in recipe.outputs.items():
            produced[m] = produced.get(m, 0.0) + float(qty) * n_runs

    result: dict[int, float] = {}
    materials = set(consumed) | set(window.demand) | set(layer.product_ids)
    for m in materials:
        if m in window.manual_build or m not in window.prices:
            continue
        on_hand = float(window.on_hand.get(m, 0.0))
        need_input = max(0.0, consumed.get(m, 0.0) - on_hand)
        if m in layer.product_ids or m in window.demand:
            dem = float(window.demand.get(m, 0.0)) - float(defer.get(m, 0.0))
            cover_gap = max(
                0.0,
                dem + consumed.get(m, 0.0)
                - produced.get(m, 0.0)
                - on_hand
                - float(window.incoming1.get(m, 0.0)),
            )
            need = max(need_input, cover_gap)
        else:
            need = need_input
        if need > _EPS:
            result[m] = need
    return result


def _objective_of(
    layer: ReactionLayer,
    window: ReactionWindow,
    runs1: Mapping[str, float],
    runs2: Mapping[str, float],
    new_formulas: Mapping[str, int],
    buy1: Mapping[int, float],
    buy2: Mapping[int, float],
    short1: Mapping[int, float],
    short2: Mapping[int, float],
    slot_slack1: float,
    slot_slack2: float,
) -> float:
    payback = max(window.formula_payback_cycles, _EPS)
    by_key = {r.key: r for r in layer.recipes}
    total = 0.0
    for key, n in runs1.items():
        total += by_key[key].fee_per_run * n
    for key, n in runs2.items():
        total += by_key[key].fee_per_run * n
    for key, n_new in new_formulas.items():
        price = by_key[key].formula_price
        if price is not None and n_new:
            total += float(price) * n_new / payback
    for m, qty in buy1.items():
        if m in window.prices:
            total += float(window.prices[m]) * qty
    for m, qty in buy2.items():
        if m in window.prices:
            total += float(window.prices[m]) * qty
    for qty in short1.values():
        total += _SHORT_PENALTY * qty
    for qty in short2.values():
        total += _SHORT_PENALTY * qty
    total += _SLOT_SLACK_PENALTY * (slot_slack1 + slot_slack2)
    return total


def _shadow_prices(
    layer: ReactionLayer, window: ReactionWindow, raw: _RawSolve,
) -> tuple[float, dict[str, float]]:
    """Shadow prices from the continuous LP relaxation (plan section 3).

    One LP gives every formula-row dual; a second LP at ``slots1 + 1``
    gives the slot value as an objective drop (the slot-row dual of an
    LP with jobs fixed at the MIP solution is zero, because those jobs
    cannot rise). No per-recipe probe MIP.

    Formula value is the W1 formula-row dual, or the amortised purchase
    price when the MIP buys that formula (buying was cheaper than the
    dual of one more owned original).
    """
    allow = {p for p, c in window.chain_choice.items() if c == "wait"}
    base_lp = _solve_mip(layer, window, allow_defer=allow, continuous=True)
    more_lp = _solve_mip(
        layer, window, allow_defer=allow, continuous=True,
        slots1_override=int(window.slots) + 1,
    )
    slot_shadow = max(0.0, float(base_lp.objective) - float(more_lp.objective))

    payback = max(window.formula_payback_cycles, _EPS)
    by_key = {r.key: r for r in layer.recipes}
    formula_shadows: dict[str, float] = {}
    marg = base_lp.ineq_marginals
    for key, (r1, r2) in base_lp.formula_rows.items():
        recipe = by_key[key]
        bought = raw.new_formulas.get(key, 0) > 0
        purchase = (
            float(recipe.formula_price) / payback
            if recipe.formula_price is not None and recipe.formula_price > 0
            else None
        )
        dual = 0.0
        if marg is not None:
            d1 = max(0.0, -float(marg[r1])) if 0 <= r1 < len(marg) else 0.0
            d2 = max(0.0, -float(marg[r2])) if 0 <= r2 < len(marg) else 0.0
            dual = d1 if d1 >= d2 else d2
        if bought and purchase is not None:
            formula_shadows[key] = purchase
            continue
        if dual > _EPS:
            if purchase is not None and dual + _EPS < purchase:
                formula_shadows[key] = purchase
            else:
                formula_shadows[key] = dual
    return slot_shadow, formula_shadows


def _postprocess(
    layer: ReactionLayer,
    window: ReactionWindow,
    raw: _RawSolve,
) -> tuple[dict[str, int], dict[str, int], dict[int, float], dict[str, int],
           dict[int, float], float, float, list[str]]:
    """Round W1, rebuild buy1 / new_formulas / unmet; return pieces for cost."""
    runs1, jobs1, buy1, round_warnings = _round_w1(layer, window, raw)
    buy1 = _recompute_buy1(layer, window, runs1, raw.defer)
    new_f: dict[str, int] = {}
    for recipe in layer.recipes:
        need = max(0, jobs1.get(recipe.key, 0) - recipe.owned_formulas)
        need = max(need, max(0, raw.jobs2.get(recipe.key, 0) - recipe.owned_formulas))
        if need > 0 and recipe.formula_price is not None:
            new_f[recipe.key] = need
    jobs_used = sum(jobs1.values())
    slot_slack1 = max(0.0, float(jobs_used - window.slots))
    slot_slack2 = max(0.0, float(sum(raw.jobs2.values()) - window.slots_w2))
    unmet = dict(raw.short1)
    if not raw.success:
        for p, dem in window.demand.items():
            covered = (
                float(window.on_hand.get(p, 0.0))
                + float(window.incoming1.get(p, 0.0))
                + float(buy1.get(p, 0.0))
                + float(raw.defer.get(p, 0.0))
            )
            for recipe in layer.recipes:
                if recipe.product_type_id != p:
                    continue
                covered += float(recipe.outputs.get(p, 0.0)) * float(runs1.get(recipe.key, 0))
            gap = float(dem) - covered
            if gap > _EPS:
                unmet[p] = max(unmet.get(p, 0.0), gap)
    warnings = list(round_warnings)
    if not raw.success and raw.message:
        warnings.append(f"Solver: {raw.message}")
    if slot_slack1 > _EPS or raw.slot_slack1 > _EPS:
        if any(
            r.product_type_id in window.manual_build and jobs1.get(r.key, 0) > 0
            for r in layer.recipes
        ):
            warnings.append("Manual Build exceeds reaction slot capacity in W1")
        else:
            warnings.append("Reaction jobs exceed W1 slot capacity")
    for p, qty in unmet.items():
        warnings.append(f"Unmet W1 demand for type {p}: {qty:g} units")
    return runs1, jobs1, buy1, new_f, unmet, slot_slack1, slot_slack2, warnings


def _finalize(
    layer: ReactionLayer,
    window: ReactionWindow,
    raw: _RawSolve,
) -> ReactionPlan:
    runs1, jobs1, buy1, new_f, unmet, slot_slack1, slot_slack2, warnings = _postprocess(
        layer, window, raw,
    )
    total = _objective_of(
        layer, window, runs1, raw.runs2, new_f, buy1, raw.buy2,
        unmet, raw.short2, slot_slack1, slot_slack2,
    )

    allocations = []
    for recipe in layer.recipes:
        n_jobs = jobs1.get(recipe.key, 0)
        n_runs = float(runs1.get(recipe.key, 0))
        if n_jobs <= 0 and n_runs <= 0 and new_f.get(recipe.key, 0) <= 0:
            continue
        units = {
            m: float(qty) * n_runs for m, qty in recipe.outputs.items() if n_runs
        }
        allocations.append(RecipeAllocation(
            recipe_key=recipe.key,
            product_type_id=recipe.product_type_id,
            jobs=n_jobs,
            runs=n_runs,
            runs_per_job=(n_runs / n_jobs) if n_jobs else 0.0,
            units=units,
            formulas_to_buy=new_f.get(recipe.key, 0),
        ))

    preview_recipes = []
    for recipe in layer.recipes:
        n_jobs = raw.jobs2.get(recipe.key, 0)
        n_runs = float(raw.runs2.get(recipe.key, 0.0))
        if n_jobs <= 0 and n_runs <= _EPS:
            continue
        units = {
            m: float(qty) * n_runs for m, qty in recipe.outputs.items() if n_runs > _EPS
        }
        preview_recipes.append(RecipeAllocation(
            recipe_key=recipe.key,
            product_type_id=recipe.product_type_id,
            jobs=n_jobs,
            runs=n_runs,
            runs_per_job=(n_runs / n_jobs) if n_jobs else 0.0,
            units=units,
            formulas_to_buy=0,
        ))
    next_window = NextWindowPreview(
        recipes=tuple(preview_recipes),
        buy=dict(raw.buy2),
        jobs_used=float(sum(raw.jobs2.values())),
    )

    slot_shadow, formula_shadows = _shadow_prices(layer, window, raw)

    return ReactionPlan(
        recipes=tuple(allocations),
        buy=buy1,
        total_cost=total,
        slot_shadow_price=slot_shadow,
        formula_shadow_prices=formula_shadows,
        warnings=tuple(dict.fromkeys(warnings)),
        unmet_demand=unmet,
        jobs_used=sum(jobs1.values()),
        slots=window.slots,
        next_window=next_window,
        deferred=dict(raw.defer),
    )


def _chaining_comparisons(
    layer: ReactionLayer,
    window: ReactionWindow,
    plan: ReactionPlan,
) -> tuple[ChainingComparison, ...]:
    """D10: for products whose W1 demand needs bought reaction inputs (or
    the product itself is bought and it has reaction-layer inputs), compare
    buy-now against allowing full defer to W2. Leaf products that only take
    moon goo are skipped - waiting cannot free a chaining decision."""
    by_key = {r.key: r for r in layer.recipes}
    bought_reaction = {
        m for m, qty in plan.buy.items()
        if qty > _EPS and m in layer.reaction_material_ids
    }

    def _reaction_inputs(product_id: int) -> set[int]:
        ids: set[int] = set()
        for recipe in layer.recipes:
            if recipe.product_type_id != product_id:
                continue
            for m in recipe.inputs:
                if m in layer.reaction_material_ids:
                    ids.add(m)
        return ids

    affected: dict[int, set[int]] = {}
    for alloc in plan.recipes:
        recipe = by_key[alloc.recipe_key]
        r_inputs = _reaction_inputs(recipe.product_type_id)
        if not r_inputs:
            continue
        short_inputs = {
            m for m in recipe.inputs
            if m in bought_reaction or (
                m in layer.reaction_material_ids
                and float(window.on_hand.get(m, 0.0)) + _EPS
                < float(recipe.inputs[m]) * alloc.runs
                and alloc.runs > 0
            )
        }
        if short_inputs:
            affected.setdefault(recipe.product_type_id, set()).update(short_inputs)

    for product_id, qty in plan.buy.items():
        if qty <= _EPS or product_id not in layer.product_ids:
            continue
        r_inputs = _reaction_inputs(product_id)
        if not r_inputs:
            continue
        affected.setdefault(product_id, set()).update(r_inputs)

    for product_id, demand in window.demand.items():
        if demand <= _EPS or product_id in affected:
            continue
        r_inputs = _reaction_inputs(product_id)
        if not r_inputs:
            continue
        product_recipes = [r for r in layer.recipes if r.product_type_id == product_id]
        recipe = min(product_recipes, key=lambda r: r.fee_per_run)
        out = float(recipe.outputs.get(product_id, 0.0)) or 1.0
        runs_needed = max(
            0.0,
            demand - float(window.on_hand.get(product_id, 0.0))
            - float(window.incoming1.get(product_id, 0.0)),
        ) / out
        short_inputs = {
            m for m, qty in recipe.inputs.items()
            if m in layer.reaction_material_ids
            and float(window.on_hand.get(m, 0.0)) + _EPS < float(qty) * runs_needed
        }
        if short_inputs:
            affected[product_id] = short_inputs

    buy_now_cost = plan.total_cost
    comparisons: list[ChainingComparison] = []
    for product_id, short_ids in sorted(affected.items()):
        # Continuous LP probe: same defer bound as a MIP wait solve, ~100x
        # faster. Needed so a 60-product layer stays under the 5 s target
        # (one MIP per product would be ~2 s each).
        wait_raw = _solve_mip(
            layer, window,
            allow_defer={product_id},
            force_defer={product_id: float(window.demand.get(product_id, 0.0))},
            continuous=True,
        )
        wait_cost = float(wait_raw.objective) if wait_raw.success else buy_now_cost
        units = float(window.demand.get(product_id, 0.0))
        preferred = window.chain_choice.get(product_id, "buy")
        if preferred not in ("buy", "wait"):
            preferred = "buy"
        comparisons.append(ChainingComparison(
            product_type_id=product_id,
            buy_now_cost=buy_now_cost,
            wait_cost=wait_cost,
            isk_saved_by_waiting=buy_now_cost - wait_cost,
            units_delayed=units,
            preferred=preferred,
            short_input_ids=tuple(sorted(short_ids)),
        ))
    return tuple(comparisons)
