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
from typing import Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import ConfigError, OAUTH_CONFIG, save_tenant_config_overrides
from . import engine, esi_sync
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .models import ContractHistoryRow, ContractItemRow, ParsedFitting
from .parser import FittingParseError


# ---------------------------------------------------------------------- auth
# No do_auth_doctrine() here, deliberately - a doctrine character logs in via
# the same redirect-based EVE SSO flow buyer/seller/producer already use
# (GET /api/auth/doctrine/start -> EVE SSO -> /api/auth/callback, see
# api/routers/auth.py's _scopes_for and DoctrineLayout.tsx's CharacterList).
# A do_* action here would need TokenManager.get_token_interactive_multi's
# old server-side webbrowser.open()-plus-local-HTTP-server flow, which binds
# its own listener on the exact host:port the running backend already uses -
# confirmed real bug live: works only by accident in local dev, always fails
# on a real deployment ("Cannot assign requested address").


def do_list_doctrine_characters() -> list[tuple[str, int, str]]:
    return esi_sync.list_doctrine_characters()


def do_remove_doctrine_character(role_key: str) -> dict:
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


def do_list_doctrine_asset_characters() -> list[tuple[str, int, str]]:
    """Separate character group from do_list_doctrine_characters - see
    esi_sync.py's own module docstring for why asset-scanning characters
    are kept distinct from contract-reading ones."""
    return esi_sync.list_doctrine_asset_characters()


def do_remove_doctrine_asset_character(role_key: str) -> dict:
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


def do_sync_assets() -> dict:
    return esi_sync.sync_assets()


def do_get_asset_sync_time() -> dict:
    return {"synced_at": storage.get_esi_sync_time("doctrine_assets")}


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
    """Preview only - never persists (Phase 2 F/Phase 3 A.7's "einfügen ->
    Vorschau prüfen -> speichern" flow)."""
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
                       ship_maintenance_bay_text: Optional[str] = None) -> dict:
    """Re-parses on a new raw_eft, then always re-validates this fitting's
    already-persisted contracts (Phase 2 F: changed Soll data must never
    leave a stale validation result standing - same invalidation discipline
    as production/engine.py's invalidate_discover_cache).

    GitHub issue #18: raw_eft/fuel_bay_text/ship_maintenance_bay_text share
    replace_fitting_items' own wholesale-replace semantics - there's one
    combined item set per fitting, not one per text field, so editing *any*
    of the three re-parses *all* of them together, falling back to whatever
    is already stored for the ones this call didn't touch."""
    existing_row = storage.get_fitting(fitting_id)
    if existing_row is None:
        raise ActionError(f"Fitting {fitting_id} not found.")
    if (contract_target is not None and contract_target < 0) or (stockpile_target is not None and stockpile_target < 0):
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

    do_validate_contracts()
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


def do_get_fitting_detail(fitting_id: str) -> dict:
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


# --------------------------------------------------------------------- sync
def do_sync_contracts() -> dict:
    return esi_sync.sync_contracts()


