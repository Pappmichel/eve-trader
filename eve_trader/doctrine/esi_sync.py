"""ESI sync for the Doctrine tool - two wrappers around the Phase 3
orchestrator, plus tool-specific post-processing:

1. Contract sync: `do_sync_for_tool("doctrine")` writes the snapshot
   (structure pre-filter happens in the contracts fetcher before the
   per-contract items call). Matching against fittings and finished-
   contract history stay here.
2. Asset sync: the same `do_sync_for_tool("doctrine")` call writes
   character_assets / corp_assets. Sharing with `tool_key="doctrine"`
   is what keeps a Doctrine-only tenant's stockpile independent of
   Production (the property the doctrine asset tables existed for).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .. import storage
from ..actions import ActionError, _emit_progress
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import CONTRACT_TYPE_ITEM_EXCHANGE, FINISHED_CONTRACT_STATUSES, SYNCABLE_CONTRACT_STATUSES

log = logging.getLogger("eve_trader.doctrine.esi_sync")

DOCTRINE_ROLE_PREFIX = "doctrine"
DOCTRINE_SCOPES = [
    "esi-contracts.read_character_contracts.v1",
    "esi-contracts.read_corporation_contracts.v1",
    "esi-universe.read_structures.v1",   # structure name resolution, same as Production
]

# Separate role/scope group from DOCTRINE_ROLE_PREFIX above - see this
# module's own docstring point 2 for why asset-scanning characters are kept
# distinct from contract-reading ones.
DOCTRINE_ASSET_ROLE_PREFIX = "doctrine-assets"
DOCTRINE_ASSET_SCOPES = [
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
]


def list_doctrine_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """Returns (role_key, character_id, character_name) for every registered
    doctrine character - same get_record (no refresh) pattern as
    production/esi_sync.py's list_producer_characters, for the same reason
    (this runs on every "characters" sidebar render, one dead token must
    not take the whole list down)."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(DOCTRINE_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
    return out


def _passes_history_filter(contract: dict, structure_id: Optional[int]) -> bool:
    """Same shape as _passes_prefilter below, for FINISHED_CONTRACT_STATUSES
    instead of SYNCABLE_CONTRACT_STATUSES (GitHub issue #19) - a finished
    contract is about to drop out of the *active* snapshot (see
    _passes_prefilter's own SYNCABLE_CONTRACT_STATUSES, which doesn't
    include "finished"), so this is checked separately, over the same
    already-fetched raw contract list, to catch it for doctrine_contract_
    history before that happens."""
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") in FINISHED_CONTRACT_STATUSES
        and structure_id is not None
        and contract.get("start_location_id") == structure_id
    )


def _passes_prefilter(contract: dict, structure_id: Optional[int]) -> bool:
    """Phase 2 E.3 point 2 - applied before any items fetch, never after.
    Deliberately no date_expired/"is this actually still alive" check here
    anymore - SYNCABLE_CONTRACT_STATUSES (ESI's own status field) is now the
    single authoritative signal for that, see its own comment for why."""
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") in SYNCABLE_CONTRACT_STATUSES
        and structure_id is not None
        and contract.get("start_location_id") == structure_id
    )


def _issued_by_own_identity(contract: dict, character_id: int, corporation_id: Optional[int]) -> bool:
    """ESI's character-contracts endpoint returns every contract the
    character is issuer, acceptor, OR ASSIGNEE of (confirmed via esi_client.
    ESIClient.character_contracts' own docstring) - a public/third-party
    contract merely *assigned* to this character or their corp (never
    actually created by them) would otherwise leak into the Doctrine match
    pool. Only contracts this character (or their own corp) actually issued
    are relevant - `issuer_corporation_id` is transient here, never stored
    (_CONTRACT_COLUMNS keeps its existing issuer_id-only shape)."""
    return contract.get("issuer_id") == character_id or (
        corporation_id is not None and contract.get("issuer_corporation_id") == corporation_id
    )


