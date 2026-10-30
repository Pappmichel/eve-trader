"""Production tool actions - UI-agnostic entry points, same shape as the
trading tool's actions.py, so the FastAPI routers (eve_trader/api/routers/)
only ever call into this module (no direct storage/engine access from the UI
layer).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from typing import Optional

import requests

from .. import storage, tenant_scope
from ..actions import ActionError
from ..auth import InvalidRoleKey, TokenManager, validate_role_key_for_tool
from ..config import ConfigError, OAUTH_CONFIG, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from ..paste_parser import merge_duplicate_stacks, parse_paste
from . import dynamic_targets, esi_sync, invention, jobs, order_integrity, pricing, sde
from .config import PRODUCTION_CONFIG, ProductionConfig, validate_production_merged, validate_production_overrides
from .constants import DECRYPTORS, JOB_CATEGORIES
from .engine import (
    _PlanContext, _invention_input_type_ids, _item_margin_detail_with_context, _structural_material_closure,
    asset_plan_ready_jobs, build_material_tree, compare_alchemy_profitability, discover_build_candidates, discover_ship_margins,
    distribution_recommendations, get_cached_discover_results, invention_logistics, item_margin_detail,
    invalidate_discover_cache, invalidate_ship_margin_cache, t1_bpc_invention_needs,
    invalidate_production_locations_cache, logistics_status, market_restock, market_status, plan_asset_optimized,
    plan_production, plan_special_order, production_needs_by_type, shared_production_owner_ids, stock_value,
)
from .models import (
    BuildCandidate, ManualBlueprintCopyCostRow, ManualBlueprintMeTeOverrideRow, OwnedBlueprintRow, ShipMarginRow,
    SpecialOrder, UnlistedStockRow,
)

# Capacity fields whose real change marks cached plans stale (phase 4).
# Compared against live cfg before save_tenant_config_overrides mutates it —
# POST /settings dumps every field, so a plain freight edit still carries
# reaction_* keys.
_REACTION_CAPACITY_KEYS = (
    "reaction_capacity_enabled",
    "reaction_cycle_days",
    "reaction_slot_override",
    "reaction_window_next",
    "reaction_window_interval_days",
    "reaction_formula_payback_cycles",
    "reaction_next_window_demand_factor",
    "reaction_stock_carry_value",
    "reaction_w2_from_sales",
)


def do_update_settings(updates: dict, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Persists `updates` to tenant_settings and applies them to the live
    PRODUCTION_CONFIG immediately (see Settings tab)."""
    reaction_capacity_changed = any(
        key in updates and getattr(cfg, key) != updates[key]
        for key in _REACTION_CAPACITY_KEYS
    )
    try:
        validate_production_overrides(updates)  # structure_type/rig_tier enum check - see its docstring
        validate_production_merged(cfg, updates)  # dynamic stock target cross-field checks (V-06)
        save_tenant_config_overrides("production", updates, cfg, cfg_type=ProductionConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    invalidate_discover_cache()  # a settings change (margin gate, structure/rig, ...) can change the result set
    # Allowlisted trigger (review finding V-01): any Production settings save can move a dynamic
    # target (min_margin, fees, structure, dynamic_target_*), so today's frozen rows are dropped.
    # Only touches storage when the feature is on or was just toggled (P-02).
    if cfg.dynamic_targets_enabled or "dynamic_targets_enabled" in updates:
        dynamic_targets.invalidate_today()
        _mark_cached_production_plans_stale()
    # Reaction capacity settings change the next plan's jobs/buys (D7/D12).
    if reaction_capacity_changed:
        _mark_cached_production_plans_stale()
    invalidate_ship_margin_cache()  # structure/rig/fee settings feed build_cost/margins here too
    if "home_location_id" in updates:
        invalidate_production_locations_cache()  # _current_stock's location set includes this
    return updates


def do_set_reaction_chain_choice(type_id: int, choice: Optional[str]) -> dict:
    """Store a D10 chaining choice (``buy`` / ``wait``), or ``None`` to
    delete the row and return to the default buy. Marks cached plans stale."""
    if not isinstance(type_id, int) or type_id <= 0:
        raise ActionError(f"type_id must be a positive integer, got {type_id!r}")
    if choice is None:
        storage.delete_reaction_chain_choice(type_id)
        _mark_cached_production_plans_stale()
        return {"type_id": type_id, "choice": None}
    if choice not in ("buy", "wait"):
        raise ActionError(f"choice must be 'buy', 'wait', or null, got {choice!r}")
    storage.upsert_reaction_chain_choice(type_id, choice)
    _mark_cached_production_plans_stale()
    return {"type_id": type_id, "choice": choice}


def do_get_reaction_capacity_status(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Derived slots / busy / capacity for the Settings card (read-only)."""
    from . import reaction_capacity
    state = reaction_capacity.window_state(cfg)
    total, _free_now = reaction_capacity.reaction_slot_totals(cfg)
    window_anchor = cfg.reaction_window_next
    window_effective = reaction_capacity.effective_window_iso(
        window_anchor, float(cfg.reaction_window_interval_days),
    )
    return {
        "derived_slots": total if cfg.reaction_slot_override is None else None,
        "slot_override": cfg.reaction_slot_override,
        "slots_total": state.total_slots,
        "slots_busy": state.busy_slots,
        "slots_free": state.free_slots,
        "slots_busy_w2": state.busy_slots_w2,
        "slots_free_w2": state.free_slots_w2,
        "window_anchor": window_anchor,
        "window_next": window_effective,
        "cycle_days": cfg.reaction_cycle_days,
        "capacity_slot_days": state.free_slots * float(cfg.reaction_cycle_days),
        "enabled": cfg.reaction_capacity_enabled,
    }


def do_set_system(profile: str, system_id: int, system_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Persists `system_id`/`system_name` under the given `profile`
    ("component" or "manufacturing" - see production/constants.py
    COMPONENT_GROUP_IDS) - so the rest of the app (system cost index
    lookups) keeps using the numeric ID without re-resolving it every time.

    `system_id` is expected to already be resolved client-side, picked from
    the static local SDE system list (GET /production/systems) - this used
    to re-resolve `system_name` via a live ESI call
    (ESIClient().resolve_system_id) on every save, which is both slower and
    a needless ESI-failure mode now that the frontend already has a real ID
    from a local, non-access-gated data source."""
    if profile not in ("component", "manufacturing"):
        raise ActionError(f"Unknown profile '{profile}'. Options: component, manufacturing")
    system_name = system_name.strip()
    save_tenant_config_overrides(
        "production", {f"{profile}_system_id": system_id, f"{profile}_system_name": system_name},
        cfg, cfg_type=ProductionConfig,
    )
    invalidate_discover_cache()  # system cost index feeds directly into build cost
    invalidate_ship_margin_cache()
    return {f"{profile}_system_name": system_name, f"{profile}_system_id": system_id}


def do_get_system_cost_indices(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Live ESI cost indices for the configured component/manufacturing
    systems - a display-only hint for Settings' manual override fields (so
    "what's a sane value here" doesn't require guessing - confirmed real
    live 2026-08-27: a user set 0.14 meaning to enter Jita-ballpark 0.014,
    which was actually ~5x *higher* than their real system's index, making
    the Buy/Build lists worse instead of better). Never raises -
    pricing.system_cost_indices_for already degrades to {} on any ESI
    failure or unset system_id; {} is normalized to None here so the
    frontend can treat "no data" as one falsy value."""
    esi_client = ESIClient()
    return {
        "component": pricing.system_cost_indices_for(esi_client, cfg.component_system_id) or None,
        "manufacturing": pricing.system_cost_indices_for(esi_client, cfg.manufacturing_system_id) or None,
    }


def do_check_sde_freshness(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Read-only - the actual refresh action (do_apply_sde) moved to
    admin.py (GitHub issue #34): the SDE cache is global/shared data, not
    per-tenant, so triggering a refresh is a cross-tenant-impacting action
    that belongs in the Admin tool's superadmin surface, not exposed to
    every Production tenant. This staleness check stays here since it's
    read-only and still legitimately informs both Production's and
    Trading's own sidebars.

    Two independent staleness checks, both read-only/no-op (neither ever
    auto-refreshes anything):

    1. newer_sde_available - is there a newer Fuzzwork dump than the one
       currently cached (see sde.check_for_newer_sde)?
    2. trading_universe_stale - has an SDE apply *changed the data Trading's
       candidate universe is built from* more recently than Trading's "Load
       Market Groups" candidate_universe snapshot was last rebuilt?
       Production's own scans (discover_build_candidates, plan_production)
       always walk the live SDE cache directly, so they never need this -
       but Trading caches a snapshot (see actions.do_build_universe), and a
       new item added to the SDE isn't visible there until that snapshot is
       explicitly rebuilt. An apply that changed nothing Trading reads does
       not count (sde.apply_sde only stamps trading_inputs_changed_at on a
       real change). True whenever both timestamps are known and the SDE
       change is newer; False (not "unknown") if either side has never
       happened - nothing to compare yet."""
    freshness = sde.check_for_newer_sde(cfg)
    sde_refreshed_at = storage.get_sde_trading_inputs_changed_at()
    universe_built_at = storage.get_candidate_universe_built_at()
    # Timestamps come from two different isoformat() calls (this module's own
    # tz-aware datetime.now(timezone.utc).isoformat() vs Trading's actions.
    # now_ts(), a naive utcnow().isoformat(timespec="seconds")) - comparing
    # the full strings directly risks a spurious mismatch from the trailing
    # "+00:00"/microseconds Trading's format lacks, so compare only the
    # shared whole-second prefix both formats guarantee.
    trading_universe_stale = bool(
        sde_refreshed_at and universe_built_at and sde_refreshed_at[:19] > universe_built_at[:19]
    )
    return {**freshness, "trading_universe_stale": trading_universe_stale, "trading_universe_built_at": universe_built_at}


# No do_auth_add_producer_character() here, deliberately - ESI login is the
# Characters page (`/api/characters/reauth/start`), not a prefix `/start`
# and not TokenManager.get_token_interactive_multi's old server-side
# webbrowser.open()-plus-local-HTTP-server flow.


def do_list_producer_characters() -> list[tuple[str, int, str]]:
    # docs/ESI_ACCESS_PLAN.md Known gap 4 (closed): sharing-based listing -
    # see list_shared_producer_characters' own docstring. Remove (below)
    # still only accepts a producer:* role_key; a character listed here
    # under an esi:<id> key correctly cannot be "removed" through this
    # endpoint (that key may be shared with other tools too) - the frontend
    # points such a character at the Characters page instead.
    return esi_sync.list_shared_producer_characters()


def do_remove_producer_character(role_key: str) -> dict:
    try:
        role_key = validate_role_key_for_tool(role_key, "production")
    except InvalidRoleKey as e:
        raise ActionError(str(e)) from e
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


def do_sync_esi() -> dict:
    """Pulls character + corp assets/industry-jobs/blueprints from ESI for
    every registered producer character.

    Track B (2026-09-11, this tenant): 0 producer characters; POST
    /api/production/esi/sync returned 400 in 0.001s before any ESI. Not
    migrated to pipeline_runner — re-measure if a real character roster
    ever hits a proxy timeout (Trading's original reason for a background
    job). The scheduler's ESI job is esi_data.orchestrator.do_sync_due;
    this remains the Production-page Sync button (do_sync_for_tool plus
    cache invalidation)."""
    result = esi_sync.sync_esi()
    # Owned-BPO ME/TE (storage.get_owned_bpo_best_me_te, used by _owned_bpo_mods
    # for every Tech I build-cost calc) comes from character_blueprints/
    # corp_blueprints - exactly what this just refreshed. Covers every case
    # that can change it: a newly added producer character's first sync, a
    # researched BPO's ME/TE improving, or a BPO changing hands - not just
    # add/remove-character, which by itself changes no stored blueprint data
    # at all (do_auth_add_producer_character only gets a token;
    # do_remove_producer_character only deletes it - storage.character_
    # blueprints/corp_blueprints keep whatever was last synced either way,
    # until the next do_sync_esi call, which is this one).
    invalidate_discover_cache()
    invalidate_ship_margin_cache()  # owned-BPO ME/TE feeds Tech I build_cost, including for ships
    return result


def do_get_esi_sync_time() -> dict:
    # esi_freshness, not a stamp of this tool's own button: Sync all, the
    # scheduler and other tools sharing the same kinds move it too.
    return {"synced_at": storage.newest_esi_freshness_success_for_tool("production")}


def do_add_stock_target(type_name: str, backup_stock: float = 0,
                         home_market_stock: float | None = None, jita_market_stock: float | None = None) -> dict:
    matches = storage.search_sde_types(type_name, limit=2)
    exact = [m for m in matches if m[1].lower() == type_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{type_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{type_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]
    storage.upsert_stock_target(type_id, resolved_name, backup_stock, home_market_stock, jita_market_stock)
    invalidate_discover_cache()  # this item must now be excluded from discovery (already tracked)
    return {"type_id": type_id, "type_name": resolved_name, "backup_stock": backup_stock,
            "home_market_stock": home_market_stock, "jita_market_stock": jita_market_stock}


def do_update_stock_target(type_id: int, backup_stock: float | None = None,
                            home_market_stock: float | None = None, jita_market_stock: float | None = None,
                            cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Edits an *existing* stock target's numeric fields in place (GitHub
    issue #16 - "should be able to change the targets directly in the table,
    like the targets on the doctrine table"). Unlike do_add_stock_target,
    takes type_id directly (the row already exists - no name lookup/exact-
    match ambiguity to resolve) and each field is genuinely optional:
    storage.upsert_stock_target already treats None as "leave this field
    alone" (see its own docstring), so editing just one column in the table
    doesn't require also resending the other two's current values.

    Doesn't invalidate the discover-build-candidates cache (unlike add/
    remove) - editing an existing target's numbers doesn't change *which*
    items count as "already tracked" (the only thing that cache depends
    on), only add/remove/SDE-refresh/decryptor-change do."""
    if (backup_stock is not None and backup_stock < 0) or (home_market_stock is not None and home_market_stock < 0) \
            or (jita_market_stock is not None and jita_market_stock < 0):
        raise ActionError("Stock targets cannot be negative.")
    # A dynamic row's home target is computed; with the global switch on an edit
    # would silently change a hidden value (P-37/section 9). With the switch off
    # every row acts as fixed, so editing the stored fixed value stays allowed.
    if home_market_stock is not None and getattr(cfg, "dynamic_targets_enabled", False):
        mode = storage.load_stock_target_modes().get(type_id, ("fixed",))[0]
        if mode == "dynamic":
            raise ActionError("This row's home market target is dynamic - switch it to fixed or edit min/max.")
    sde_type = storage.get_sde_type(type_id)
    if sde_type is None:
        raise ActionError(f"Unknown type_id {type_id} - refresh SDE first?")
    type_name = sde_type[2]
    storage.upsert_stock_target(type_id, type_name, backup_stock, home_market_stock, jita_market_stock)
    return {"type_id": type_id, "type_name": type_name, "backup_stock": backup_stock,
            "home_market_stock": home_market_stock, "jita_market_stock": jita_market_stock}


def do_remove_stock_target(type_id: int) -> dict:
    storage.delete_stock_target(type_id)  # also drops the item's frozen dynamic values (P-24)
    invalidate_discover_cache()  # this item is eligible for discovery again
    return {"removed": type_id}


# ---------------------------------------------------------------- dynamic targets
# docs/DYNAMIC_STOCK_TARGETS_PLAN.md section 10. Every trigger below is on the
# closed invalidation allowlist of review finding V-01.
def do_list_stock_targets(cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[dict]:
    """GET /stock-targets: the configured values plus, for dynamic rows, the
    effective value and the inputs behind it (a read that merges decisions
    belongs in an action, P-35)."""
    return dynamic_targets.list_stock_targets_view(cfg)


def _validate_mode_bounds(dynamic_min: float | None, dynamic_max: float | None) -> None:
    if dynamic_min is not None and dynamic_min < 0:
        raise ActionError("Minimum cannot be negative.")
    if dynamic_max is not None and dynamic_max < 0:
        raise ActionError("Maximum cannot be negative.")
    if dynamic_min is not None and dynamic_max is not None and dynamic_min > dynamic_max:
        raise ActionError("Minimum cannot be larger than the maximum.")


def do_set_stock_target_mode(type_id: int, mode: str, dynamic_min: float | None = None,
                              dynamic_max: float | None = None) -> dict:
    """Switches one row between 'fixed' and 'dynamic'. Switching on with no
    min/max applies the defaults (min 0, max = max(3 x the current fixed home
    value, 10) - D6/D13); a row without a home value must be given a max."""
    if mode not in ("fixed", "dynamic"):
        raise ActionError("Mode must be 'fixed' or 'dynamic'.")
    _validate_mode_bounds(dynamic_min, dynamic_max)
    rows = {r[0]: r for r in storage.load_stock_targets()}
    if type_id not in rows:
        raise ActionError(f"Type {type_id} is not a stock target.")
    _old_mode, old_min, old_max = storage.load_stock_target_modes().get(type_id, ("fixed", None, None))
    new_min = dynamic_min if dynamic_min is not None else old_min
    new_max = dynamic_max if dynamic_max is not None else old_max
    configured_home = float(rows[type_id][3] or 0.0)
    if mode == "dynamic":
        if new_min is None:
            new_min = 0.0
        if new_max is None:
            if configured_home <= 0:
                raise ActionError("This row has no home market target to derive a maximum from - enter a maximum.")
            new_max = dynamic_targets.default_max(configured_home)
        if new_max <= 0:
            raise ActionError("A dynamic row needs a maximum above 0.")
        _validate_mode_bounds(new_min, new_max)
    storage.set_stock_target_mode(type_id, mode, new_min, new_max)
    dynamic_targets.invalidate_today([type_id])
    _mark_cached_production_plans_stale()
    return {"type_id": type_id, "target_mode": mode, "dynamic_min": new_min, "dynamic_max": new_max}


def do_set_all_stock_target_modes(mode: str) -> dict:
    """Bulk "All to dynamic" / "All to fixed" in one transaction (D9). All to
    dynamic applies the D6/D13 defaults to rows without min/max, keeps
    existing ones, and skips rows with no home target (nothing to scale, D1,
    E-26) - they come back in `skipped` with the reason, never half-applied.
    All to fixed keeps min/max stored so a later All to dynamic restores them."""
    if mode not in ("fixed", "dynamic"):
        raise ActionError("Mode must be 'fixed' or 'dynamic'.")
    modes = storage.load_stock_target_modes()
    updates: list[tuple[int, str, float | None, float | None]] = []
    skipped: list[dict] = []
    for type_id, type_name, _backup, home, _jita in storage.load_stock_targets():
        _m, old_min, old_max = modes.get(type_id, ("fixed", None, None))
        if mode == "fixed":
            updates.append((type_id, "fixed", old_min, old_max))
            continue
        if not home or home <= 0:
            skipped.append({"type_id": type_id, "type_name": type_name,
                            "reason": "no home market target (backup-only or Jita-only row)"})
            continue
        updates.append((type_id, "dynamic", 0.0 if old_min is None else old_min,
                        dynamic_targets.default_max(home) if old_max is None else old_max))
    updated = storage.set_stock_target_modes_bulk(updates)
    dynamic_targets.invalidate_today()
    _mark_cached_production_plans_stale()
    return {"mode": mode, "updated": updated, "skipped": skipped}


def do_recalculate_dynamic_targets(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """The "Recalculate" button: drops today's frozen rows and runs the same
    freeze a plan run would (allowlisted trigger)."""
    if not cfg.dynamic_targets_enabled:
        raise ActionError("Dynamic targets are switched off (Settings -> Dynamic targets).")
    try:
        result = dynamic_targets.recalculate_dynamic_targets(cfg)
    except ValueError as e:
        raise ActionError(str(e)) from e
    _mark_cached_production_plans_stale()
    return result


def do_dynamic_target_history(type_id: int, days: int = 30) -> dict:
    days = max(1, min(int(days), 90))
    rows = storage.dynamic_value_history(type_id, days)
    for row in rows:
        row["day"] = row["day"].isoformat()
        row["computed_at"] = row["computed_at"].isoformat() if row["computed_at"] else None
    return {"type_id": type_id, "rows": rows}


def do_preview_dynamic_targets(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Read-only dry run for every row with a home target, switch ignored
    (section 14.1): current vs. proposed with all inputs. Writes nothing."""
    return dynamic_targets.preview_dynamic_targets(cfg)


def do_set_manual_stock(type_id: int, count: float, location_id: int = 0) -> dict:
    # The UI's NumberInput already enforces min=0, but the API itself had no
    # boundary check (confirmed: a raw negative POST was silently accepted) -
    # a negative override would poison _current_stock for that item across
    # every stock-target/plan/stock-value computation that reads it.
    if count < 0:
        raise ActionError("Current stock cannot be negative.")
    storage.upsert_manual_stock(type_id, count, location_id)
    return {"type_id": type_id, "count": count, "location_id": location_id}


def do_list_manual_stock_entries() -> dict:
    """One row per (type, location) - docs/MANUAL_TRACKING_PLAN.md phase 3,
    decision 9 - for the Stock Targets page's own "Manual stock" table,
    separate from the existing per-type total (do_set_manual_stock's own
    location_id=0-default single value)."""
    return {"rows": [
        {"type_id": type_id, "type_name": type_name, "location_id": location_id, "count": count}
        for type_id, type_name, location_id, count in storage.load_manual_stock_entries()
    ]}


def do_add_manual_stock_entry(item_name: str, count: float, location_id: int = 0) -> dict:
    if count < 0:
        raise ActionError("Count cannot be negative.")
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]
    storage.upsert_manual_stock(type_id, count, location_id)
    return {"type_id": type_id, "type_name": resolved_name, "location_id": location_id, "count": count}


def do_remove_manual_stock_entry(type_id: int, location_id: int = 0) -> dict:
    storage.delete_manual_stock(type_id, location_id)
    return {"type_id": type_id, "location_id": location_id}


def _parse_asset_paste(text: str) -> dict:
    """Shared by do_preview_asset_paste and do_commit_asset_paste (docs/
    MANUAL_TRACKING_PLAN.md phase 4) - parses an EVE inventory "Copy As"
    paste (eve_trader.paste_parser, same format issue #92's Ore & Minerals
    import uses), skips Blueprint-category lines (decision 10 - Phase 0
    found no reliable ME/TE/Runs clipboard format for them, so they can
    never be more than a name+quantity here), and resolves every remaining
    name to a type_id via storage.resolve_type_names_exact/suggest_type_names
    (decision 18's batch "Did you mean...?" pattern).

    Returns {"resolved": {type_id: (type_name, quantity)}, "skipped_blueprints":
    [name, ...], "unresolved": [{"line": raw_line, "suggestion": name|None}, ...],
    "errors": [{"line": raw_line, "error": message}, ...]}. Two different
    paste lines that both resolve to the same type_id (distinct names for
    the same item is not a real case, but merge_duplicate_stacks only
    merges identical names) have their quantities summed."""
    all_lines = parse_paste(text)
    error_lines = [line for line in all_lines if line.error]
    parsed = merge_duplicate_stacks(all_lines)

    skipped_blueprints = [line.name for line in parsed if line.category.strip().lower() == "blueprint"]
    item_lines = [line for line in parsed if line.category.strip().lower() != "blueprint"]

    exact = storage.resolve_type_names_exact([line.name for line in item_lines])
    missing = [line.name for line in item_lines if line.name.strip().lower() not in exact]
    suggestions = storage.suggest_type_names(missing) if missing else {}

    resolved: dict[int, list] = {}
    unresolved = []
    for line in item_lines:
        key = line.name.strip().lower()
        hit = exact.get(key)
        if hit is None:
            suggestion = suggestions.get(key)
            unresolved.append({"line": line.raw_line, "suggestion": suggestion[1] if suggestion else None})
            continue
        type_id, resolved_name, _category_id = hit
        if type_id in resolved:
            resolved[type_id][1] += line.quantity
        else:
            resolved[type_id] = [resolved_name, float(line.quantity)]

    return {
        "resolved": {type_id: (name, qty) for type_id, (name, qty) in resolved.items()},
        "skipped_blueprints": skipped_blueprints,
        "unresolved": unresolved,
        "errors": [{"line": line.raw_line, "error": line.error} for line in error_lines],
    }


def _validate_asset_paste_args(text: str, mode: str) -> None:
    if mode not in ("replace", "merge"):
        raise ActionError("Mode must be 'replace' or 'merge'.")
    if not text or not text.strip():
        raise ActionError("Paste is empty - copy items from an Inventory window's list view first.")


def do_preview_asset_paste(text: str, location_id: int, mode: str) -> dict:
    """Diffs an asset paste against this location's existing manual-stock
    entries, without writing anything - do_commit_asset_paste applies the
    exact same parse independently server-side (never trusting rows the
    client sends back)."""
    _validate_asset_paste_args(text, mode)
    parsed = _parse_asset_paste(text)
    existing = {
        type_id: (type_name, count)
        for type_id, type_name, loc_id, count in storage.load_manual_stock_entries()
        if loc_id == location_id
    }

    rows = []
    seen: set[int] = set()
    for type_id, (name, qty) in parsed["resolved"].items():
        seen.add(type_id)
        _old_name, old = existing.get(type_id, (name, 0.0))
        new = qty if mode == "replace" else old + qty
        status = "new" if type_id not in existing else ("unchanged" if new == old else "changed")
        rows.append({"type_id": type_id, "name": name, "old": old, "new": new, "status": status})

    if mode == "replace":
        for type_id, (name, old) in existing.items():
            if type_id not in seen:
                rows.append({"type_id": type_id, "name": name, "old": old, "new": 0.0, "status": "removed"})

    return {
        "rows": rows, "skipped_blueprints": parsed["skipped_blueprints"],
        "unresolved": parsed["unresolved"], "errors": parsed["errors"],
    }


def do_commit_asset_paste(text: str, location_id: int, mode: str) -> dict:
    """Re-parses `text` on the server (never takes rows from the client, so
    the frontend can't alter what actually gets written) and applies it via
    storage.apply_manual_stock_paste. Deliberately no cache invalidation
    (decision 4) - a stock change alone doesn't need discover_cache/
    ship_margin_cache invalidated, unlike a manual blueprint change."""
    _validate_asset_paste_args(text, mode)
    parsed = _parse_asset_paste(text)
    rows = {type_id: qty for type_id, (_name, qty) in parsed["resolved"].items()}
    storage.apply_manual_stock_paste(location_id, rows, mode)
    return {
        "applied": len(rows), "skipped_blueprints": parsed["skipped_blueprints"],
        "unresolved": parsed["unresolved"], "errors": parsed["errors"],
    }


def do_set_manual_build_buy(type_id: int, decision: str) -> dict:
    storage.upsert_manual_build_buy(type_id, decision)
    return {"type_id": type_id, "decision": decision}


def do_clear_manual_build_buy(type_id: int) -> dict:
    storage.delete_manual_build_buy(type_id)
    return {"type_id": type_id, "decision": "Auto"}


def do_set_selected_decryptor(type_id: int, decryptor: str) -> dict:
    if decryptor not in DECRYPTORS:
        raise ActionError(f"Unknown decryptor '{decryptor}'. Options: {', '.join(DECRYPTORS)}")
    storage.upsert_selected_decryptor(type_id, decryptor)
    invalidate_discover_cache()  # changes this item's T2 build cost/margin
    invalidate_ship_margin_cache()  # T2 ships are affected too
    return {"type_id": type_id, "decryptor": decryptor}


def do_clear_selected_decryptor(type_id: int) -> dict:
    storage.delete_selected_decryptor(type_id)
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"type_id": type_id, "decryptor": "Best"}


def do_set_category_location(category: str, location_id: int) -> dict:
    if category not in JOB_CATEGORIES:
        raise ActionError(f"Unknown category '{category}'. Options: {', '.join(JOB_CATEGORIES)}")
    storage.upsert_category_location(category, location_id)
    # Also remember it as a quick-switch option - setting a category active
    # shouldn't require a separate "save as option" step too.
    storage.add_category_location_option(category, location_id)
    invalidate_production_locations_cache()  # _current_stock now checks this location too
    return {"category": category, "location_id": location_id}


def do_clear_category_location(category: str) -> dict:
    storage.delete_category_location(category)
    invalidate_production_locations_cache()
    return {"category": category}


def do_set_category_cost_index_override(category: str, value: float) -> dict:
    """Per-category ISK job-cost-index override (confirmed with the user
    2026-09-16) - see storage.upsert_category_cost_index_override and
    engine.py's _job_cost_rate for the priority this slots into (higher
    than the existing flat reaction_/component_/manufacturing_cost_index_
    override fields, see do_set_cost_index_override above - not a
    replacement for those, an additional finer-grained layer). No cache to
    invalidate here, unlike do_set_category_location's location-cache
    dependency - _PlanContext reads this fresh on every plan/margin call,
    nothing caches it across calls."""
    if category not in JOB_CATEGORIES:
        raise ActionError(f"Unknown category '{category}'. Options: {', '.join(JOB_CATEGORIES)}")
    storage.upsert_category_cost_index_override(category, value)
    return {"category": category, "cost_index_override": value}


def do_clear_category_cost_index_override(category: str) -> dict:
    storage.delete_category_cost_index_override(category)
    return {"category": category}


def do_list_category_cost_index_overrides() -> dict:
    return {"overrides": storage.load_category_cost_index_overrides()}


def do_add_category_location_option(category: str, location_id: int) -> dict:
    if category not in JOB_CATEGORIES:
        raise ActionError(f"Unknown category '{category}'. Options: {', '.join(JOB_CATEGORIES)}")
    storage.add_category_location_option(category, location_id)
    return {"category": category, "location_id": location_id}


def do_remove_category_location_option(category: str, location_id: int) -> dict:
    storage.delete_category_location_option(category, location_id)
    return {"category": category, "location_id": location_id}


def do_resolve_structure_name(location_id: int, force: bool = False) -> dict:
    """Resolves `location_id` to its structure name (and solar_system_id,
    GitHub issue #12 - see storage.load_category_system_ids). The tenant's own
    lookup is the central resolver (locations.resolver.resolve_ids,
    docs/LOCATION_RESOLUTION_PLAN.md): the registry/global caches first, then the
    tenant's own structure_name_resolution characters (corp structure list,
    then per-character docking), and a refusal or error is remembered with a
    retry date instead of being cached forever. `force=True` looks it up again
    now. If that leaves it unresolved, the last tier is the operator fallback -
    only when the switch is on. The switch itself is Default-Tenant-only
    (admin.do_get/set_structure_resolution_fallback only ever read/write it
    there), so it is read inside tenant_scope.enter_tenant(DEFAULT_TENANT_ID),
    not off the ambient PRODUCTION_CONFIG: the requesting tenant's own copy is
    always False (confirmed real bug in code review, 2026-09-25). Resolution
    then retries with the Default Tenant's own characters inside that same
    scope; the token never leaves the server, only {name, solar_system_id}
    crosses back out.

    A character added before esi-universe.read_structures.v1/
    esi-corporations.read_structures.v1 existed needs to be re-added (remove
    + add again) before the character tiers work for it. Every success is
    written to this tenant's cache and the global one (decision Q2)."""
    from ..locations import resolver

    characters = esi_sync.list_capability_characters("structure_name_resolution")
    result = resolver.resolve_ids([location_id], force=force, characters=characters)
    info = (result.get("ids") or {}).get(location_id) or {}
    name = info.get("name")
    attempted = bool(info.get("attempted"))
    if name is not None:
        return {"location_id": location_id, "name": name, "cached": not attempted}
    if info.get("status") not in (None, "pending") and not attempted and not force:
        # A failure the registry remembers; it is retried on its own date.
        return {"location_id": location_id, "name": None, "cached": True}

    solar_system_id = None
    # Read lazily (only once the tenant's own lookup has failed) so a normal
    # success never pays for an extra tenant switch.
    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        fallback_enabled = PRODUCTION_CONFIG.global_structure_resolution_fallback
    if not characters and not fallback_enabled:
        raise ActionError(
            "No character has ticked Structure name resolution yet (Characters page, Access section)."
        )
    if fallback_enabled:
        with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
            fallback_characters = esi_sync.list_capability_characters("structure_name_resolution")
            if fallback_characters:
                fallback_client = ESIClient(tokens=TokenManager(OAUTH_CONFIG))
                fallback_corp_roles = esi_sync.corp_roles_for_characters(fallback_client, fallback_characters)
                fallback_resolved = esi_sync.resolve_structure_ids(
                    fallback_client, {location_id}, fallback_corp_roles, lambda: fallback_characters,
                )
                if location_id in fallback_resolved:
                    name, solar_system_id = fallback_resolved[location_id]
    if name is not None:
        resolver.record_resolved(location_id, name, solar_system_id)
    return {"location_id": location_id, "name": name, "cached": False}


def do_search_locations(query: str) -> dict:
    """Type-ahead for the LocationPicker (docs/MANUAL_TRACKING_PLAN.md
    phase 2) - NPC stations, this tenant's own resolved structures and its
    own manual names. See storage.search_locations for why the global
    structure cache is deliberately not searchable here."""
    return {"rows": [
        {"location_id": location_id, "name": name, "kind": kind}
        for location_id, name, kind in storage.search_locations(query)
    ]}


def do_set_manual_location_name(location_id: int, name: str) -> dict:
    """Gives `location_id` a tenant-own display name (docs/
    MANUAL_TRACKING_PLAN.md phase 2, decision 8). The logic is shared with the
    other tools in locations.actions."""
    from ..locations import actions as location_actions

    return location_actions.do_set_manual_name(location_id, name)


def do_remove_manual_location_name(location_id: int) -> dict:
    from ..locations import actions as location_actions

    return location_actions.do_remove_manual_name(location_id)


def do_estimate_invention(product_name: str, decryptor_name: str | None = None,
                           cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """`product_name` is the T2/T3 blueprint you want (e.g. "Small Shield Booster
    II Blueprint"), not the T1 base blueprint (or, for Tech III, the relic).
    If `decryptor_name` is None, compares every (grade, decryptor)
    combination (invention.compare_recipes_and_decryptors - for Tech III,
    "grade" means the Intact/Malfunctioning/Wrecked relic tier, not just the
    decryptor: see that function's own docstring); otherwise estimates just
    that one decryptor, still auto-picking the cheapest grade for it
    (invention.best_recipe_for_decryptor).

    Confirmed real bug (reported by a user, 2026-08-30): this used to take
    only storage.find_invention_recipe_by_product_name's own arbitrary,
    non-deterministic `blueprint_type_id` (no ORDER BY at all - fine for its
    OTHER return value, product_type_id, which is identical across every
    grade candidate for the same product, but not for blueprint_type_id,
    which varies by grade) and run every decryptor comparison against that
    one grade alone - so for Tech III this page could show, and the "single
    decryptor" mode could estimate against, a different relic grade on every
    call, never letting the user compare or deliberately pick a grade at
    all."""
    recipe = storage.find_invention_recipe_by_product_name(product_name.strip())
    if recipe is None:
        raise ActionError(
            f"No invention recipe found for '{product_name}'. Exact name? Refresh SDE first?"
        )
    _, product_blueprint_id = recipe

    # Seeded with the blueprint's own build materials plus its invention
    # inputs: the closure walks products, and a blueprint itself is no
    # product, so seeding with product_blueprint_id alone priced nothing.
    build_materials = [m for m, _qty in storage.get_blueprint_materials(product_blueprint_id, 1)]
    type_ids = list(_structural_material_closure(build_materials)
                    | _invention_input_type_ids(product_blueprint_id))
    home = pricing.home_prices(cfg, type_ids)
    jita = pricing.jita_prices(type_ids)
    # activity 1 = Manufacturing: the invented BPC's *own* build materials,
    # used to weigh ME savings the same way the Bauliste does (see engine.py).
    reducible_cost = invention.reducible_material_cost(product_blueprint_id, 1, home, jita, cfg)

    if decryptor_name is None:
        results = invention.compare_recipes_and_decryptors(product_blueprint_id, home, jita, cfg, reducible_cost)
    else:
        if decryptor_name not in DECRYPTORS:
            raise ActionError(f"Unknown decryptor '{decryptor_name}'. Options: {', '.join(DECRYPTORS)}")
        chosen = invention.best_recipe_for_decryptor(product_blueprint_id, decryptor_name, home, jita, cfg, reducible_cost)
        results = [chosen] if chosen is not None else []

    return {"results": results}


def do_refresh_production(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Recomputes Inventory (target vs. current stock) and the resulting
    Buy/Build lists. Raises if the SDE cache is empty (nothing to plan against).

    Track B (2026-09-11, this tenant): 0 stock targets, SDE loaded
    (sde_types=52999); POST /api/production/plan/refresh returned 400 in
    0.009s. Not migrated to pipeline_runner — re-measure against a real
    stock-target set if Compute Buy/Build List ever blocks the request.
    If migrated later, progress belongs on this function's stock-target
    loop, not inside _PlanContext/_expand_all (shared with unlisted-stock
    / discover / item_margin_detail)."""
    if sum(storage.sde_row_counts().values()) == 0:
        raise ActionError("SDE cache is empty. Run 'Refresh SDE' first.")
    if not storage.load_stock_targets() and not storage.list_integrated_special_order_items():
        raise ActionError("No stock targets configured.")
    plan = plan_production(cfg)
    return {
        "stock_targets": len(plan["inventory"]),
        "missing_types": sum(1 for r in plan["inventory"] if r.total_missing > 0),
        "buy_entries": len(plan["buy_list"]),
        "build_jobs": len(plan["build_list"]),
        "plan": plan,
    }


def do_refresh_asset_plan(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Recomputes the asset-aware Bauliste (engine.plan_asset_optimized).
    Raises under the same conditions as do_refresh_production."""
    if sum(storage.sde_row_counts().values()) == 0:
        raise ActionError("SDE cache is empty. Run 'Refresh SDE' first.")
    if not storage.load_stock_targets() and not storage.list_integrated_special_order_items():
        raise ActionError("No stock targets configured.")
    plan = plan_asset_optimized(cfg)
    return {"jobs": len(plan["jobs"]), "plan": plan}


# ------------------------------------------------------------- Special Orders
# One-off build orders, tracked separately from the permanent stock_targets
# list - see engine.plan_special_order's own docstring for the two
# deliberate differences from plan_production (no margin gate,
# net_against_stock replacing stock-target-driven netting).
# Frozen semantics: docs/PRODUCTION_SEMANTICS.md (SF-1..SF-8). API and the
# frontend must not call plan_special_order / _expand_all / _invention_need_row.

_COST_INDEX_KINDS = ("reaction", "component", "manufacturing")

# Callbacks registered by api/routers/production.py so special-order mutations
# that change integrated demand can mark that router's in-process
# `_last_plan` / `_last_asset_plan` caches stale (D9) - the plans stay
# readable, the pages show a recompute hint. Empty outside the web app
# (CLI / unit tests) - those have no cached plan.
_plan_cache_invalidators: list = []


def register_plan_cache_invalidator(fn) -> None:
    _plan_cache_invalidators.append(fn)


def _mark_cached_production_plans_stale() -> None:
    for fn in _plan_cache_invalidators:
        fn()


def _special_order_to_model(row: tuple, item_count: int) -> SpecialOrder:
    order_id, note, net_against_stock, status, created_at, include_in_production = row
    return SpecialOrder(
        order_id=str(order_id), note=note, net_against_stock=net_against_stock,
        status=status, created_at=str(created_at) if created_at else None,
        item_count=item_count, include_in_production=bool(include_in_production),
    )


def _order_is_integrated(row: tuple) -> bool:
    """Integrated = include_in_production AND status = 'open' (plan D1)."""
    return bool(row[5]) and row[3] == "open"


def _resolve_type(type_id_or_name: int | str) -> tuple[int, str]:
    """Accepts a numeric type_id or an item name (exact match, case-insensitive)
    - same lookup shape do_add_stock_target uses for names."""
    if isinstance(type_id_or_name, int) or (isinstance(type_id_or_name, str) and type_id_or_name.strip().isdigit()):
        type_id = int(type_id_or_name)
        sde_type = storage.get_sde_type(type_id)
        if sde_type is None:
            raise ActionError(f"Unknown type_id {type_id} - refresh SDE first?")
        return type_id, sde_type[2]
    stripped = str(type_id_or_name).strip()
    matches = storage.search_sde_types(stripped, limit=2)
    exact = [m for m in matches if m[1].lower() == stripped.lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{stripped}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{stripped}'. Did you mean: {matches[0][1]}?")
    return exact[0]


def _resolve_create_item(item: dict) -> tuple[int, str, float]:
    quantity = item["quantity"]
    raw = item.get("type_id")
    if raw is None:
        raw = item.get("type_id_or_name") or item.get("name")
    if raw is None or raw == "":
        raise ActionError("Each item needs type_id or name.")
    if quantity <= 0:
        raise ActionError(f"Quantity for {raw} must be positive.")
    type_id, type_name = _resolve_type(raw)
    return type_id, type_name, quantity


def _plan_special_order_items(items: list[tuple[int, str, float]], cfg: ProductionConfig,
                              net_against_stock: bool) -> dict:
    """Single planner entry for one-order compute and combined preview (SF-8)."""
    return plan_special_order(items, cfg, net_against_stock=net_against_stock)


def do_create_special_order(items: list[dict], note: str | None = None,
                             net_against_stock: bool = False,
                             include_in_production: bool = False) -> dict:
    """`items`: [{"type_id": int, "quantity": float}, ...] and/or
    {"name"|"type_id_or_name", "quantity"}. At least one item. Duplicate
    type_ids are summed (SF-3 / E.2 pooling). Header + items write in one
    transaction — a failed item does not leave an empty order.
    Turning `include_in_production` on also forces `net_against_stock`
    (SPECIAL_ORDERS_INTEGRATION_PLAN.md D2)."""
    if not items:
        raise ActionError("A special order needs at least one item.")
    pooled: dict[int, list] = {}
    for item in items:
        type_id, type_name, quantity = _resolve_create_item(item)
        if type_id in pooled:
            pooled[type_id][1] += quantity
        else:
            pooled[type_id] = [type_name, quantity]
    resolved = [(type_id, type_name, quantity) for type_id, (type_name, quantity) in pooled.items()]
    if include_in_production:
        net_against_stock = True
    order_id = storage.create_special_order_with_items(
        note, net_against_stock, resolved, include_in_production=include_in_production,
    )
    if include_in_production:
        _mark_cached_production_plans_stale()
    return {"order_id": order_id, "item_count": len(resolved)}


def do_list_special_orders(status: str | None = None) -> list[SpecialOrder]:
    """`status` None lists all; "open" / "done" filters. Unknown status is an error."""
    if status is not None and status not in ("open", "done"):
        raise ActionError(f"Unknown status {status!r} - must be 'open' or 'done'.")
    rows = storage.list_special_orders()
    if status is not None:
        rows = [row for row in rows if row[3] == status]
    return [_special_order_to_model(row, len(storage.list_special_order_items(str(row[0]))))
            for row in rows]


def do_get_special_order(order_id: str) -> dict:
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    items = storage.list_special_order_items(order_id)
    return {
        "order": _special_order_to_model(row, len(items)),
        "items": [{"type_id": t, "type_name": n, "quantity": q} for t, n, q in items],
    }


def do_update_special_order(order_id: str, status: str | None = None, note: str | None = None,
                             net_against_stock: bool | None = None,
                             include_in_production: bool | None = None) -> dict:
    """Partial update - same dynamic-dict shape as doctrine/actions.py's
    do_update_fitting/storage.update_doctrine. Used for "mark complete"
    (status="done"), "reopen" (status="open"), editing the note,
    flipping net_against_stock after creation, and include_in_production
    (SPECIAL_ORDERS_INTEGRATION_PLAN.md D1/D2). Turning integration on
    also sets net_against_stock=true; turning Prefer-stock off while
    integrated is refused. Combined preview still ignores stored flags
    (SF-5)."""
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    if status is not None and status not in ("open", "done"):
        raise ActionError(f"Unknown status {status!r} - must be 'open' or 'done'.")
    was_integrated = _order_is_integrated(row)
    current_include = bool(row[5])
    updates: dict = {}
    if status is not None:
        updates["status"] = status
    if note is not None:
        updates["note"] = note
    if include_in_production is not None:
        updates["include_in_production"] = include_in_production
        if include_in_production:
            # D2: integrated orders always net against stock.
            updates["net_against_stock"] = True
    if net_against_stock is not None:
        effective_include = (
            include_in_production if include_in_production is not None else current_include
        )
        if effective_include and not net_against_stock:
            raise ActionError(
                "Cannot turn off Prefer stock while the order is included in build lists."
            )
        # When turning integration on we already force True above; skip a
        # redundant / conflicting write if the caller also sent net=True.
        if "net_against_stock" not in updates:
            updates["net_against_stock"] = net_against_stock
    storage.update_special_order(order_id, updates)
    new_row = storage.get_special_order(order_id)
    now_integrated = _order_is_integrated(new_row) if new_row is not None else False
    # D9: mark cached Build / Asset-Optimized plans stale when integrated
    # demand changes. Note-only edits do not.
    if (was_integrated or now_integrated) and (
            status is not None or include_in_production is not None
    ):
        _mark_cached_production_plans_stale()
    return do_get_special_order(order_id)


def do_remove_special_order(order_id: str) -> dict:
    row = storage.get_special_order(order_id)
    was_integrated = _order_is_integrated(row) if row is not None else False
    storage.delete_special_order(order_id)
    if was_integrated:
        _mark_cached_production_plans_stale()
    return {"removed": order_id}


def do_set_special_order_item(order_id: str, type_id: int | str, quantity: float) -> dict:
    """Adds a new line item to an existing order, or updates an existing
    one's quantity (upsert). Does not compute a plan (SF-6). `type_id` may
    be a numeric id or an exact item name (E.3)."""
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    if quantity <= 0:
        raise ActionError(f"Quantity for type_id {type_id} must be positive.")
    resolved_id, type_name = _resolve_type(type_id)
    storage.upsert_special_order_item(order_id, resolved_id, type_name, quantity)
    if _order_is_integrated(row):
        _mark_cached_production_plans_stale()
    return do_get_special_order(order_id)


def do_remove_special_order_item(order_id: str, type_id: int | str) -> dict:
    """Refuses to remove an order's last remaining item - same "needs at
    least one item" invariant do_create_special_order enforces at creation,
    kept true for the lifetime of the order rather than only checked once.
    Unknown type on that order is an error (SF-6), not a silent no-op."""
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    if isinstance(type_id, int) or (isinstance(type_id, str) and str(type_id).strip().isdigit()):
        resolved_id = int(type_id)
    else:
        resolved_id, _type_name = _resolve_type(type_id)
    items = storage.list_special_order_items(order_id)
    if not any(t == resolved_id for t, _n, _q in items):
        raise ActionError(f"Type {resolved_id} is not on special order {order_id}.")
    if len(items) <= 1:
        raise ActionError("A special order needs at least one item - remove the whole order instead.")
    storage.remove_special_order_item(order_id, resolved_id)
    if _order_is_integrated(row):
        _mark_cached_production_plans_stale()
    return do_get_special_order(order_id)


def do_compute_special_order(order_id: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Runs engine.plan_special_order for one order's current line items.
    Raises under the same SDE-cache-empty precondition as
    do_refresh_production - a special order has no stock_targets
    equivalent precondition (it always has >=1 item, enforced at
    creation). Does not mutate stored orders (SF-4)."""
    if sum(storage.sde_row_counts().values()) == 0:
        raise ActionError("SDE cache is empty. Run 'Refresh SDE' first.")
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    _order_id, _note, net_against_stock, _status, _created_at, _include = row
    items = storage.list_special_order_items(order_id)
    return _plan_special_order_items(items, cfg, net_against_stock=net_against_stock)


def do_compute_combined_special_orders(order_ids: list[str], net_against_stock: bool,
                                        cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Temporary, unsaved combination of existing orders' line items into
    one pooled Buy/Build/Invention computation (SF-2 / SF-4). Items sharing
    a type_id across the selected orders are pooled (summed) (SF-3).
    `net_against_stock` is chosen fresh for this combined view (SF-5),
    independent of whatever each individual order's own setting is."""
    if not order_ids:
        raise ActionError("Select at least one special order to combine.")
    if len(order_ids) != len(set(order_ids)):
        raise ActionError("Duplicate special order ids cannot be combined.")
    if sum(storage.sde_row_counts().values()) == 0:
        raise ActionError("SDE cache is empty. Run 'Refresh SDE' first.")
    pooled: dict[int, list] = {}  # type_id -> [type_name, quantity]
    for order_id in order_ids:
        if storage.get_special_order(order_id) is None:
            raise ActionError(f"Special order {order_id} not found.")
        for type_id, type_name, quantity in storage.list_special_order_items(order_id):
            if type_id in pooled:
                pooled[type_id][1] += quantity
            else:
                pooled[type_id] = [type_name, quantity]
    items = [(type_id, type_name, quantity) for type_id, (type_name, quantity) in pooled.items()]
    return _plan_special_order_items(items, cfg, net_against_stock=net_against_stock)


def do_audit_special_orders() -> dict:
    """Read-only integrity report (E.2). Never repairs and never plans."""
    issues = order_integrity.audit()
    return {"ok": not issues, "issues": issues}


def do_list_special_order_events(order_id: str | None = None) -> dict:
    rows = storage.list_special_order_events(order_id)
    return {
        "rows": [
            {"event_id": str(event_id), "order_id": str(oid), "event": event,
             "detail": detail, "at": str(at) if at else None}
            for event_id, oid, event, detail, at in rows
        ]
    }


def do_set_cost_index_override(kind: str, value: float) -> dict:
    """E.5: writes an existing ProductionConfig cost-index field via Settings."""
    if kind not in _COST_INDEX_KINDS:
        raise ActionError(f"Unknown cost-index kind {kind!r} - must be reaction, component, or manufacturing.")
    return do_update_settings({f"{kind}_cost_index_override": value})


def do_clear_cost_index_override(kind: str) -> dict:
    if kind not in _COST_INDEX_KINDS:
        raise ActionError(f"Unknown cost-index kind {kind!r} - must be reaction, component, or manufacturing.")
    return do_update_settings({f"{kind}_cost_index_override": None})


def do_list_cost_index_overrides() -> dict:
    cfg = PRODUCTION_CONFIG
    return {
        "overrides": {kind: getattr(cfg, f"{kind}_cost_index_override") for kind in _COST_INDEX_KINDS}
    }


# GitHub issue #64 (found in a full-codebase audit 2026-08-21): the three
# Logistics-tab reads used to call engine.py directly from
# api/routers/production.py, bypassing this module - the only architectural
# exception to "actions.py is the one entry point" outside the small,
# deliberate list CLAUDE.md documents (portfolio.py/scheduler.py). Thin
# wrappers, same shape as every other do_* here - the router still owns
# `_last_plan` itself (transient, router-local in-memory state with no CLI
# equivalent to keep in sync, unlike everything else in this module) and its
# own "was a plan computed yet" precondition check, since that's about the
# router's own state, not something a do_* action can meaningfully own.
#
# Each of the two material reads takes either the Build List (default scope)
# or the asset plan's jobs ("ready now" scope, docs/LOGISTICS_SCOPE_TOGGLE_
# PLAN.md) - exactly one of the two.
def _logistics_jobs(build_list: Optional[list], asset_jobs: Optional[list]) -> list:
    if (build_list is None) == (asset_jobs is None):
        raise ActionError("Pass either a build list or asset plan jobs.")
    return build_list if build_list is not None else asset_plan_ready_jobs(asset_jobs)


def do_get_logistics_status(build_list: Optional[list] = None, asset_jobs: Optional[list] = None) -> list:
    return logistics_status(_logistics_jobs(build_list, asset_jobs))


def do_get_distribution_recommendations(build_list: Optional[list] = None,
                                        asset_jobs: Optional[list] = None) -> list:
    return distribution_recommendations(_logistics_jobs(build_list, asset_jobs))


def do_get_market_restock() -> list:
    return market_restock()


def do_get_invention_logistics(invention_list: list) -> list:
    return invention_logistics(invention_list)


def do_get_t1_bpc_invention_needs(invention_list: list) -> list:
    return t1_bpc_invention_needs(invention_list)


def do_market_status(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Personal stock vs. backup target, and home/Jita market-listed stock vs.
    their own targets, for the Marktstatus tab."""
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured.")
    return {"rows": market_status(cfg)}


def do_stock_value(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Total ISK value of current stock, for the KPI shown on the
    Stock-Ziele tab."""
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured.")
    return stock_value(cfg)


def do_build_material_tree(type_name: str, quantity: float = 1.0,
                            cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Resolves `type_name` (exact match, same lookup do_add_stock_target uses
    - not just invented T2/T3 items like do_estimate_invention, any
    manufacturable-or-not item) and returns its full recursive material tree.
    See engine.build_material_tree."""
    matches = storage.search_sde_types(type_name, limit=2)
    exact = [m for m in matches if m[1].lower() == type_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{type_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{type_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    type_ids = list(_structural_material_closure([type_id]))
    home = pricing.home_prices(cfg, type_ids)
    jita = pricing.jita_prices(type_ids)
    selected_decryptors = storage.load_selected_decryptors()
    t2_memo: dict = {}
    # This function doesn't build a real _PlanContext (no ESI system-cost-
    # index/adjusted-price calls needed - the tree never shows job cost) -
    # but a manual ME/TE override must still apply here too (confirmed real
    # bug 2026-09-16, see build_material_tree's own docstring), so load just
    # that override-only slice of what _PlanContext would otherwise build.
    cost_indices = {
        f"me_te_override:{override_type_id}": (me, te)
        for override_type_id, _name, me, te in storage.load_manual_blueprint_me_te_overrides()
    }

    return build_material_tree(type_id, quantity, cfg, home, jita, selected_decryptors, t2_memo,
                                cost_indices=cost_indices)


def do_discover_build_candidates(top_n: int = 200, cfg: ProductionConfig = PRODUCTION_CONFIG,
                                  progress_callback=None) -> dict:
    """Scans every manufacturable, market-listed SDE item not already a
    stock target for ones where building clearly beats buying right now -
    see engine.discover_build_candidates. Needs the SDE cache populated
    (Refresh SDE) to find anything at all.

    The underlying scan is cached for a few minutes (engine._DISCOVER_CACHE_TTL) -
    repeat calls (e.g. re-clicking "Discover" with a different top_n) reuse it
    instead of re-walking ~19,400 SDE items each time; invalidated automatically
    by anything that changes the result set (Settings save, stock target
    add/remove, decryptor change, SDE refresh - see invalidate_discover_cache
    call sites in this module).

    progress_callback is optional so this stays usable in-process (e.g. a
    future CLI/scheduler call) unchanged - the HTTP background-job path
    (do_start_discover_build_candidates) passes pipeline_runner's writer,
    matching do_refresh_sde/do_sync_contracts's own shape."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Refresh SDE first.")
    candidates = discover_build_candidates(cfg, top_n=top_n, progress_callback=progress_callback)
    return {"rows": [BuildCandidate(**c) for c in candidates]}


def do_start_discover_build_candidates(top_n: int = 200) -> dict:
    """Kicks off Discover Build Candidates as a background job - the scan
    walks up to ~19,400 SDE items (engine._scan_build_candidates's own
    docstring), genuinely slow enough on a cold cache to warrant progress
    reporting instead of a blocking spinner with no feedback, same
    reasoning as Admin's SDE refresh."""
    from .. import pipeline_runner
    return pipeline_runner.start_discover_build_candidates(top_n=top_n)


def do_discover_build_candidates_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(pipeline_runner.TOOL_PRODUCTION)


def do_get_build_candidates(top_n: int = 200) -> dict:
    """Read-only: the current tenant's last-computed Discover Build
    Candidates result (engine.get_cached_discover_results), without
    triggering a scan - the background-job POST only ever returns
    {run_id, status}, so the frontend fetches the actual rows here once the
    job's status flips to succeeded. Empty (not an error) if no scan has
    completed yet for this tenant."""
    candidates = get_cached_discover_results(top_n=top_n) or []
    return {"rows": [BuildCandidate(**c) for c in candidates]}


def do_compare_alchemy(product_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[dict]:
    """Thin wrapper - see engine.compare_alchemy_profitability. Resolves
    product_name via the same exact-match SDE lookup pattern used
    elsewhere (see refining/reprocessing.py's resolve_type_id for the
    pattern), raises ActionError if unresolvable."""
    matches = storage.search_sde_types(product_name, limit=5)
    type_id = None
    for tid, type_name in matches:
        if type_name.strip().lower() == product_name.strip().lower():
            type_id = tid
            break
    if type_id is None:
        raise ActionError(f"No exact SDE match for '{product_name}'.")
    result = compare_alchemy_profitability(type_id, cfg)
    return asdict(result) if result else None


def do_get_ship_margins(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Margin page's list view - every ship's current home/Jita price, build
    cost, and both margins. See engine.discover_ship_margins - cached there,
    same as do_discover_build_candidates."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Refresh SDE first.")
    rows = discover_ship_margins(cfg)
    return {"rows": [ShipMarginRow(**r) for r in rows]}


def do_get_item_margin(item_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> ShipMarginRow:
    """Margin page's search - resolves `item_name` (exact match, same lookup
    do_build_material_tree uses) and returns its
    current home/Jita price, build cost, and both margins for any category,
    not just ships. See engine.item_margin_detail."""
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    return ShipMarginRow(**item_margin_detail(type_id, resolved_name, cfg))


def _fetch_unlisted_character_personal(client: ESIClient, role: str, character_id: int) -> dict:
    """Phase A of do_unlisted_stock: character-scoped assets/orders/corp id
    in isolation. Same error-per-character contract as the old sequential
    loop (ESIError -> empty list / None, never abort the rest). Corp
    assets/orders stay sequential in the caller - they are stateful across
    characters (first Director/Accountant in original list order claims
    that corp; parallelizing would race two characters to "claim" it),
    matching esi_sync.sync_esi's Phase A/B split."""
    try:
        assets = client.character_assets(character_id, auth_role=role)
    except ESIError:
        assets = []
    try:
        orders = client.character_orders(character_id, auth_role=role)
    except ESIError:
        orders = []
    try:
        corporation_id = client.character_public_info(character_id)["corporation_id"]
    except ESIError:
        corporation_id = None
    return {
        "role": role, "character_id": character_id,
        "assets": assets, "orders": orders, "corporation_id": corporation_id,
    }


def _accumulate_stock_at_location(assets: list[dict], location_id: int, out: dict[int, float]) -> None:
    """In-memory equivalent of storage.esi_stock_at_location's per-location
    aggregation (same corp-office-unwrap + NON_STOCK_LOCATION_FLAGS exclusion
    logic - see that function's docstring for why both are needed), for
    live-fetched asset lists that were never written to the DB. Assembled
    ships and their fittings don't count (storage.mark_assembled_or_fitted)."""
    storage.mark_assembled_or_fitted(assets)
    office_item_ids = {
        a["item_id"] for a in assets
        if a.get("type_id") == storage.OFFICE_TYPE_ID and a.get("location_id") == location_id
    }
    valid_locations = {location_id} | office_item_ids
    for a in assets:
        if a.get("location_id") not in valid_locations:
            continue
        if a.get("location_flag") in storage.NON_STOCK_LOCATION_FLAGS or a["assembled_or_fitted"]:
            continue
        out[a["type_id"]] = out.get(a["type_id"], 0.0) + a.get("quantity", 0)


def do_unlisted_stock(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Live-fetches every registered producer character's (+ their corps')
    assets and sell orders (personal *and* corp - see corp_orders_seen below)
    at cfg.home_location_id, and returns stock targets sitting there with no
    open sell order at all - matches the Trading tool's
    do_check_seller_unlisted_stock (always a fresh live read, never relies
    on the last "ESI-Daten synchronisieren" snapshot, so it can't show a
    stale answer if you sold/listed something moments ago).

    Only considers stock targets that actually have a home_market_stock or
    jita_market_stock target set (see listed_target_ids below) - a target
    with only backup_stock configured is a personal/component buffer never
    meant to be listed for sale, so it must not show up here.

    Track B (2026-09-11, this tenant): POST /api/production/unlisted-stock/check
    returned 400 in 0.001s (no home_location_id; also 0 stock targets and
    0 producer characters). Not migrated to pipeline_runner."""
    if cfg.home_location_id is None:
        raise ActionError("No structure/location ID configured (Settings -> Market & Location).")
    stock_targets = dynamic_targets.effective_stock_targets(cfg)
    if not stock_targets:
        raise ActionError("No stock targets configured.")
    # docs/ESI_ACCESS_PLAN.md Known gap 4 (closed): sharing-based, not the
    # legacy producer:* prefix - a character shared via the newer esi:<id>
    # key (Characters page add-a-character path) must not be invisible here.
    characters = esi_sync.list_shared_producer_characters()
    if not characters:
        raise ActionError("No Production character shared yet.")

    client = ESIClient(tokens=TokenManager(OAUTH_CONFIG))
    stock_qty: dict[int, float] = {}
    sell_qty: dict[int, float] = {}
    seen_corp_assets: set[int] = set()
    # Separate from seen_corp_assets: corp assets need the Director role,
    # corp orders need Accountant/Trader - confirmed real bug where stock
    # sitting in a corp hangar (assets, Director-gated) had a real corp sell
    # order (orders, Accountant/Trader-gated) that this check never looked
    # at, so it was always flagged "unlisted" even while actually for sale.
    seen_corp_orders: set[int] = set()

    # Phase A: personal assets/orders/public-info in parallel, same pattern
    # as esi_sync.sync_esi. list() not as_completed() so Phase B still sees
    # characters in original registration order (first Director/Accountant
    # per corp wins). with_current_tenant: worker threads don't inherit
    # contextvars (CLAUDE.md) - TokenManager refresh would otherwise 500.
    with ThreadPoolExecutor(max_workers=min(8, len(characters))) as pool:
        char_results = list(pool.map(
            storage.with_current_tenant(
                lambda c: _fetch_unlisted_character_personal(client, c[0], c[1])),
            characters,
        ))

    for r in char_results:
        _accumulate_stock_at_location(r["assets"], cfg.home_location_id, stock_qty)
        for o in r["orders"]:
            if not o.get("is_buy_order") and o.get("location_id") == cfg.home_location_id:
                sell_qty[o["type_id"]] = sell_qty.get(o["type_id"], 0.0) + o.get("volume_remain", 0)

        corporation_id = r["corporation_id"]
        if corporation_id is None:
            continue
        role = r["role"]

        if corporation_id not in seen_corp_assets:
            try:
                corp_assets = client.corporation_assets(corporation_id, auth_role=role)
            except ESIError:
                pass  # missing Director role on this character - a later one might have it
            else:
                seen_corp_assets.add(corporation_id)
                _accumulate_stock_at_location(corp_assets, cfg.home_location_id, stock_qty)

        if corporation_id not in seen_corp_orders:
            try:
                corp_orders = client.corporation_orders(corporation_id, auth_role=role)
            except ESIError:
                pass  # missing Accountant/Trader role on this character - a later one might have it
            else:
                seen_corp_orders.add(corporation_id)
                for o in corp_orders:
                    if not o.get("is_buy_order") and o.get("location_id") == cfg.home_location_id:
                        sell_qty[o["type_id"]] = sell_qty.get(o["type_id"], 0.0) + o.get("volume_remain", 0)

    # Only stock targets with an actual home/Jita *market* target are meant to
    # be listed for sale - a target with only backup_stock set (home_market_
    # stock and jita_market_stock both None/0) is a personal/component buffer
    # the user never intends to sell, so it must not be flagged as "unlisted"
    # (confirmed real bug: e.g. every Decryptor here is backup-only, yet all
    # of them showed up as "unlisted" yielding a useless, noisy list).
    # A dynamic row is always meant for sale, even while its effective target
    # is 0 (E-10) - otherwise its stock would drop out of this check exactly
    # when the target fell.
    listed_target_ids = {t[0] for t in stock_targets if (t[3] or 0) > 0 or (t[4] or 0) > 0}
    listed_target_ids |= dynamic_targets.dynamic_type_ids(cfg)
    unlisted: list[tuple[int, str, float]] = []
    for type_id, qty in stock_qty.items():
        if type_id not in listed_target_ids or qty <= 0:
            continue
        if sell_qty.get(type_id, 0.0) <= 0:
            sde_type = storage.get_sde_type(type_id)
            name = sde_type[2] if sde_type else str(type_id)
            unlisted.append((type_id, name, qty))

    # GitHub issue #45: also show the structure's own current sell volume
    # (everyone's listed quantity at C-J, same ESI call the Trading
    # shortlist uses) and margin_home (see engine.margin_home) - best-effort,
    # degrades to None rather than blocking the whole page (matches this
    # function's existing per-character ESIError tolerance above).
    structure_stats_by_item = {}
    if unlisted:
        first_role = characters[0][0]
        try:
            structure_stats_by_item = client.structure_order_stats_bulk(
                cfg.home_location_id, [type_id for type_id, _name, _qty in unlisted], auth_role=first_role)
        except ESIError:
            pass

    # One _PlanContext for every unlisted row, not one per row. Each
    # item_margin_detail call used to rebuild stock_targets/manual
    # overrides/decryptors and walk the full BOM tree again - the same
    # waste discover_build_candidates already avoided by sharing a
    # context+memo pair across its ~19,400-item scan. Public
    # item_margin_detail is unchanged (Margin page still wants a fresh
    # context for a single lookup).
    #
    # extra_type_ids covers every unlisted row's own type_id (same reason
    # item_margin_detail now passes its own searched type_id, see that
    # function's docstring) - without it, an unlisted row whose BOM shares
    # nothing with stock_targets (an owned-but-unlisted Titan/Supercarrier,
    # same failure mode) would silently price as None instead of its real
    # build cost.
    ctx = None
    cost_memo: dict[int, float | None] = {}
    t2_memo: dict[int, tuple[float, float, str | None]] = {}
    if unlisted:
        try:
            ctx = _PlanContext(cfg, extra_type_ids=[type_id for type_id, _name, _qty in unlisted])
        except (ESIError, requests.RequestException):
            ctx = None

    # Lazy: doctrine.engine imports production.engine.
    from ..doctrine.engine import doctrine_needs_by_type
    doctrine_needs = doctrine_needs_by_type() if unlisted else {}
    production_needs = production_needs_by_type() if unlisted else {}
    rows = []
    for type_id, name, qty in unlisted:
        need = doctrine_needs.get(type_id)
        structure_stats = structure_stats_by_item.get(type_id)
        sell_volume = structure_stats.sell_volume if structure_stats else None
        try:
            if ctx is None:
                margin = None
            else:
                margin = _item_margin_detail_with_context(
                    type_id, name, cfg, ctx, cost_memo, t2_memo).get("margin_home")
        except (ESIError, requests.RequestException):
            # Found in code review: unlike every other try/except in this
            # function (pure ESI calls, whose own client already wraps
            # transport failures into ESIError), the _PlanContext build also
            # calls Goonmetrics directly (pricing.jita_prices/home_prices) -
            # a Goonmetrics outage used to propagate uncaught past this
            # best-effort degrade, turning "margin unknown for this one row"
            # into a 500 for the whole Unlisted Stock page.
            margin = None
        rows.append(UnlistedStockRow(type_id=type_id, type_name=name, stock_quantity=qty,
                                      sell_volume=sell_volume, margin=margin,
                                      doctrine_shortfall=need["shortfall"] if need else None,
                                      doctrine_names=need["doctrines"] if need else [],
                                      production_needed=production_needs.get(type_id)))
    rows.sort(key=lambda r: r.stock_quantity, reverse=True)
    return {"rows": rows}


def do_list_current_jobs() -> dict:
    """Every active character + corp industry job, for the Industriejobs tab."""
    return {"rows": jobs.list_current_jobs()}


def do_character_slot_overview() -> dict:
    """Total/used/free industry job slots per registered character, for the
    Charakter-Slots tab."""
    return {"rows": jobs.character_slot_overview()}


def do_set_character_slot_excluded(character_name: str, excluded: bool) -> dict:
    """GitHub issue #39: excludes/includes `character_name` from the shared
    free-slot pool (_free_slots_by_category) and therefore the asset-
    optimized build list's slot-splitting - e.g. an alt kept registered for
    ESI sync/asset visibility but not actually meant to run production jobs.
    Persisted (storage.set_character_slot_excluded), survives the next ESI
    re-sync (replace_character_slots is now an UPSERT, not delete+reinsert).
    No cache to invalidate here - plan_asset_optimized has no cache of its
    own (api/routers/production.py's _last_asset_plan is only ever
    refreshed by its own explicit "Refresh" button, same as every other
    settings change that affects the asset-optimized plan)."""
    storage.set_character_slot_excluded(character_name, excluded)
    return {"character_name": character_name, "excluded": excluded}


def do_list_owned_blueprints() -> dict:
    """Every owned blueprint (character + corp), aggregated across identical
    (type_id, is_original, ME, TE, runs) groups, for the Blueprints tab -
    surfaces storage.load_owned_blueprints() (synced but never shown before).
    A BPO is identified by runs == -1 (ESI's convention for "original"), not
    by any explicit is_blueprint_copy flag (that field only exists on the
    *asset* tables, not the blueprint tables - see esi_sync.py's
    _blueprint_rows)."""
    char_ids, corp_ids = shared_production_owner_ids("blueprints")
    grouped: dict[tuple, int] = {}
    for type_id, quantity, material_efficiency, time_efficiency, runs in storage.load_owned_blueprints(
        owner_character_ids=char_ids, owner_corporation_ids=corp_ids,
    ):
        is_original = runs == -1
        key = (type_id, is_original, material_efficiency, time_efficiency, None if is_original else runs)
        # ESI's `quantity` field for a blueprint item is usually a sentinel
        # (-1 original / -2 copy), not an actual stack size - only add it
        # when it looks like a real positive count, else this row is 1 item.
        grouped[key] = grouped.get(key, 0) + (quantity if quantity and quantity > 0 else 1)

    rows = []
    for (type_id, is_original, me, te, runs), qty in grouped.items():
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        rows.append(OwnedBlueprintRow(
            type_id=type_id, type_name=name, is_original=is_original,
            quantity=qty, material_efficiency=me, time_efficiency=te, runs=runs,
        ))

    # Manual rows (docs/MANUAL_TRACKING_PLAN.md phase 5) - each its own row,
    # never merged with an ESI row or with each other (see OwnedBlueprintRow's
    # own docstring for why).
    for manual_id, bp_type_id, bp_name, is_original, me, te, runs, quantity, location_id in \
            storage.load_manual_owned_blueprints():
        rows.append(OwnedBlueprintRow(
            type_id=bp_type_id, type_name=bp_name, is_original=is_original, quantity=quantity,
            material_efficiency=me, time_efficiency=te, runs=runs,
            source="manual", manual_id=manual_id, location_id=location_id,
        ))

    rows.sort(key=lambda r: r.type_name)
    return {"rows": rows}


def _validate_manual_blueprint_me_te(material_efficiency: int, time_efficiency: int) -> None:
    if not (0 <= material_efficiency <= 10):
        raise ActionError("Material efficiency must be between 0 and 10.")
    if not (0 <= time_efficiency <= 20) or time_efficiency % 2 != 0:
        raise ActionError("Time efficiency must be an even number between 0 and 20.")


def do_add_manual_owned_blueprint(item_name: str, is_original: bool, material_efficiency: int,
                                   time_efficiency: int, runs: Optional[int], quantity: int,
                                   location_id: int = 0) -> dict:
    """`item_name` accepts the blueprint's own name ("Rifter Blueprint") or
    the product's name ("Rifter") - the latter is mapped to its blueprint
    via storage.get_blueprint_for_product (same location picker/ME/TE
    treatment as an ESI-owned blueprint, decision 14)."""
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    if storage.is_known_blueprint(type_id):
        blueprint_type_id = type_id
    else:
        bp = storage.get_blueprint_for_product(type_id)
        if bp is None:
            raise ActionError(f"'{resolved_name}' is not a known blueprint, or a producible item.")
        blueprint_type_id = bp[0]

    _validate_manual_blueprint_me_te(material_efficiency, time_efficiency)
    if is_original:
        if runs is not None:
            raise ActionError("A blueprint original (BPO) has no runs.")
    elif not runs or runs <= 0:
        raise ActionError("A blueprint copy (BPC) needs runs > 0.")
    if quantity <= 0:
        raise ActionError("Quantity must be positive.")

    bp_sde_type = storage.get_sde_type(blueprint_type_id)
    bp_name = bp_sde_type[2] if bp_sde_type else str(blueprint_type_id)
    manual_id = storage.insert_manual_owned_blueprint(
        blueprint_type_id, is_original, material_efficiency, time_efficiency, runs, quantity, location_id)
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {
        "manual_id": manual_id, "type_id": blueprint_type_id, "type_name": bp_name, "is_original": is_original,
        "material_efficiency": material_efficiency, "time_efficiency": time_efficiency,
        "runs": runs, "quantity": quantity, "location_id": location_id,
    }


def do_update_manual_owned_blueprint(manual_id: int, material_efficiency: int, time_efficiency: int,
                                      runs: Optional[int], quantity: int) -> dict:
    existing = storage.get_manual_owned_blueprint(manual_id)
    if existing is None:
        raise ActionError(f"No manual blueprint entry #{manual_id}.")
    _id, _bp_type_id, is_original, _me, _te, _runs, _qty, _loc = existing

    _validate_manual_blueprint_me_te(material_efficiency, time_efficiency)
    if is_original:
        if runs is not None:
            raise ActionError("A blueprint original (BPO) has no runs.")
    elif not runs or runs <= 0:
        raise ActionError("A blueprint copy (BPC) needs runs > 0.")
    if quantity <= 0:
        raise ActionError("Quantity must be positive.")

    storage.update_manual_owned_blueprint(manual_id, material_efficiency, time_efficiency, runs, quantity)
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"manual_id": manual_id, "material_efficiency": material_efficiency,
            "time_efficiency": time_efficiency, "runs": runs, "quantity": quantity}


def do_remove_manual_owned_blueprint(manual_id: int) -> dict:
    storage.delete_manual_owned_blueprint(manual_id)
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"manual_id": manual_id}


_UNSET = object()  # sentinel for "field not provided" on a partial PATCH, distinct from None ("clear it")


def _validate_ready_at(ready_at: Optional[str]) -> None:
    """manual_industry_jobs.ready_at is TIMESTAMPTZ - an unparseable string
    would otherwise reach Postgres unvalidated and surface as a bare 500
    (psycopg raising on the INSERT/UPDATE) instead of a clean 400. Confirmed
    real gap in code review (2026-09-25). Accepts the same ISO 8601 shape
    JavaScript's Date.toISOString() produces (a trailing "Z"), which
    datetime.fromisoformat only understands as "+00:00" from Python 3.11 -
    normalize it by hand so this validates identically on 3.10 and 3.11
    (this repo's CI matrix, see .github/workflows/ci.yml)."""
    if ready_at is None:
        return
    try:
        datetime.fromisoformat(ready_at.replace("Z", "+00:00"))
    except ValueError as e:
        raise ActionError(f"ready_at must be a valid ISO 8601 timestamp, got '{ready_at}'.") from e


def _resolve_manual_job_quantity(product_type_id: int, quantity: Optional[float],
                                  runs: Optional[int]) -> tuple[float, int, Optional[int]]:
    """Exactly one of `quantity`/`runs` must be given - `runs` is converted
    to a quantity via the product's own qty-per-run (decision 13: rejected
    if there's no known blueprint at all); a raw `quantity` is stored as-is
    with `runs` left None (display-only, "if entered as runs" - see the
    table's own column comment)."""
    if (quantity is None) == (runs is None):
        raise ActionError("Provide exactly one of quantity or runs.")
    bp = storage.get_blueprint_for_product(product_type_id)
    if bp is None:
        raise ActionError("This item has no known blueprint - it can't be an industry job's output.")
    _blueprint_type_id, activity_id, product_qty = bp
    if runs is not None:
        if runs <= 0:
            raise ActionError("Runs must be positive.")
        return runs * product_qty, activity_id, runs
    if quantity is None or quantity <= 0:
        raise ActionError("Quantity must be positive.")
    return quantity, activity_id, None


def do_add_manual_industry_job(item_name: str, quantity: Optional[float] = None, runs: Optional[int] = None,
                                location_id: int = 0, ready_at: Optional[str] = None) -> dict:
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    product_type_id, resolved_name = exact[0]

    _validate_ready_at(ready_at)
    resolved_quantity, activity_id, resolved_runs = _resolve_manual_job_quantity(product_type_id, quantity, runs)

    manual_id = storage.insert_manual_industry_job(
        product_type_id, activity_id, resolved_quantity, resolved_runs, location_id, ready_at)
    return {
        "manual_id": manual_id, "type_id": product_type_id, "type_name": resolved_name,
        "activity_id": activity_id, "quantity": resolved_quantity, "runs": resolved_runs,
        "location_id": location_id, "ready_at": ready_at,
    }


def do_update_manual_industry_job(manual_id: int, quantity=_UNSET, runs=_UNSET,
                                   location_id=_UNSET, ready_at=_UNSET) -> dict:
    """A true partial update (decision from code review 2026-09-25 - the
    old signature defaulted every field to None, which is indistinguishable
    from "explicitly clear this", so a caller updating only e.g. location_id
    was forced to also resend quantity/runs and always cleared ready_at).
    Each parameter's default is `_UNSET`, not None, so "not passed at all"
    (keep the existing value) and "passed as null" (e.g. clear ready_at)
    are no longer the same thing. The router only forwards fields the
    client actually set (Pydantic's `model_dump(exclude_unset=True)`)."""
    existing = storage.get_manual_industry_job(manual_id)
    if existing is None:
        raise ActionError(f"No manual job entry #{manual_id}.")
    _id, product_type_id, existing_activity_id, existing_qty, existing_runs, existing_location_id, \
        existing_ready_at = existing

    if quantity is _UNSET and runs is _UNSET:
        resolved_quantity, activity_id, resolved_runs = existing_qty, existing_activity_id, existing_runs
    else:
        effective_quantity = None if quantity is _UNSET else quantity
        effective_runs = None if runs is _UNSET else runs
        resolved_quantity, activity_id, resolved_runs = _resolve_manual_job_quantity(
            product_type_id, effective_quantity, effective_runs)

    effective_location_id = existing_location_id if location_id is _UNSET else location_id
    if ready_at is _UNSET:
        # Confirmed real bug in code review (2026-09-25): existing_ready_at
        # comes back from storage.get_manual_industry_job as a real
        # datetime (psycopg's own TIMESTAMPTZ mapping), not a string - only
        # ever validate a value the client actually just sent (always a
        # string or None); the already-stored value needs no re-validation,
        # and _validate_ready_at's str.replace("Z", ...) would TypeError on
        # a datetime.
        effective_ready_at = existing_ready_at
    else:
        _validate_ready_at(ready_at)
        effective_ready_at = ready_at

    storage.update_manual_industry_job(manual_id, resolved_quantity, resolved_runs,
                                        effective_location_id, effective_ready_at)
    return {
        "manual_id": manual_id, "activity_id": activity_id, "quantity": resolved_quantity,
        "runs": resolved_runs, "location_id": effective_location_id, "ready_at": effective_ready_at,
    }


def do_remove_manual_industry_job(manual_id: int) -> dict:
    storage.delete_manual_industry_job(manual_id)
    return {"manual_id": manual_id}


def do_complete_manual_industry_job(manual_id: int, location_id: Optional[int] = None) -> dict:
    """Deletes the job and books its quantity into manual_stock -
    `location_id=None` (the default) uses the job's own output location
    (decision 12); an explicit `location_id` overrides that (e.g. moved the
    finished goods somewhere else before completing)."""
    existing = storage.get_manual_industry_job(manual_id)
    if existing is None:
        raise ActionError(f"No manual job entry #{manual_id}.")
    _id, _product_type_id, _activity_id, _qty, _runs, job_location_id, _ready_at = existing
    effective_location_id = job_location_id if location_id is None else location_id

    storage.complete_manual_job(manual_id, effective_location_id)
    return {"manual_id": manual_id, "location_id": effective_location_id}


def do_list_manual_listed_stock() -> dict:
    """{(type_id, market): (quantity, updated_at)} as a JSON-friendly list -
    the Stock Targets page's own "Listed Home/Jita (manual)" columns
    (docs/MANUAL_TRACKING_PLAN.md phase 7)."""
    return {"rows": [
        {"type_id": type_id, "market": market, "quantity": quantity, "updated_at": updated_at}
        for (type_id, market), (quantity, updated_at) in storage.load_manual_listed_stock().items()
    ]}


def do_set_manual_listed_stock(type_id: int, market: str, quantity: float) -> dict:
    if market not in ("home", "jita"):
        raise ActionError("Market must be 'home' or 'jita'.")
    if quantity < 0:
        raise ActionError("Quantity cannot be negative.")
    storage.upsert_manual_listed_stock(type_id, market, quantity)
    return {"type_id": type_id, "market": market, "quantity": quantity}


def do_clear_manual_listed_stock(type_id: int, market: str) -> dict:
    storage.delete_manual_listed_stock(type_id, market)
    return {"type_id": type_id, "market": market}


def do_list_manual_blueprint_copy_costs() -> dict:
    """GitHub issue #40 - the Blueprints page's second table: purchase cost +
    included run count for blueprint copies that must be bought outright
    (never owned as a BPO, not inventable)."""
    rows = [
        ManualBlueprintCopyCostRow(
            type_id=type_id, type_name=type_name, purchase_cost=purchase_cost, runs=runs,
            cost_per_run=purchase_cost / runs,
        )
        for type_id, type_name, purchase_cost, runs in storage.load_manual_blueprint_copy_costs()
    ]
    return {"rows": rows}


def do_add_manual_blueprint_copy_cost(item_name: str, purchase_cost: float, runs: int) -> dict:
    """Resolves `item_name` (exact match, same lookup do_get_item_margin/
    do_build_material_tree use) to a type_id and registers/updates its
    manual BPC cost. `item_name` is the *product* built from the copy, not
    the blueprint's own name (matches manual_blueprint_copy_costs' own
    schema - a blueprint's product is a stable 1:1 lookup either way)."""
    if purchase_cost <= 0:
        raise ActionError("Purchase cost must be a positive number.")
    if runs <= 0:
        raise ActionError("Runs must be a positive integer.")
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    storage.upsert_manual_blueprint_copy_cost(type_id, resolved_name, purchase_cost, runs)
    invalidate_discover_cache()  # build cost feeds directly into build-vs-buy decisions
    invalidate_ship_margin_cache()
    return {"type_id": type_id, "type_name": resolved_name, "purchase_cost": purchase_cost, "runs": runs}


def do_update_manual_blueprint_copy_cost(type_id: int, purchase_cost: float, runs: int) -> dict:
    """Edits purchase_cost/runs for an already-registered row in place (the
    Blueprints page's inline-editable table) - unlike do_add_*, takes
    type_id directly instead of re-resolving an item name, since the row
    already exists."""
    if purchase_cost <= 0:
        raise ActionError("Purchase cost must be a positive number.")
    if runs <= 0:
        raise ActionError("Runs must be a positive integer.")
    if not storage.update_manual_blueprint_copy_cost(type_id, purchase_cost, runs):
        raise ActionError(f"No registered blueprint copy cost found for type_id {type_id}.")
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"type_id": type_id, "purchase_cost": purchase_cost, "runs": runs}


def do_remove_manual_blueprint_copy_cost(type_id: int) -> dict:
    storage.delete_manual_blueprint_copy_cost(type_id)
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"removed": type_id}


def _validate_me_te(material_efficiency: int, time_efficiency: int) -> None:
    if not 0 <= material_efficiency <= 10:
        raise ActionError("Material Efficiency must be between 0 and 10.")
    if not 0 <= time_efficiency <= 20:
        raise ActionError("Time Efficiency must be between 0 and 20.")


def do_list_manual_blueprint_me_te_overrides() -> dict:
    """Confirmed with the user 2026-09-16 - the Blueprints page's third
    table: a fixed ME/TE for a blueprint's product, taking priority over
    both the app's flat "assumes perfect research" baseline and any owned
    BPO's real ME/TE (see engine._activity_mods' own docstring for the
    full resolution chain)."""
    rows = [
        ManualBlueprintMeTeOverrideRow(
            type_id=type_id, type_name=type_name,
            material_efficiency=material_efficiency, time_efficiency=time_efficiency,
        )
        for type_id, type_name, material_efficiency, time_efficiency in storage.load_manual_blueprint_me_te_overrides()
    ]
    return {"rows": rows}


def do_add_manual_blueprint_me_te_override(item_name: str, material_efficiency: int, time_efficiency: int) -> dict:
    """Resolves `item_name` (exact match, same lookup do_add_manual_blueprint_
    copy_cost uses) to a type_id and registers/updates its manual ME/TE
    override. `item_name` is the *product* built from the blueprint, not the
    blueprint's own name (matches manual_blueprint_me_te_overrides' own
    schema - a blueprint's product is a stable 1:1 lookup either way)."""
    _validate_me_te(material_efficiency, time_efficiency)
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    storage.upsert_manual_blueprint_me_te_override(type_id, resolved_name, material_efficiency, time_efficiency)
    invalidate_discover_cache()  # build cost feeds directly into build-vs-buy decisions
    invalidate_ship_margin_cache()
    return {"type_id": type_id, "type_name": resolved_name,
            "material_efficiency": material_efficiency, "time_efficiency": time_efficiency}


def do_update_manual_blueprint_me_te_override(type_id: int, material_efficiency: int, time_efficiency: int) -> dict:
    """Edits material_efficiency/time_efficiency for an already-registered
    row in place (the Blueprints page's inline-editable table) - unlike
    do_add_*, takes type_id directly instead of re-resolving an item name,
    since the row already exists."""
    _validate_me_te(material_efficiency, time_efficiency)
    if not storage.update_manual_blueprint_me_te_override(type_id, material_efficiency, time_efficiency):
        raise ActionError(f"No registered ME/TE override found for type_id {type_id}.")
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"type_id": type_id, "material_efficiency": material_efficiency, "time_efficiency": time_efficiency}


def do_remove_manual_blueprint_me_te_override(type_id: int) -> dict:
    storage.delete_manual_blueprint_me_te_override(type_id)
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"removed": type_id}


# ---------------------------------------------------- realized profit (Phase 3)
def _realized_period(days: int | None, start: str | None, end: str | None, since_t0: bool):
    """Resolve the overview window. Default is the last 30 days. `since_t0`
    is the whole ledger from cutover and cannot be combined with another range."""
    from datetime import datetime, timedelta, timezone

    if since_t0 and (days is not None or start or end):
        raise ActionError("since_t0 cannot be combined with days or a custom range.")
    now = datetime.now(timezone.utc)
    if since_t0:
        return {"start": None, "end": now, "since_t0": True, "days": None}
    if start or end:
        if not start or not end:
            raise ActionError("A custom range needs both start and end.")
        start_at = _parse_realized_time(start, "start")
        end_at = _parse_realized_time(end, "end")
        if end_at <= start_at:
            raise ActionError("end must be after start.")
        return {"start": start_at, "end": end_at, "since_t0": False, "days": None}
    window = 30 if days is None else int(days)
    if window < 1 or window > 3660:
        raise ActionError("days must be between 1 and 3660.")
    return {"start": now - timedelta(days=window), "end": now, "since_t0": False, "days": window}


def _parse_realized_time(value: str, name: str):
    from datetime import datetime, timezone

    text = (value or "").strip()
    if not text:
        raise ActionError(f"{name} is required.")
    try:
        if len(text) == 10:
            parsed = datetime.fromisoformat(text)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as e:
        raise ActionError(f"{name} must be an ISO date or datetime.") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _finite_non_negative(name: str, value) -> float:
    import math

    try:
        number = float(value)
    except (TypeError, ValueError) as e:
        raise ActionError(f"{name} must be a number.") from e
    if not math.isfinite(number) or number < 0:
        raise ActionError(f"{name} must be zero or positive.")
    return number


def do_rebuild_realized(reset_opening: bool = False) -> dict:
    """Rebuild derived realized-profit tables from the archive.

    CLI and the background worker call this in-process. The HTTP route
    starts it via do_start_rebuild_realized so a request is not held for
    the whole recompute. The run row is written only after the replace
    transaction commits.

    `reset_opening` drops the frozen opening quantities so this rebuild
    derives them again from today's ESI stock (they are also re-derived
    automatically when T0 or the shared owners change).
    """
    from ..realized import loader

    try:
        return loader.rebuild_realized(reset_opening=bool(reset_opening))
    except ActionError:
        raise
    except Exception as e:
        raise ActionError(f"Realized profit rebuild failed: {e}") from e


def do_start_rebuild_realized(reset_opening: bool = False) -> dict:
    from .. import pipeline_runner
    return pipeline_runner.start_rebuild_realized(reset_opening=bool(reset_opening))


def do_realized_rebuild_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(
        pipeline_runner.TOOL_PRODUCTION, pipeline_runner.JOB_REBUILD_REALIZED,
    )


def do_get_realized_summary(days: int | None = None, start: str | None = None,
                            end: str | None = None, since_t0: bool = False) -> dict:
    from .realized import report
    period = _realized_period(days, start, end, since_t0)
    return report.summary(**period)


def do_get_realized_products(days: int | None = None, start: str | None = None,
                             end: str | None = None, since_t0: bool = False) -> dict:
    from .realized import report
    period = _realized_period(days, start, end, since_t0)
    return report.products(**period)


def do_get_realized_product(type_id: int, days: int | None = None, start: str | None = None,
                            end: str | None = None, since_t0: bool = False) -> dict:
    if int(type_id) <= 0:
        raise ActionError("type_id must be a positive item id.")
    from .realized import report
    period = _realized_period(days, start, end, since_t0)
    return report.product(type_id=int(type_id), **period)


def do_get_realized_sale_chain(sale_ref: str) -> dict:
    ref = (sale_ref or "").strip()
    if not ref or len(ref) > 200:
        raise ActionError("sale_ref is required.")
    from .realized import report
    chain = report.sale_chain(ref)
    if chain is None:
        raise ActionError(f"No realized sale {ref}.")
    return chain


def do_get_realized_jobs(days: int | None = None, start: str | None = None,
                         end: str | None = None, since_t0: bool = False,
                         state: str = "all") -> dict:
    """Costed jobs. Without a period every job since T0; with one, finished
    jobs that ended in it (running jobs are never cut). `state`: all /
    finished / running."""
    from .realized import report
    if state not in report.JOB_STATES:
        raise ActionError(f"state must be one of {', '.join(report.JOB_STATES)}.")
    if days is None and not start and not end and not since_t0:
        return report.jobs(state=state)
    period = _realized_period(days, start, end, since_t0)
    return report.jobs(start=period["start"], end=period["end"], state=state)


def do_get_realized_inventory() -> dict:
    from .realized import report
    return report.inventory()


def do_get_realized_warnings() -> dict:
    from .realized import report
    return report.warnings()


def do_add_ledger_entry(entry_kind: str, amount: float, entry_date: str,
                        type_id: int | None = None, quantity: float | None = None,
                        note: str | None = None) -> dict:
    """A manual cost or revenue. `amount` is the entry's total ISK.
    Quantity is stored and not multiplied in. Opening-cost overrides use
    do_set_opening_cost. These rows adjust the period report; they are not
    FIFO layers (the costing engine consumes only opening_cost overrides)."""
    from ..realized import db as ledger_db

    kind = (entry_kind or "").strip()
    if kind not in ("cost", "revenue"):
        raise ActionError("entry_kind must be cost or revenue.")
    total = _finite_non_negative("amount", amount)
    when = _parse_realized_time(entry_date, "entry_date")
    if type_id is not None and int(type_id) <= 0:
        raise ActionError("type_id must be a positive item id.")
    qty = None
    if quantity is not None:
        qty = _finite_non_negative("quantity", quantity)
    text = (note or "").strip() or None
    if text is not None and len(text) > 500:
        raise ActionError("note must be 500 characters or fewer.")
    entry_id = ledger_db.insert_manual_entry(
        entry_kind=kind, amount=total, entry_date=when,
        type_id=None if type_id is None else int(type_id),
        quantity=qty, note=text,
    )
    return {
        "entry_id": entry_id,
        "entry_kind": kind,
        "type_id": None if type_id is None else int(type_id),
        "quantity": qty,
        "amount": total,
        "entry_date": when.isoformat(),
        "note": text,
    }


def do_set_opening_cost(type_id: int, unit_cost: float, entry_date: str | None = None) -> dict:
    """Override the opening layer's unit cost for one type. Takes effect on
    the next rebuild; this call only stores the override."""
    from datetime import datetime, timezone

    from ..realized import db as ledger_db

    if int(type_id) <= 0:
        raise ActionError("type_id must be a positive item id.")
    cost = _finite_non_negative("unit_cost", unit_cost)
    when = datetime.now(timezone.utc) if not entry_date else _parse_realized_time(entry_date, "entry_date")
    entry_id = ledger_db.replace_opening_cost(int(type_id), cost, when)
    return {
        "entry_id": entry_id,
        "type_id": int(type_id),
        "unit_cost": cost,
        "entry_date": when.isoformat(),
        "applied_on_next_rebuild": True,
    }


def do_reapply_freight_rate(dry_run: bool = False) -> dict:
    """Write the current per-hub freight rates onto purchases that already
    have a frozen freight volume. Does not rebuild; the next recompute
    picks the new rates up. Home-region buys lose freight (U4); a
    station-trade sale drops freight in costing, not here (ST3). `freight_rate_per_m3` is The Forge rate, reported
    so the confirm dialog can name it; other hubs use their own entry.
    `dry_run` only counts the purchases that would change."""
    from ..realized import db as ledger_db
    from ..realized.ingest import _freight_rate_for_forge, _regions_with_freight

    rate = float(_freight_rate_for_forge(PRODUCTION_CONFIG))
    rates = _regions_with_freight(PRODUCTION_CONFIG)
    home = PRODUCTION_CONFIG.home_location_id
    home_region = ledger_db.region_id_for_location(int(home)) if home else None
    clear_locations = [home] if home is not None else []
    clear_regions = [home_region] if home_region is not None else []
    kwargs = {
        "rates": rates,
        "clear_location_ids": clear_locations,
        "clear_region_ids": clear_regions,
    }
    if dry_run:
        return {
            "dry_run": True,
            "would_update": ledger_db.count_freight_rate_changes(**kwargs),
            "freight_rate_per_m3": rate,
            "freight_label": "configured rate",
        }
    updated = ledger_db.reapply_freight_rates(**kwargs)
    return {
        "dry_run": False,
        "updated": updated,
        "freight_rate_per_m3": rate,
        "freight_label": "configured rate",
        "rebuild_required": True,
    }


def do_get_realized_opening() -> dict:
    """Opening inventory of the latest rebuild (section 9a Ledger tab):
    quantity, reference price on T0, the unit cost used, and the override."""
    from .realized import report
    return report.opening()


def do_list_ledger_entries() -> dict:
    """Manual cost/revenue entries and opening-cost overrides."""
    from .realized import report
    return report.ledger_entries()


def do_delete_ledger_entry(entry_id: int) -> dict:
    """Delete one manual entry or opening-cost override. Takes effect in
    the report at once for cost/revenue, on the next rebuild for an
    opening-cost override."""
    from ..realized import db as ledger_db

    if int(entry_id) <= 0:
        raise ActionError("entry_id must be positive.")
    if not ledger_db.delete_manual_entry(int(entry_id)):
        raise ActionError(f"No ledger entry {int(entry_id)}.")
    return {"deleted": int(entry_id)}


def do_get_realized_archive_status() -> dict:
    """Per owner: which archive kinds are shared with Production, last
    archive sync per kind, recorded gaps, and kinds that have archived rows
    but are no longer shared (hidden, D11)."""
    from .realized import report
    return report.archive_status()


def do_get_realized_courier() -> dict:
    """Monthly courier comparison for The Forge to the home structure.
    Does not change costing."""
    from .realized import report
    return report.courier_comparison()


def _hidden_archive(owner_type: str, owner_id: int, kind: str, delete: bool) -> dict:
    from ..realized import access as ledger_access
    if owner_type not in ("character", "corporation"):
        raise ActionError("owner_type must be character or corporation.")
    try:
        if delete:
            count = ledger_access.delete_hidden_archive_rows(owner_type, int(owner_id), kind)
        else:
            count = ledger_access.count_hidden_archive_rows(owner_type, int(owner_id), kind)
    except ledger_access.ArchiveStillShared as exc:
        raise ActionError(str(exc)) from exc
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    key = "deleted" if delete else "count"
    return {"owner_type": owner_type, "owner_id": int(owner_id), "kind": kind, key: count}


def do_count_hidden_archive_rows(owner_type: str, owner_id: int, kind: str) -> dict:
    """How many archived rows of a hidden kind would be deleted."""
    return _hidden_archive(owner_type, owner_id, kind, delete=False)


def do_delete_hidden_archive_rows(owner_type: str, owner_id: int, kind: str) -> dict:
    """Delete one owner's hidden archive rows of `kind`, plus sync state and
    gaps. Refuses while that kind is shared with Production."""
    return _hidden_archive(owner_type, owner_id, kind, delete=True)