def do_validate_contracts(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Re-matches + re-validates every persisted contract against the
    current Fitting definitions, without touching ESI (Phase 2 F) - used
    after editing a fitting (do_update_fitting) and as its own standalone
    action for "I only changed targets/tolerance, no need to re-sync"."""
    candidates = engine.load_match_candidates()
    contracts = engine.contract_rows_from_db(storage.list_doctrine_contracts())
    revalidated = 0
    contract_rows: list[tuple] = []
    item_rows: list[tuple] = []
    deviation_rows: list[tuple] = []
    with storage.batch_session():
        for c in contracts:
            items_raw = storage.load_doctrine_contract_items(c.contract_id)
            items = [ContractItemRow(c.contract_id, rid, tid, qty, bool(incl), bool(single))
                     for rid, tid, qty, incl, single in items_raw]
            matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(
                c.contract_id, c.title, items, candidates, cfg)
            contract_rows.append((
                c.contract_id, c.source_role, c.for_corporation, c.issuer_id, c.start_location_id,
                c.status, c.title, c.price, c.date_expired, matched_fitting_id, score, status, c.synced_at,
            ))
            for it in items:
                item_rows.append((c.contract_id, it.record_id, it.type_id, it.quantity, it.is_included,
                                   it.is_singleton))
            for d in deviations:
                deviation_rows.append((c.contract_id, d.type_id, d.kind, d.expected_qty, d.actual_qty, d.severity))
            revalidated += 1
        storage.replace_doctrine_sync_snapshot(contract_rows, item_rows, deviation_rows)
    return {"revalidated": revalidated}


# ------------------------------------------------------------------- status
def do_get_doctrine_status(doctrine_id: Optional[str] = None) -> dict:
    if doctrine_id is not None:
        row = storage.get_doctrine(doctrine_id)
        if row is None:
            raise ActionError(f"Doctrine {doctrine_id} not found.")
        return {"doctrines": [asdict(engine.doctrine_status(row))]}
    return {"doctrines": [asdict(engine.doctrine_status(row)) for row in storage.list_doctrines()]}


def do_get_stockpile_status(doctrine_id: Optional[str] = None) -> dict:
    rows, assets_available = engine.stockpile_rows_for_doctrine(doctrine_id)
    aggregated_rows = engine.aggregate_stockpile_rows(rows)
    return {
        "rows": [asdict(r) for r in rows],
        "aggregated_rows": [asdict(r) for r in aggregated_rows],
        "assets_available": assets_available,
    }


def do_get_shopping_list(doctrine_id: Optional[str] = None, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    return {"rows": [asdict(r) for r in engine.shopping_list_rows(doctrine_id, cfg)]}


def do_list_contracts(fitting_id: Optional[str] = None, status: Optional[str] = None) -> dict:
    contracts = engine.contract_rows_from_db(storage.list_doctrine_contracts(fitting_id=fitting_id, status=status))

    # source_role is an ESI-token role key ("doctrine:<character_id>", see
    # esi_sync.DOCTRINE_ROLE_PREFIX) - resolve it to the character's actual
    # name for display rather than leaving the raw string on screen.
    character_names = {role_key: name for role_key, _character_id, name in esi_sync.list_doctrine_characters()}
    # Hull comes from the matched fitting, if any - an unmatched contract
    # doesn't authoritatively identify which of possibly-several relevant
    # hulls it's for, so it stays blank rather than guessing from its items.
    hull_by_fitting_id = {}
    for row in storage.list_active_fittings():
        fitting = engine.fitting_from_row(row)
        hull_by_fitting_id[fitting.fitting_id] = (fitting.hull_type_id, _type_name(fitting.hull_type_id))

    rows = []
    for c in contracts:
        hull_type_id, hull_name = hull_by_fitting_id.get(c.matched_fitting_id, (None, None))
        rows.append({**asdict(c), "source_character_name": character_names.get(c.source_role),
                     "hull_type_id": hull_type_id, "hull_name": hull_name})
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
    character_names = {role_key: name for role_key, _character_id, name in esi_sync.list_doctrine_characters()}
    result = []
    for (contract_id, source_role, fitting_id, fitting_name, hull_type_id, title, price, acceptor_id,
         acceptor_name, date_issued, date_completed) in rows:
        result.append(ContractHistoryRow(
            contract_id=contract_id, source_role=source_role, fitting_id=fitting_id, fitting_name=fitting_name,
            hull_type_id=hull_type_id, hull_name=_type_name(hull_type_id) if hull_type_id is not None else None,
            title=title, price=price, acceptor_id=acceptor_id, acceptor_name=acceptor_name,
            date_issued=_iso(date_issued), date_completed=_iso(date_completed),
            source_character_name=character_names.get(source_role),
        ))
    return {"rows": [asdict(r) for r in result]}


def do_get_esi_sync_time() -> dict:
    return {"synced_at": storage.get_esi_sync_time("doctrine")}


# ------------------------------------------------------------------ settings
def do_update_settings(updates: dict, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    try:
        save_tenant_config_overrides("doctrine", updates, cfg, cfg_type=DoctrineConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return {"updated": list(updates.keys())}