def sync_contracts(cfg: DoctrineConfig = DOCTRINE_CONFIG, progress_callback=None) -> dict:
    """Fetch is the Phase 3 orchestrator (`do_sync_for_tool("doctrine")`).
    Matching against fittings and finished-contract history stay here —
    they are tool-specific, not owned-data writes."""
    from ..esi_data.orchestrator import do_sync_for_tool
    from .actions import do_validate_contracts

    structure_id = cfg.effective_structure_id
    if structure_id is None:
        raise ActionError(
            "No structure configured for Doctrine contract sync. Set it in Doctrine Settings "
            "(or Trading's own structure_id, which this falls back to)."
        )

    tm = TokenManager(OAUTH_CONFIG)
    characters = list_doctrine_characters(tm)
    if not characters:
        raise ActionError("No doctrine character logged in yet. Use 'Add Character' in the sidebar.")

    existing_by_id = {row[0]: row for row in storage.load_doctrine_contracts()}
    result = do_sync_for_tool("doctrine", extra={"structure_id": structure_id})
    match_result = do_validate_contracts(cfg)

    role_by_character = {cid: role for role, cid, _name in characters}
    fallback_role = characters[0][0] if characters else ""
    finished: list[tuple[dict, str]] = []
    seen_history: set[int] = set()
    for owner in result.get("owners") or []:
        kinds = owner.get("kinds") or {}
        contracts = kinds.get("contracts")
        if not isinstance(contracts, dict):
            continue
        if owner.get("owner_type") == "character":
            role = role_by_character.get(owner.get("owner_id"), fallback_role)
        else:
            role = fallback_role
        for raw in contracts.get("finished") or []:
            cid = raw.get("contract_id")
            if cid in seen_history:
                continue
            seen_history.add(cid)
            finished.append((raw, role))

    client = ESIClient(tokens=tm)
    acceptor_ids = {c.get("acceptor_id") for c, _role in finished if c.get("acceptor_id")}
    acceptor_names = client.resolve_names(list(acceptor_ids)) if acceptor_ids else {}
    history_rows: list[tuple] = []
    for raw, role in finished:
        cid = raw["contract_id"]
        existing = existing_by_id.get(cid)
        fitting_id = existing[9] if existing is not None else None
        if fitting_id is None:
            continue
        fitting_row = storage.get_fitting(fitting_id)
        if fitting_row is None:
            continue
        fitting_name, hull_type_id = fitting_row[2], fitting_row[4]
        acceptor_id = raw.get("acceptor_id")
        history_rows.append((
            cid, role, fitting_id, fitting_name, hull_type_id, raw.get("title"), raw.get("price"),
            acceptor_id, acceptor_names.get(acceptor_id) if acceptor_id is not None else None,
            raw.get("date_issued"), raw.get("date_completed"),
        ))
    storage.upsert_doctrine_contract_history(history_rows)
    storage.set_esi_sync_time("doctrine", datetime.now(timezone.utc).isoformat())
    _emit_progress(progress_callback, {"phase": "sync", "message": "Matching contracts"})
    result["contracts_synced"] = match_result.get("revalidated", match_result.get("contracts_synced"))
    result["history_written"] = len(history_rows)
    return result


# =========================================================== asset sync (Stockpile)
def list_doctrine_asset_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """Returns (role_key, character_id, character_name) for every registered
    asset-scanning character - same get_record (no refresh) pattern as
    list_doctrine_characters above, for the same reason."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(DOCTRINE_ASSET_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
    return out


def sync_assets() -> dict:
    """Delegates the fetch to the Phase 3 orchestrator. Sharing with
    `tool_key="doctrine"` is what keeps a Doctrine-only tenant's stockpile
    independent of Production."""
    from ..esi_data.orchestrator import do_sync_for_tool

    tm = TokenManager(OAUTH_CONFIG)
    characters = list_doctrine_asset_characters(tm)
    if not characters:
        raise ActionError(
            "No asset-scanning character logged in yet. Use 'Add Character' under Stockpile in the sidebar."
        )
    extra = {}
    structure_id = DOCTRINE_CONFIG.effective_structure_id
    if structure_id is not None:
        extra["structure_id"] = structure_id
    result = do_sync_for_tool("doctrine", extra=extra or None)
    storage.set_esi_sync_time("doctrine_assets", datetime.now(timezone.utc).isoformat())
    return result
