"""How many of one item can be built right now from what is on hand.

Answers "how many Sacrileges can I start today, and what stops the next one".
The item gets top priority: stock that the Build List or a special order would
reserve for something else counts as free here. Nothing is bought.

Rules (confirmed with the user 2026-11-03):
- Stock is physically on hand only (`engine._stock_on_hand`): same corp-wide
  scope and hangar filters as the planners, no output of running jobs.
- `full` mode builds a missing component or reaction product from owned inputs
  when a blueprint for it is owned. `direct` mode counts only the item's own
  direct inputs; a missing component is a bottleneck even if its inputs exist.
- Blueprints limit the result. An owned original (BPO or reaction formula) is
  unlimited. Otherwise only owned copies count: each copy is its own job with
  its own ME and its own material rounding, best ME first. No blueprint owned
  means the item cannot be built.
- Normal recipes only (no alchemy), no overbuild buffer, no buy-vs-build
  decision and no manual Build/Buy override, since nothing is bought.

Material math goes through the planner's own helpers (`_material_qty`,
`_activity_mods`, structure/rig multipliers) so the numbers match the Build
List. Called through the `engine` module so tests can monkeypatch it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .. import storage
from . import engine
from .config import ProductionConfig

MODES = ("full", "direct")
MAX_SEARCH_QUANTITY = 1_000_000


@dataclass
class _Recipe:
    """How one product type can be built with what is owned right now."""
    blueprint_id: int
    activity_id: int
    product_qty: float
    activity: str
    # "original", "copies" or "none" (recipe exists, no blueprint owned)
    source: str
    unlimited_mult: float = 1.0
    # Planner's assumed multiplier, used to list the materials of runs no
    # owned blueprint covers (top item only) and for the material-only maximum.
    assumed_mult: float = 1.0
    # (material_multiplier, runs_per_copy, copy_count), best ME first
    copies: list[tuple[float, int, int]] = field(default_factory=list)

    @property
    def owned(self) -> bool:
        return self.source != "none"

    @property
    def copy_runs(self) -> int:
        return sum(runs * count for _mult, runs, count in self.copies)


class _Context:
    """Per-request memo of stock, recipes and names - every search step reuses it."""

    def __init__(self, cfg: ProductionConfig, mode: str):
        self.cfg = cfg
        self.mode = mode
        self.manual_stock = storage.load_manual_stock()
        self.cost_indices: engine.CostIndices = {
            f"me_te_override:{type_id}": (me, te)
            for type_id, _name, me, te in storage.load_manual_blueprint_me_te_overrides()
        }
        self._stock: dict[int, float] = {}
        self._recipes: dict[int, Optional[_Recipe]] = {}

    def stock(self, type_id: int) -> float:
        if type_id not in self._stock:
            self._stock[type_id] = float(engine._stock_on_hand(type_id, self.manual_stock, self.cfg))
        return self._stock[type_id]

    def recipe(self, type_id: int) -> Optional[_Recipe]:
        """None when the type has no recipe at all (it can only be bought)."""
        if type_id not in self._recipes:
            self._recipes[type_id] = self._load_recipe(type_id)
        return self._recipes[type_id]

    def _structure_me_mult(self, activity: str, type_id: int) -> float:
        _, me_mult, _ = engine._facility_mults(activity, type_id, self.cfg)
        return me_mult

    def _load_recipe(self, type_id: int) -> Optional[_Recipe]:
        activity, bp = engine.classify_activity(type_id)
        if bp is None:
            return None
        blueprint_id, activity_id, product_qty = bp
        recipe = _Recipe(blueprint_id, activity_id, product_qty or 1.0, activity, "none")
        recipe.assumed_mult = engine._activity_mods(
            activity, type_id, self.cfg, self.cost_indices, blueprint_id)[0]
        original = engine._owned_bpo_best_me_te(blueprint_id)
        if original is not None:
            recipe.source = "original"
            if activity in ("Tech I", "Reaction"):
                # Manual ME override, owned BPO ME and structure bonus, exactly
                # as the planner applies them.
                recipe.unlimited_mult = engine._activity_mods(
                    activity, type_id, self.cfg, self.cost_indices, blueprint_id)[0]
            else:
                manual = engine._manual_me_te_override(type_id, self.cost_indices)
                base = manual[0] if manual is not None else 1 - original[0] / 100
                recipe.unlimited_mult = base * self._structure_me_mult(activity, type_id)
            return recipe
        if activity == "Reaction":
            return recipe  # formulas cannot be copied; no original = cannot react
        char_ids, corp_ids = engine.shared_production_owner_ids("blueprints")
        groups = storage.list_blueprint_copies(
            blueprint_id, owner_character_ids=char_ids, owner_corporation_ids=corp_ids)
        if groups:
            struct = self._structure_me_mult(activity, type_id)
            recipe.copies = sorted(
                ((1 - me / 100) * struct, runs, count) for me, runs, count in groups if runs > 0 and count > 0)
            recipe.source = "copies" if recipe.copies else "none"
            if recipe.copies:
                recipe.assumed_mult = recipe.copies[0][0]
        return recipe

    def buildable(self, type_id: int) -> bool:
        recipe = self.recipe(type_id)
        return recipe is not None and recipe.owned


@dataclass
class _Row:
    type_id: int
    depth: int
    needed: float = 0.0
    from_stock: float = 0.0
    built: float = 0.0
    runs: int = 0
    short: float = 0.0
    reason: Optional[str] = None
    # First item that asked for this one; a shared material is listed once,
    # under that parent.
    parent: Optional[int] = None


def _allocate_jobs(recipe: _Recipe, runs_needed: int,
                   copies_left: dict[int, list[list]]) -> tuple[list[tuple[float, int, int]], int]:
    """Splits `runs_needed` into jobs: (material_mult, runs, job_count).
    Returns the jobs and the runs no owned blueprint can cover."""
    if runs_needed <= 0:
        return [], 0
    if recipe.source == "original":
        return [(recipe.unlimited_mult, runs_needed, 1)], 0
    if recipe.source != "copies":
        return [], runs_needed
    pool = copies_left.setdefault(recipe.blueprint_id, [list(g) for g in recipe.copies])
    jobs: list[tuple[float, int, int]] = []
    remaining = runs_needed
    for group in pool:
        if remaining <= 0:
            break
        mult, runs, count = group
        if count <= 0:
            continue
        whole = min(count, remaining // runs)
        if whole:
            jobs.append((mult, runs, whole))
            group[2] -= whole
            remaining -= whole * runs
        if 0 < remaining < runs and group[2] > 0:
            # One copy runs part of its runs; the rest stays usable later.
            jobs.append((mult, remaining, 1))
            group[2] -= 1
            pool.append([mult, runs - remaining, 1])
            remaining = 0
    pool.sort(key=lambda g: g[0])
    return jobs, remaining


def _evaluate(ctx: _Context, type_id: int, quantity: int, ignore_top_blueprint: bool = False) -> dict[int, _Row]:
    """One stock-netted walk for `quantity` units. Breadth-first and pooled
    per level, like engine._expand_all, so a shared material is decided once
    per level on its pooled demand. Rows with `short > 0` make it infeasible.

    Runs of the item itself that no owned blueprint covers still list their
    materials (at the planner's assumed ME), so the table shows the whole
    requirement; the item row carries the blueprint shortfall.
    `ignore_top_blueprint` drops that shortfall: "what would materials allow"."""
    rows: dict[int, _Row] = {type_id: _Row(type_id, 0, needed=float(quantity))}
    used: dict[int, float] = {}
    copies_left: dict[int, list[list]] = {}
    level: dict[int, float] = {type_id: float(quantity)}
    depth = 0
    while level and depth < engine.MAX_DEPTH:
        next_level: dict[int, float] = {}
        first_parent: dict[int, int] = {}
        for tid, qty in level.items():
            recipe = ctx.recipe(tid)
            row = rows[tid]
            if recipe is None:
                row.short += qty
                row.reason = row.reason or "no_recipe"
                continue
            runs_needed = math.ceil(qty / recipe.product_qty - 1e-9)
            jobs, unbuilt = _allocate_jobs(recipe, runs_needed, copies_left)
            if unbuilt and depth == 0:
                jobs.append((recipe.assumed_mult, unbuilt, 1))
                if ignore_top_blueprint:
                    unbuilt = 0
            built_runs = runs_needed - unbuilt
            row.runs += built_runs
            row.built += built_runs * recipe.product_qty
            if unbuilt:
                row.short += max(0.0, qty - built_runs * recipe.product_qty)
                row.reason = "no_blueprint" if not recipe.owned else "blueprint_runs"
            for mult, runs, count in jobs:
                for material_id, base_qty in storage.get_blueprint_materials(recipe.blueprint_id, recipe.activity_id):
                    need = engine._material_qty(base_qty, mult, runs) * count
                    if need > 0:
                        next_level[material_id] = next_level.get(material_id, 0.0) + need
                        first_parent.setdefault(material_id, tid)

        level = {}
        for material_id, target in next_level.items():
            row = rows.get(material_id)
            if row is None:
                row = rows[material_id] = _Row(material_id, depth + 1, parent=first_parent[material_id])
            row.needed += target
            available = max(0.0, ctx.stock(material_id) - used.get(material_id, 0.0))
            consumed = min(target, available)
            used[material_id] = used.get(material_id, 0.0) + consumed
            row.from_stock += consumed
            missing = target - consumed
            if missing <= 0:
                continue
            if ctx.mode == "full" and ctx.buildable(material_id):
                level[material_id] = level.get(material_id, 0.0) + missing
            else:
                row.short += missing
                if ctx.mode == "direct" and ctx.buildable(material_id):
                    row.reason = "not_built_direct_mode"
                elif ctx.recipe(material_id) is not None:
                    row.reason = "no_blueprint"
                else:
                    row.reason = "material"
        depth += 1
    return rows


def _feasible(rows: dict[int, _Row]) -> bool:
    return all(row.short <= 1e-9 for row in rows.values())


def _blueprint_dict(recipe: _Recipe) -> dict:
    return {
        "blueprint_type_id": recipe.blueprint_id,
        "blueprint_name": engine._sde_type_name(recipe.blueprint_id),
        "source": recipe.source,
        "copy_runs": recipe.copy_runs if recipe.source == "copies" else None,
    }


def _row_dict(ctx: _Context, row: _Row, top_type_id: int, depth: Optional[int] = None) -> dict:
    recipe = ctx.recipe(row.type_id)
    blueprint = None
    if recipe is not None and (row.type_id == top_type_id or row.runs > 0
                               or row.reason in ("blueprint_runs", "no_blueprint")):
        blueprint = _blueprint_dict(recipe)
    on_hand = 0.0 if row.type_id == top_type_id else ctx.stock(row.type_id)
    return {
        "type_id": row.type_id,
        "type_name": engine._sde_type_name(row.type_id),
        "depth": row.depth if depth is None else depth,
        "needed": row.needed,
        "on_hand": on_hand,
        "from_stock": row.from_stock,
        "built": row.built,
        "runs": row.runs,
        "short": row.short,
        "reason": row.reason,
        "blueprint": blueprint,
    }


def _tree_order(rows: dict[int, _Row], top_type_id: int) -> list[tuple[_Row, int]]:
    """Rows depth-first under their first parent, siblings by name, so the
    indentation in the table shows what each material is for. Returns
    (row, display depth) pairs."""
    children: dict[Optional[int], list[_Row]] = {}
    for row in rows.values():
        if row.type_id != top_type_id:
            children.setdefault(row.parent, []).append(row)
    out: list[tuple[_Row, int]] = []
    stack = [(rows[top_type_id], 0)]
    while stack:
        row, depth = stack.pop()
        out.append((row, depth))
        kids = sorted(children.get(row.type_id, []), key=lambda r: engine._sde_type_name(r.type_id))
        stack.extend((kid, depth + 1) for kid in reversed(kids))
    return out


def _search(evaluate, upper: int) -> int:
    """Largest qty in [0, upper] with a feasible walk (requirements grow with qty)."""
    if upper < 1 or not _feasible(evaluate(1)):
        return 0
    lo, hi = 1, 2
    while hi <= upper and _feasible(evaluate(hi)):
        lo, hi = hi, hi * 2
    if hi > upper:
        if _feasible(evaluate(upper)):
            return upper
        hi = upper
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _feasible(evaluate(mid)):
            lo = mid
        else:
            hi = mid
    return lo


def max_buildable(type_id: int, cfg: ProductionConfig, mode: str = "full",
                  target_quantity: Optional[int] = None) -> dict:
    """Largest quantity of `type_id` that can be built right now, the
    bottleneck that stops one more, and the full requirement table (for the
    max quantity, or for `target_quantity` when given)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    ctx = _Context(cfg, mode)
    recipe = ctx.recipe(type_id)
    if recipe is None:
        raise ValueError("This item has no manufacturing blueprint or reaction formula.")

    cache: dict[int, dict[int, _Row]] = {}

    def evaluate(qty: int) -> dict[int, _Row]:
        if qty not in cache:
            cache[qty] = _evaluate(ctx, type_id, qty)
        return cache[qty]

    upper = MAX_SEARCH_QUANTITY
    if recipe.source == "copies":
        upper = min(upper, int(recipe.copy_runs * recipe.product_qty))
    elif recipe.source == "none":
        upper = 0
    best = _search(evaluate, upper)
    capped = best >= MAX_SEARCH_QUANTITY

    # Blueprint is what limits: also say how many the materials alone allow.
    material_max: Optional[int] = None
    if recipe.source != "original":
        material_max = _search(lambda qty: _evaluate(ctx, type_id, qty, ignore_top_blueprint=True),
                               MAX_SEARCH_QUANTITY)

    def ordered(rows: dict[int, _Row]) -> list[dict]:
        return [_row_dict(ctx, r, type_id, depth) for r, depth in _tree_order(rows, type_id)]

    bottlenecks: list[dict] = []
    if not capped:
        next_rows = evaluate(best + 1)
        short_rows = [r for r in next_rows.values() if r.short > 1e-9]
        short_rows.sort(key=lambda r: (-(r.short / r.needed if r.needed else 1.0), r.depth))
        bottlenecks = [_row_dict(ctx, r, type_id) for r in short_rows]

    requirements_quantity = target_quantity if target_quantity else max(best, 1)
    requirement_rows = evaluate(requirements_quantity)
    return {
        "type_id": type_id,
        "type_name": engine._sde_type_name(type_id),
        "mode": mode,
        "max_quantity": best,
        "capped": capped,
        "material_max_quantity": material_max,
        "product_qty_per_run": recipe.product_qty,
        "blueprint": _blueprint_dict(recipe),
        "bottleneck_quantity": None if capped else best + 1,
        "bottlenecks": bottlenecks,
        "requirements_quantity": requirements_quantity,
        "requirements_feasible": _feasible(requirement_rows),
        "requirements": ordered(requirement_rows),
    }
