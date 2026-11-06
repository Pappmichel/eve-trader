"""Doctrine tool actions - the do_* functions api/routers/doctrine.py and
(should a CLI equivalent ever be added) cli.py both call, exactly the same
UI-agnostic, thin-orchestration-wrapper shape as actions.py/production/
actions.py (see CLAUDE.md's "Architecture: actions.py is the one entry
point"). Real logic lives in parser.py/validation.py/engine.py/esi_sync.py;
every function here does load -> call -> ActionError-on-failure -> return
plain dict/dataclass, nothing more.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Optional

from .. import storage
from ..actions import ActionError
from ..auth import InvalidRoleKey, TokenManager, validate_role_key_for_tool
from ..config import ConfigError, OAUTH_CONFIG, save_tenant_config_overrides
from . import contract_market, engine, equivalents, esi_sync, reserve
from .config import DOCTRINE_CONFIG, DoctrineConfig, validate_doctrine_overrides
from .models import ContractHistoryRow, ContractItemRow, ParsedFitting
from .parser import FittingParseError


# ---------------------------------------------------------------------- auth
# No do_auth_doctrine() here, deliberately - ESI login is the Characters
# page (`/api/characters/reauth/start`), not a prefix `/start` and not
# TokenManager.get_token_interactive_multi's old server-side
# webbrowser.open()-plus-local-HTTP-server flow.


def do_list_doctrine_characters() -> list[tuple[str, int, str]]:
    return esi_sync.list_shared_doctrine_characters()


def do_remove_doctrine_character(role_key: str) -> dict:
    try:
        role_key = validate_role_key_for_tool(role_key, "doctrine")
    except InvalidRoleKey as e:
        raise ActionError(str(e)) from e
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


def do_list_doctrine_asset_characters() -> list[tuple[str, int, str]]:
    """Separate character group from do_list_doctrine_characters - see
    esi_sync.py's own module docstring for why asset-scanning characters
    are kept distinct from contract-reading ones."""
    return esi_sync.list_shared_doctrine_asset_characters()


def do_remove_doctrine_asset_character(role_key: str) -> dict:
    try:
        role_key = validate_role_key_for_tool(role_key, "doctrine")
    except InvalidRoleKey as e:
        raise ActionError(str(e)) from e
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


def do_sync_assets() -> dict:
    """Doctrine Stockpile asset sync.

    Track B (2026-09-11, this tenant): 0 doctrine-assets characters; POST
    /api/doctrine/assets/sync returned 400 in 0.002s before any ESI. Not
    migrated to pipeline_runner — re-measure if a real asset-scanner roster
    ever hits a proxy timeout."""
    return esi_sync.sync_assets()


def do_get_asset_sync_time() -> dict:
    return {"synced_at": storage.newest_esi_freshness_success_for_tool("doctrine", ("assets",))}


# ------------------------------------------------------------------ doctrines
def do_create_doctrine(name: str, description: Optional[str] = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ActionError("Doctrine name is required.")
    if any(row[1].lower() == name.lower() for row in storage.list_doctrines()):
        raise ActionError(f"A doctrine named '{name}' already exists.")
    doctrine_id = storage.create_doctrine(name, description)
    return asdict(engine.doctrine_from_row(storage.get_doctrine(doctrine_id)))


def do_update_doctrine(doctrine_id: str, name: Optional[str] = None, description: Optional[str] = None,
                        active: Optional[bool] = None) -> dict:
    if storage.get_doctrine(doctrine_id) is None:
        raise ActionError(f"Doctrine {doctrine_id} not found.")
    updates = {}
    if name is not None:
        name = name.strip()
        if not name:
            raise ActionError("Doctrine name cannot be empty.")
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    if active is not None:
        updates["active"] = active
    storage.update_doctrine(doctrine_id, updates)
    return asdict(engine.doctrine_from_row(storage.get_doctrine(doctrine_id)))


def do_delete_doctrine(doctrine_id: str) -> dict:
    """Cascades app-side in one batch (Phase 2 F): every fitting under this
    doctrine, and everything under each of those (items/issues/contract
    unmatching) - the schema itself has no FK constraints (this app's
    established convention, see phase1_schema.sql), so this orchestration
    is this function's own job, not the database's."""
    if storage.get_doctrine(doctrine_id) is None:
        raise ActionError(f"Doctrine {doctrine_id} not found.")
    with storage.batch_session():
        for row in storage.list_fittings_for_doctrine(doctrine_id):
            fitting_id = str(row[0])
            storage.unmatch_contracts_for_fitting(fitting_id)
            storage.delete_fitting(fitting_id)
        storage.delete_doctrine(doctrine_id)
    return {"deleted": doctrine_id}


# -------------------------------------------------------------------- fittings
def do_parse_fitting(raw_eft: str) -> dict:
    """Preview only - never persists (Phase 2 F/Phase 3 A.7's "paste ->
    review preview -> save" flow)."""
    try:
        parsed = engine.parse_fitting_text(raw_eft)
    except FittingParseError as e:
        raise ActionError(str(e)) from e
    return {
        "hull_type_id": parsed.hull_type_id, "hull_name": parsed.hull_name, "fit_name": parsed.fit_name,
        "items": [asdict(i) for i in parsed.items], "issues": [asdict(i) for i in parsed.issues],
    }


def _merge_bay_items(parsed: ParsedFitting, fuel_bay_text: Optional[str],
                      ship_maintenance_bay_text: Optional[str]) -> tuple[list, list]:
    """GitHub issue #18: Fuel Bay / Ship Maintenance Bay content lists parse
    separately from the main EFT body (parse_fitting has no syntax for
    either - see parser.parse_bay_items' own docstring) and are merged into
    one combined item/issue set here, numbered to continue on from wherever
    the main body's own line numbers left off so a bay-content issue's
    line_no never collides with a main-body one."""
    all_items = list(parsed.items)
    all_issues = list(parsed.issues)
    next_line_no = max((i.line_no for i in all_items), default=0) + 1
    if fuel_bay_text:
        bay_items, bay_issues = engine.parse_bay_items_text(fuel_bay_text, "fuelbay", line_no_start=next_line_no)
        all_items.extend(bay_items)
        all_issues.extend(bay_issues)
        next_line_no = max((i.line_no for i in bay_items), default=next_line_no - 1) + 1
    if ship_maintenance_bay_text:
        bay_items, bay_issues = engine.parse_bay_items_text(
            ship_maintenance_bay_text, "shipmaintenancebay", line_no_start=next_line_no)
        all_items.extend(bay_items)
        all_issues.extend(bay_issues)
    return all_items, all_issues


def do_add_fitting(doctrine_id: str, raw_eft: str, name: Optional[str] = None,
                    variant_label: Optional[str] = None, contract_target: int = 0,
                    stockpile_target: int = 0, cargo_tolerance_pct: Optional[float] = None,
                    fuel_bay_text: Optional[str] = None, ship_maintenance_bay_text: Optional[str] = None) -> dict:
    if storage.get_doctrine(doctrine_id) is None:
        raise ActionError(f"Doctrine {doctrine_id} not found.")
    if contract_target < 0 or stockpile_target < 0:
        raise ActionError("Targets must be zero or greater.")
    try:
        parsed = engine.parse_fitting_text(raw_eft)
    except FittingParseError as e:
        raise ActionError(str(e)) from e
    all_items, all_issues = _merge_bay_items(parsed, fuel_bay_text, ship_maintenance_bay_text)

    with storage.batch_session():
        fitting_id = storage.create_fitting(
            doctrine_id, name or parsed.fit_name, parsed.hull_type_id, raw_eft, variant_label,
            contract_target, stockpile_target, cargo_tolerance_pct, fuel_bay_text, ship_maintenance_bay_text,
        )
        storage.replace_fitting_items(
            fitting_id, [(i.line_no, i.slot_section, i.type_id, i.quantity, i.is_offline) for i in all_items])
        storage.replace_fitting_parse_issues(
            fitting_id, [(i.line_no, i.raw_line, i.issue_kind, i.message) for i in all_issues])

    fitting, _items = engine.load_fitting_with_items(fitting_id)
    return {"fitting": asdict(fitting), "issues": [asdict(i) for i in all_issues]}


def do_update_fitting(fitting_id: str, raw_eft: Optional[str] = None, name: Optional[str] = None,
                       variant_label: Optional[str] = None, contract_target: Optional[int] = None,
                       stockpile_target: Optional[int] = None, cargo_tolerance_pct: Optional[float] = None,
                       active: Optional[bool] = None, fuel_bay_text: Optional[str] = None,
                       ship_maintenance_bay_text: Optional[str] = None,
                       reserve_target: Optional[int] = None) -> dict:
    """Re-parses on a new raw_eft. Re-validates only contracts this change
    can actually affect (see match_and_validate_contract): already-matched
    rows for this fitting always, plus unmatched and same-hull competitors
    when matching-relevant fields change (EFT/bays/name/active). Pure
    stockpile_target / contract_target / variant_label edits skip
    validation — stockpile and ampel are computed live, not persisted on
    the contract snapshot."""
    existing_row = storage.get_fitting(fitting_id)
    if existing_row is None:
        raise ActionError(f"Fitting {fitting_id} not found.")
    if any(t is not None and t < 0 for t in (contract_target, stockpile_target, reserve_target)):
        raise ActionError("Targets must be zero or greater.")

    issues = []
    with storage.batch_session():
        updates: dict = {}
        if name is not None:
            updates["name"] = name
        if variant_label is not None:
            updates["variant_label"] = variant_label
        if contract_target is not None:
            updates["contract_target"] = contract_target
        if stockpile_target is not None:
            updates["stockpile_target"] = stockpile_target
        if reserve_target is not None:
            updates["reserve_target"] = reserve_target
        if cargo_tolerance_pct is not None:
            updates["cargo_tolerance_pct"] = cargo_tolerance_pct
        if active is not None:
            updates["active"] = active

        if raw_eft is not None or fuel_bay_text is not None or ship_maintenance_bay_text is not None:
            existing = engine.fitting_from_row(existing_row)
            effective_raw_eft = raw_eft if raw_eft is not None else existing.raw_eft
            effective_fuel_bay_text = fuel_bay_text if fuel_bay_text is not None else existing.fuel_bay_text
            effective_smb_text = (ship_maintenance_bay_text if ship_maintenance_bay_text is not None
                                   else existing.ship_maintenance_bay_text)
            try:
                parsed = engine.parse_fitting_text(effective_raw_eft)
            except FittingParseError as e:
                raise ActionError(str(e)) from e
            all_items, all_issues = _merge_bay_items(parsed, effective_fuel_bay_text, effective_smb_text)

            updates["raw_eft"] = effective_raw_eft
            updates["hull_type_id"] = parsed.hull_type_id
            updates["fuel_bay_text"] = effective_fuel_bay_text
            updates["ship_maintenance_bay_text"] = effective_smb_text
            storage.replace_fitting_items(
                fitting_id, [(i.line_no, i.slot_section, i.type_id, i.quantity, i.is_offline) for i in all_items])
            storage.replace_fitting_parse_issues(
                fitting_id, [(i.line_no, i.raw_line, i.issue_kind, i.message) for i in all_issues])
            issues = all_issues

        if updates:
            storage.update_fitting(fitting_id, updates)

    matching_changed = any(v is not None for v in (
        raw_eft, fuel_bay_text, ship_maintenance_bay_text, name, active))
    tolerance_changed = cargo_tolerance_pct is not None
    if matching_changed or tolerance_changed:
        existing = engine.fitting_from_row(existing_row)
        hulls = {existing.hull_type_id}
        if updates.get("hull_type_id") is not None:
            hulls.add(updates["hull_type_id"])
        do_validate_contracts(contract_ids=_contract_ids_affected_by_fitting(
            fitting_id, rematch_unmatched=matching_changed, hull_type_ids=hulls))
    fitting, _items = engine.load_fitting_with_items(fitting_id)
    return {"fitting": asdict(fitting), "issues": [asdict(i) for i in issues]}


def do_delete_fitting(fitting_id: str) -> dict:
    """Contracts matched to this fitting persist, unmatched (Phase 2 F -
    never deleted, a contract is real inventory-tracking data independent
    of whichever fitting definition it happened to match)."""
    if storage.get_fitting(fitting_id) is None:
        raise ActionError(f"Fitting {fitting_id} not found.")
    with storage.batch_session():
        storage.unmatch_contracts_for_fitting(fitting_id)
        storage.delete_fitting(fitting_id)
    return {"deleted": fitting_id}


def _type_name(type_id: int) -> str:
    row = storage.get_sde_type(type_id)
    return row[2] if row else str(type_id)


def _character_names_by_role_key() -> dict[str, str]:
    """role_key -> character name for every token this tenant holds, any
    prefix.

    Contract/history rows store the `source_role` that synced them, and
    since the sharing migration that is whatever
    esi_sync.list_shared_doctrine_characters resolved via
    esi_data.selector.select_auth_role - which picks a token by scope, not
    by prefix, so it is routinely `esi:<id>` or another tool's key, not
    `doctrine:<id>`. Building this map from the old prefix-scanning
    list_doctrine_characters() left every such row without a name (blank
    "Character" column, confirmed 2026-09-21). Reading TokenManager's own
    records covers legacy and new keys alike with no union to keep in
    sync."""
    return {
        rec.role: rec.character_name
        for rec in TokenManager(OAUTH_CONFIG).list_records()
        if rec.character_name
    }


def do_get_fitting_detail(fitting_id: str) -> dict:
    # Same not-found guard as do_update_fitting/do_delete_fitting above -
    # confirmed real bug (2026-09-26 pentest follow-up): this was the one
    # fitting_id call site with no existence check before
    # load_fitting_with_items, so any nonexistent fitting_id (a genuinely
    # bad ID, or another tenant's real ID - RLS makes the two
    # indistinguishable here, correctly) hit that function's own
    # LookupError unconverted, surfacing as a bare 500 instead of a clean
    # ActionError-derived 400.
    if storage.get_fitting(fitting_id) is None:
        raise ActionError(f"Fitting {fitting_id} not found.")
    match_pending_contracts()
    fitting, items = engine.load_fitting_with_items(fitting_id)
    issue_rows = storage.load_fitting_parse_issues(fitting_id)
    contracts = engine.contract_rows_from_db(storage.list_doctrine_contracts(fitting_id=fitting_id))
    contracts_with_deviations = []
    for c in contracts:
        dev_rows = storage.load_doctrine_contract_deviations(c.contract_id)
        deviations = [{"type_id": t, "type_name": _type_name(t), "kind": k, "expected_qty": e,
                        "actual_qty": a, "severity": s} for t, k, e, a, s in dev_rows]
        contracts_with_deviations.append({**asdict(c), "deviations": deviations})
    status = engine.fitting_status(fitting)
    return {
        "fitting": asdict(fitting),
        "items": [{**asdict(i), "type_name": _type_name(i.type_id)} for i in items],
        "issues": [{"line_no": ln, "raw_line": rl, "issue_kind": ik, "message": m}
                   for ln, rl, ik, m in issue_rows],
        "contracts": contracts_with_deviations, "status": asdict(status),
    }


def do_get_assemblable(fitting_id: str, cfg: DoctrineConfig = DOCTRINE_CONFIG,
                       target_sets: Optional[int] = None) -> dict:
    """How many complete fits of one fitting the Doctrine stockpile holds
    right now, what stops one more and, with `target_sets`, what is missing
    for that many fits (engine.fitting_assemblable)."""
    if target_sets is not None and target_sets < 1:
        raise ActionError("Target fits must be at least 1.")
    if storage.get_fitting(fitting_id) is None:
        raise ActionError(f"Fitting {fitting_id} not found.")
    return engine.assemblable_for_fitting(fitting_id, cfg, target_sets=target_sets)


def do_get_strategic_reserve(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """The Strategic Reserve tab (GitHub issue #418, reserve.status)."""
    return reserve.status(cfg)


def do_list_reserve_hangars() -> list[dict]:
    return reserve.hangar_options()


def do_find_fittings(query: str) -> list[dict]:
    """Fittings whose id equals `query` or whose name contains it (case
    insensitive); an exact name match wins over partial ones. CLI lookup."""
    rows = [engine.fitting_from_row(r) for d in storage.list_doctrines()
            for r in storage.list_fittings_for_doctrine(str(d[0]))]
    by_id = [f for f in rows if f.fitting_id == query]
    if by_id:
        found = by_id
    else:
        q = query.strip().lower()
        exact = [f for f in rows if f.name.lower() == q]
        found = exact or [f for f in rows if q in f.name.lower()]
    return [{"fitting_id": f.fitting_id, "name": f.name, "doctrine_id": f.doctrine_id} for f in found]


# --------------------------------------------------------------------- sync
def do_sync_contracts(progress_callback=None) -> dict:
    return esi_sync.sync_contracts(progress_callback=progress_callback)


def do_start_sync_contracts() -> dict:
    """Kicks off Doctrine contract sync as a background job. Scheduler still
    calls do_sync_contracts in-process."""
    from .. import pipeline_runner
    return pipeline_runner.start_doctrine_sync()


def do_sync_status() -> dict:
    from .. import pipeline_runner
    # Narrowed to sync_contracts once Contract Market shares TOOL_DOCTRINE
    # (pipeline_runner.job_status docstring / phase 8).
    return pipeline_runner.job_status(
        pipeline_runner.TOOL_DOCTRINE, pipeline_runner.JOB_SYNC_CONTRACTS,
    )


def do_scan_contract_market(system_id: Optional[int] = None, progress_callback=None,
                            cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Scan public contracts in one system (GitHub issue #428).
    Writes only doctrine_market_* tables."""
    if system_id is not None:
        contract_market._system_region(int(system_id))  # an unknown id is never saved
    if system_id is not None and system_id != cfg.contract_market_system_id:
        # Persist the system the user just picked so the next open remembers it.
        do_update_settings({"contract_market_system_id": int(system_id)}, cfg)
        cfg = DOCTRINE_CONFIG  # proxy re-reads after save
    return contract_market.scan_contract_market(
        cfg, system_id=system_id, progress_callback=progress_callback,
    )


def do_start_scan_contract_market(system_id: Optional[int] = None,
                                  cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    from .. import pipeline_runner
    target = system_id if system_id is not None else cfg.contract_market_system_id
    if target:
        contract_market._system_region(int(target))
        contract_market.reject_scan_too_soon(int(target))
    return pipeline_runner.start_contract_market_scan(system_id=system_id)


def do_contract_market_scan_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(
        pipeline_runner.TOOL_DOCTRINE, pipeline_runner.JOB_CONTRACT_MARKET_SCAN,
    )


def do_get_contract_market(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    return contract_market.market_results(cfg)


def _contract_ids_affected_by_fitting(fitting_id: str, rematch_unmatched: bool,
                                       hull_type_ids: set[int]) -> list[int]:
    """Contracts whose match/validation result can change when `fitting_id`
    is edited. Matching uses hull_gate + soll + name tie-break
    (match_and_validate_contract); cargo_tolerance only affects deviations
    after a winner is chosen, so rematch_unmatched=False limits this to
    rows already assigned to this fitting."""
    fitting_id = str(fitting_id)
    competitor_ids = {fitting_id}
    if rematch_unmatched:
        for row in storage.list_active_fittings():
            fitting = engine.fitting_from_row(row)
            if fitting.hull_type_id in hull_type_ids:
                competitor_ids.add(fitting.fitting_id)
    ids: list[int] = []
    seen: set[int] = set()
    for c in engine.contract_rows_from_db(storage.list_doctrine_contracts()):
        matched = str(c.matched_fitting_id) if c.matched_fitting_id else None
        take = matched == fitting_id
        if rematch_unmatched:
            take = take or matched is None or matched in competitor_ids
        if take and c.contract_id not in seen:
            seen.add(c.contract_id)
            ids.append(c.contract_id)
    return ids


def do_validate_contracts(cfg: DoctrineConfig = DOCTRINE_CONFIG,
                           contract_ids: Optional[Iterable[int]] = None) -> dict:
    """Re-matches + re-validates persisted contracts against current Fitting
    definitions, without touching ESI (Phase 2 F). Used after editing a
    fitting (do_update_fitting, scoped to affected contract_ids) and as its
    own standalone action for a full re-run (`contract_ids` None).

    replace_doctrine_sync_snapshot is partitioned by owner; this path still
    writes every loaded contract through (grouped by source_role when owner
    ids are omitted) so untouched contracts are not dropped."""
    candidates = engine.load_match_candidates()
    contracts = engine.contract_rows_from_db(storage.list_doctrine_contracts())
    target_ids = set(contract_ids) if contract_ids is not None else None
    revalidated = 0
    contract_rows: list[tuple] = []
    item_rows: list[tuple] = []
    deviation_rows: list[tuple] = []
    with storage.batch_session():
        for c in contracts:
            items_raw = storage.load_doctrine_contract_items(c.contract_id)
            items = [ContractItemRow(c.contract_id, rid, tid, qty, bool(incl), bool(single))
                     for rid, tid, qty, incl, single in items_raw]
            if target_ids is None or c.contract_id in target_ids:
                matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(
                    c.contract_id, c.title, items, candidates, cfg)
                revalidated += 1
                for d in deviations:
                    deviation_rows.append(
                        (c.contract_id, d.type_id, d.kind, d.expected_qty, d.actual_qty, d.severity))
            else:
                matched_fitting_id, score, status = c.matched_fitting_id, c.match_score, c.validation_status
                for t, k, e, a, s in storage.load_doctrine_contract_deviations(c.contract_id):
                    deviation_rows.append((c.contract_id, t, k, e, a, s))
            contract_rows.append((
                c.contract_id, c.source_role, c.for_corporation, c.issuer_id, c.start_location_id,
                c.status, c.title, c.price, c.date_expired, matched_fitting_id, score, status, c.synced_at,
            ))
            for it in items:
                item_rows.append((c.contract_id, it.record_id, it.type_id, it.quantity, it.is_included,
                                   it.is_singleton))
        storage.replace_doctrine_sync_snapshot(contract_rows, item_rows, deviation_rows)
    return {"revalidated": revalidated}


# ------------------------------------------------------------------- status
def match_pending_contracts(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> int:
    """Matches every stored contract that was never matched yet and returns
    how many. The ESI contracts fetcher (esi_data/fetchers.py) stores a new
    or status-changed contract with no match score, and only the Doctrine
    sync re-runs matching. Contracts fetched any other way - the scheduler's
    esi_data_sync, or a corp owner the Doctrine sync skipped because another
    sync already had it in flight - stayed "unmatched" until the next manual
    sync. Every Doctrine read calls this first, so they are matched on the
    next page load instead. A never-matched contract is the only kind with
    a NULL score: matching always stores a number, 0.0 for no hull."""
    pending = [c for c in engine.contract_rows_from_db(storage.list_doctrine_contracts())
               if c.match_score is None]
    if not pending:
        return 0
    candidates = engine.load_match_candidates()
    matches: list[tuple] = []
    deviation_rows: list[tuple] = []
    for c in pending:
        items = [ContractItemRow(c.contract_id, rid, tid, qty, bool(incl), bool(single))
                 for rid, tid, qty, incl, single in storage.load_doctrine_contract_items(c.contract_id)]
        fitting_id, score, deviations, status = engine.match_and_validate_contract(
            c.contract_id, c.title, items, candidates, cfg)
        matches.append((c.contract_id, fitting_id, score, status))
        deviation_rows.extend((c.contract_id, d.type_id, d.kind, d.expected_qty, d.actual_qty, d.severity)
                              for d in deviations)
    storage.update_doctrine_contract_matches(matches, deviation_rows)
    return len(matches)


def do_get_doctrine_status(doctrine_id: Optional[str] = None) -> dict:
    match_pending_contracts()
    if doctrine_id is not None:
        row = storage.get_doctrine(doctrine_id)
        if row is None:
            raise ActionError(f"Doctrine {doctrine_id} not found.")
        return {"doctrines": [asdict(engine.doctrine_status(row))]}
    return {"doctrines": [asdict(engine.doctrine_status(row)) for row in storage.list_doctrines()]}


def _location_options(location_ids: Iterable[int]) -> list[dict]:
    """[{location_id, name}] in the given order, names through the one
    location reader (None when unknown)."""
    ids = [int(i) for i in location_ids]
    if not ids:
        return []
    from ..locations import reader
    info = reader.describe(ids)
    return [{"location_id": i, "name": info[i].name if i in info else None} for i in ids]


def do_get_stockpile_status(doctrine_id: Optional[str] = None, location_id: Optional[int] = None,
                            cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """`location_id` narrows Available to one stockpile location; Required
    stays the whole need (user decision 2026-11-04)."""
    locations = cfg.effective_stockpile_location_ids
    if location_id is not None and location_id not in locations:
        raise ActionError(f"Location {location_id} is not a Doctrine stockpile location.")
    match_pending_contracts(cfg)
    rows, assets_available = engine.stockpile_rows_for_doctrine(doctrine_id, cfg, location_id=location_id)
    aggregated_rows = engine.aggregate_stockpile_rows(rows)
    return {
        "rows": [asdict(r) for r in rows],
        "aggregated_rows": [asdict(r) for r in aggregated_rows],
        "assets_available": assets_available,
        "location_id": location_id,
        "locations": _location_options(locations),
    }


def do_get_shopping_list(doctrine_id: Optional[str] = None, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    match_pending_contracts(cfg)
    rows, warnings = engine.shopping_list(doctrine_id, cfg)
    return {"rows": [asdict(r) for r in rows], "warnings": warnings,
            "buyers_configured": bool(cfg.buyer_character_ids)}


def do_list_contracts(fitting_id: Optional[str] = None, status: Optional[str] = None,
                      location_id: Optional[int] = None) -> dict:
    """`location_id` keeps only contracts issued at that structure."""
    match_pending_contracts()
    contracts = engine.contract_rows_from_db(storage.list_doctrine_contracts(fitting_id=fitting_id, status=status))
    if location_id is not None:
        contracts = [c for c in contracts if c.start_location_id == location_id]
    location_names = {o["location_id"]: o["name"] for o in _location_options(
        sorted({c.start_location_id for c in contracts if c.start_location_id is not None}))}

    # source_role is an ESI-token role key - resolve it to the character's
    # actual name for display rather than leaving the raw string on screen.
    character_names = _character_names_by_role_key()
    # Hull comes from the matched fitting, if any - an unmatched contract
    # doesn't authoritatively identify which of possibly-several relevant
    # hulls it's for, so it stays blank rather than guessing from its items.
    hull_by_fitting_id = {}
    for row in storage.list_active_fittings():
        fitting = engine.fitting_from_row(row)
        hull_by_fitting_id[fitting.fitting_id] = (fitting.hull_type_id, _type_name(fitting.hull_type_id))

    rows = []
    for c in contracts:
        # No doctrine hull at all: not a doctrine ship sale. The row stays
        # stored so it is not re-matched on every read, but it is not listed.
        if c.validation_status == engine.NO_HULL_MATCH:
            continue
        hull_type_id, hull_name = hull_by_fitting_id.get(c.matched_fitting_id, (None, None))
        rows.append({**asdict(c), "source_character_name": character_names.get(c.source_role),
                     "hull_type_id": hull_type_id, "hull_name": hull_name,
                     "start_location_name": location_names.get(c.start_location_id)})
    return {"rows": rows}


def _iso(value) -> Optional[str]:
    """storage.load_doctrine_contract_history's date_issued/date_completed
    come back from Postgres as tz-aware datetime objects (TIMESTAMPTZ
    columns), not the ISO strings every other timestamp in this app is
    already a string by the time storage returns it (see e.g. esi_sync.py's
    own `datetime.now(timezone.utc).isoformat()` calls, which store the
    string form up front) - confirmed real bug caught live: the API layer's
    ContractHistoryRow schema declares these `Optional[str]`, and FastAPI's
    response validation rejects a raw datetime outright (500, not a silent
    wrong value)."""
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def do_contract_history() -> dict:
    """GitHub issue #19 - "which contracts were sold to who and when".
    fitting_name/hull_type_id are already denormalized on the stored row
    (see storage.upsert_doctrine_contract_history's own docstring) - only
    hull_name (SDE type names, stable reference data - unlike a tenant's own
    fittings, never edited/deleted out from under a history row) and
    source_character_name (same role-key resolution do_list_contracts above
    already does) need resolving here."""
    rows = storage.load_doctrine_contract_history()
    character_names = _character_names_by_role_key()
    result = []
    for (contract_id, source_role, fitting_id, fitting_name, hull_type_id, title, price, acceptor_id,
         acceptor_name, date_issued, date_completed) in rows:
        result.append(ContractHistoryRow(
            contract_id=contract_id, source_role=source_role, fitting_id=str(fitting_id) if fitting_id else None,
            fitting_name=fitting_name,
            hull_type_id=hull_type_id, hull_name=_type_name(hull_type_id) if hull_type_id is not None else None,
            title=title, price=price, acceptor_id=acceptor_id, acceptor_name=acceptor_name,
            date_issued=_iso(date_issued), date_completed=_iso(date_completed),
            source_character_name=character_names.get(source_role),
        ))
    return {"rows": [asdict(r) for r in result]}


def do_get_esi_sync_time() -> dict:
    # esi_freshness (see production.actions.do_get_esi_sync_time).
    return {"synced_at": storage.newest_esi_freshness_success_for_tool("doctrine", ("contracts",))}


# ------------------------------------------------------------------ settings
def do_list_buyer_character_options() -> list[dict]:
    """Every character holding a token in this tenant, with whether it
    already shares what the Shopping List's buyer sources read
    (buyer_sources.py) with Doctrine. Token-store listing, no live ESI."""
    from ..esi_data.access import is_shared

    names: dict[int, str] = {}
    for rec in TokenManager(OAUTH_CONFIG).list_records():
        if rec.character_name or rec.character_id not in names:
            names[rec.character_id] = rec.character_name or str(rec.character_id)
    return [
        {"character_id": cid, "character_name": name,
         "assets_shared": is_shared("assets", "doctrine", "character", cid),
         "market_orders_shared": is_shared("market_orders", "doctrine", "character", cid)}
        for cid, name in sorted(names.items(), key=lambda kv: kv[1].lower())
    ]


def do_get_settings(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Settings page values. The two location lists come back *effective*,
    so a legacy single id shows up pre-filled; Trading's structure is only a
    fallback and is reported separately, never as a saved entry."""
    stored_structures = cfg.doctrine_structure_ids or (
        (cfg.doctrine_structure_id,) if cfg.doctrine_structure_id else ())
    stored_stockpile = cfg.stockpile_location_ids or (
        (cfg.stockpile_location_id,) if cfg.stockpile_location_id else ())
    return {
        "doctrine_structure_ids": [int(s) for s in stored_structures],
        "stockpile_location_ids": [int(s) for s in stored_stockpile],
        "effective_structure_ids": list(cfg.effective_structure_ids),
        "effective_stockpile_location_ids": list(cfg.effective_stockpile_location_ids),
        "contract_structures": _location_options(cfg.effective_structure_ids),
        "stockpile_locations": _location_options(cfg.effective_stockpile_location_ids),
        "cargo_tolerance_pct": cfg.cargo_tolerance_pct,
        "strict_extras": cfg.strict_extras,
        "accept_equivalent_variants": cfg.accept_equivalent_variants,
        "import_cost_per_m3": cfg.import_cost_per_m3,
        "hub_region_id": cfg.hub_region_id,
        "stockpile_hangar_flags": list(cfg.stockpile_hangar_flags),
        "buyer_character_ids": list(cfg.buyer_character_ids),
        "strategic_reserve_owner_type": cfg.strategic_reserve_owner_type,
        "strategic_reserve_owner_id": cfg.strategic_reserve_owner_id,
        "strategic_reserve_location_id": cfg.strategic_reserve_location_id,
        "strategic_reserve_hangar_flag": cfg.strategic_reserve_hangar_flag,
        "strategic_reserve_container_id": cfg.strategic_reserve_container_id,
        "strategic_reserve_include_hulls": cfg.strategic_reserve_include_hulls,
        "contract_market_system_id": cfg.contract_market_system_id,
    }


_META_GROUP_LABELS = {1: "Tech I", 2: "Tech II", 3: "Storyline", 4: "Faction", 5: "Officer", 6: "Deadspace",
                      14: "Tech III", 19: "Limited", 52: "Structure Faction", 53: "Structure Tech II",
                      54: "Structure Tech I"}


def _fitted_type_ids() -> set[int]:
    fitted: set[int] = set()
    for candidate in engine.load_match_candidates():
        fitted.add(candidate.fitting.hull_type_id)
        fitted.update(item.type_id for item in candidate.items)
    return fitted


def do_list_equivalent_variants(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """The Equivalents tab: every class of functionally identical variants
    (SDE classes plus the tenant's manual ones, doctrine/equivalents.py) with
    each member's current Jita and C-J sell price and the cheapest member per
    market marked. Members the tenant excluded and classes it switched off
    are listed too (flagged), so they can be switched back on.

    Prices: Jita is Goonmetrics' Jita quote (one cached dump for every
    item); C-J is production.pricing.home_prices (the live structure order
    book, or Goonmetrics' C-J quote without a market character). A C-J quote
    equal to the Jita quote to the last decimal is Goonmetrics repeating
    Jita for an item C-J does not list, so it counts as no C-J price."""
    from ..production import pricing as production_pricing
    from ..production.config import PRODUCTION_CONFIG

    sde_class_of = storage.get_sde_equivalence_class_map()
    overrides = storage.list_doctrine_equivalent_overrides()
    raw_class_of = equivalents.assigned_classes(sde_class_of, overrides)
    disabled_types = storage.list_doctrine_disabled_equivalent_groups()
    disabled = {raw_class_of[t] for t in disabled_types if t in raw_class_of}
    manual = {t for t, a, x in overrides if not x and a is not None}
    excluded = {t for t, _a, x in overrides if x}

    members: dict[int, set[int]] = {}
    for type_id, class_id in raw_class_of.items():
        members.setdefault(class_id, set()).add(type_id)
    for type_id in excluded:
        if type_id in sde_class_of:
            members.setdefault(sde_class_of[type_id], set()).add(type_id)
    type_ids = sorted({t for ts in members.values() for t in ts})
    sde_rows = storage.get_sde_types_bulk(type_ids)
    group_names = storage.get_group_names({r[1] for r in sde_rows.values() if r})
    fitted = _fitted_type_ids()
    jita = production_pricing.goonmetrics_quotes(production_pricing.JITA_MARKET, type_ids) if type_ids else {}
    home = production_pricing.home_prices(PRODUCTION_CONFIG, type_ids) if type_ids else {}

    def _sell(quotes: dict, type_id: int) -> Optional[float]:
        quote = production_pricing.reference_quote(quotes.get(type_id))
        return quote.sell if quote is not None and quote.sell > 0 else None

    classes = []
    for class_id, ts in members.items():
        rows = []
        for type_id in ts:
            sde = sde_rows.get(type_id)
            jita_sell = _sell(jita, type_id)
            home_sell = _sell(home, type_id)
            rows.append({
                "type_id": type_id, "type_name": sde[2] if sde else str(type_id),
                "meta_group": _META_GROUP_LABELS.get(sde[7]) if sde else None,
                "in_fitting": type_id in fitted, "excluded": type_id in excluded,
                "manual": type_id in manual,
                "jita_sell": jita_sell,
                "home_sell": None if home_sell is not None and home_sell == jita_sell else home_sell,
                "home_repeats_jita": home_sell is not None and home_sell == jita_sell,
            })
        active_rows = [r for r in rows if not r["excluded"]]
        for key, flag in (("jita_sell", "cheapest_jita"), ("home_sell", "cheapest_home")):
            priced = [r for r in active_rows if r[key] is not None]
            best = min(priced, key=lambda r: r[key])["type_id"] if len(priced) > 1 else None
            for r in rows:
                r[flag] = r["type_id"] == best
        rows.sort(key=lambda r: (r["excluded"], r["type_name"]))
        group_id = next((sde_rows[t][1] for t in ts if sde_rows.get(t)), None)
        jita_prices = [r["jita_sell"] for r in active_rows if r["jita_sell"] is not None]
        classes.append({
            "class_id": class_id, "group_name": group_names.get(group_id, ""),
            # Any current member: the group's handle for edits (class ids
            # change with every SDE update, item ids do not).
            "anchor_type_id": next((r["type_id"] for r in rows if not r["excluded"]), rows[0]["type_id"]),
            "disabled": class_id in disabled, "manual": class_id < 0 or any(r["manual"] for r in rows),
            "active": class_id not in disabled and len(active_rows) > 1,
            "in_fitting": any(r["in_fitting"] for r in rows),
            "jita_saving": (max(jita_prices) - min(jita_prices)) if len(jita_prices) > 1 else 0.0,
            "members": rows,
        })
    classes.sort(key=lambda c: (c["group_name"], c["members"][0]["type_name"] if c["members"] else ""))
    return {"enabled": cfg.accept_equivalent_variants, "classes": classes}


def _after_equivalence_change(cfg: DoctrineConfig) -> dict:
    """Drops the cached classes and re-validates the stored contracts (no
    ESI call) so their status reflects the edit right away."""
    equivalents.invalidate()
    return do_validate_contracts(cfg)


def do_set_equivalent_excluded(type_id: int, excluded: bool, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Takes a type out of its equivalence class (excluded) or puts it back
    (removes the tenant's edit for it, which also undoes a manual add)."""
    if excluded:
        if equivalents.effective_class_of(type_id) is None and type_id not in storage.get_sde_equivalence_class_map():
            raise ActionError(f"Type {type_id} is in no equivalence group.")
        storage.set_doctrine_equivalent_override(type_id, None, True)
    else:
        storage.delete_doctrine_equivalent_override(type_id)
    return _after_equivalence_change(cfg)


def do_set_equivalent_group_disabled(type_id: int, disabled: bool,
                                     cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Switches the group that contains `type_id` off or on again."""
    raw_class_of = equivalents.assigned_classes(
        storage.get_sde_equivalence_class_map(), storage.list_doctrine_equivalent_overrides())
    class_id = raw_class_of.get(int(type_id))
    if class_id is None:
        raise ActionError(f"Type {type_id} is in no equivalence group.")
    members = [t for t, c in raw_class_of.items() if c == class_id]
    if not disabled:
        # Also clear rows of former members (an SDE update may have moved them).
        members += [t for t in storage.list_doctrine_disabled_equivalent_groups()
                    if raw_class_of.get(t) in (class_id, None)]
    storage.set_doctrine_equivalent_group_disabled(members, disabled)
    return _after_equivalence_change(cfg)


def do_add_equivalent(type_id: int, target_type_id: int, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Puts `type_id` into the equivalence group of `target_type_id` (the
    user judges them interchangeable although the SDE stats differ). Both
    must be in the same SDE group. When the target has no group yet, a new
    manual group holds both."""
    if type_id == target_type_id:
        raise ActionError("Pick two different items.")
    a, b = storage.get_sde_type(type_id), storage.get_sde_type(target_type_id)
    if a is None or b is None:
        raise ActionError("Unknown item.")
    if a[1] != b[1]:
        raise ActionError(f"{a[2]} and {b[2]} are in different item groups and cannot replace each other.")
    storage.set_doctrine_equivalent_override(type_id, int(target_type_id), False)
    return _after_equivalence_change(cfg)


def do_search_equivalent_candidates(target_type_id: int, query: str, limit: int = 20) -> list[dict]:
    """Items of the target's SDE group whose name contains `query` - the
    picker for "add item to this group"."""
    target = storage.get_sde_type(target_type_id)
    if target is None:
        raise ActionError("Unknown item.")
    return [{"type_id": t, "type_name": n}
            for t, n in storage.search_published_types_in_group(target[1], query, limit)
            if t != target_type_id]


def _reject_intake_hangar(updates: dict) -> None:
    """The reserve must not be a Sorting intake source: that hangar is staging
    for other tools, while the reserve's contents do not exist for them."""
    from ..production.config import PRODUCTION_CONFIG

    if (not updates.get("strategic_reserve_hangar_flag")
            or updates.get("strategic_reserve_location_id") != PRODUCTION_CONFIG.home_location_id):
        return
    owner = next((o["owner_name"] for o in reserve.hangar_options()
                  if o["owner_type"] == updates.get("strategic_reserve_owner_type")
                  and o["owner_id"] == updates.get("strategic_reserve_owner_id")), None)
    for _id, _kind, owner_name, flag, _label in storage.load_sorting_intake_sources():
        if owner_name == owner and flag == updates["strategic_reserve_hangar_flag"]:
            raise ActionError("This hangar is a Sorting intake source, so it cannot be the strategic reserve.")


def _reject_foreign_container(updates: dict, cfg: DoctrineConfig) -> None:
    """A newly chosen container must lie in the chosen hangar. An unchanged one
    is not rechecked: once it is gone, other settings must stay saveable."""
    container = updates.get("strategic_reserve_container_id")
    if container is None or container == cfg.strategic_reserve_container_id:
        return
    for option in reserve.hangar_options():
        if (option["owner_type"] == updates.get("strategic_reserve_owner_type")
                and option["owner_id"] == updates.get("strategic_reserve_owner_id")
                and option["location_id"] == updates.get("strategic_reserve_location_id")
                and option["hangar_flag"] == updates.get("strategic_reserve_hangar_flag")
                and any(c["item_id"] == container for c in option["containers"])):
            return
    raise ActionError("That container is not in the chosen hangar.")


def _reserve_changed(updates: dict, cfg: DoctrineConfig) -> bool:
    return any(key in updates and updates[key] != getattr(cfg, key) for key in reserve.HANGAR_FIELDS)


def _invalidate_after_reserve_change() -> None:
    """Everything derived from stock Production or the Asset Manager can read."""
    from ..asset_manager.actions import invalidate_cache as invalidate_asset_cache
    from ..production.actions import _mark_cached_production_plans_stale
    from ..production.engine import invalidate_discover_cache, invalidate_ship_margin_cache

    reserve.invalidate_cache()
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    invalidate_asset_cache(storage.get_current_tenant())
    _mark_cached_production_plans_stale()


def do_update_settings(updates: dict, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    updates = dict(updates)
    # Saving a list moves the tenant off the legacy single id for good.
    if "doctrine_structure_ids" in updates:
        updates["doctrine_structure_ids"] = list(dict.fromkeys(updates["doctrine_structure_ids"] or ()))
        updates["doctrine_structure_id"] = None
    if "stockpile_location_ids" in updates:
        updates["stockpile_location_ids"] = list(dict.fromkeys(updates["stockpile_location_ids"] or ()))
        updates["stockpile_location_id"] = None
    if updates.get("buyer_character_ids"):
        known = {o["character_id"] for o in do_list_buyer_character_options()}
        unknown = [c for c in updates["buyer_character_ids"] if c not in known]
        if unknown:
            raise ActionError(f"buyer_character_ids: {unknown} are not characters of this tenant.")
    switch_changed = ("accept_equivalent_variants" in updates
                      and bool(updates["accept_equivalent_variants"]) != cfg.accept_equivalent_variants)
    reserve_changed = _reserve_changed(updates, cfg)
    _reject_intake_hangar(updates)
    _reject_foreign_container(updates, cfg)
    try:
        validate_doctrine_overrides(updates)  # stockpile_hangar_flags enum check - see its docstring
        save_tenant_config_overrides("doctrine", updates, cfg, cfg_type=DoctrineConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    if switch_changed:
        do_validate_contracts(cfg)  # stored contract statuses follow the switch at once
    if reserve_changed:
        _invalidate_after_reserve_change()
    return {"updated": list(updates.keys())}
