"""Thin ESIClient wrappers, one per owned data kind × owner type.

Each fetcher calls an existing `ESIClient` method and writes the matching
Phase 1/2 snapshot table. Group 3 is not a fetcher (name resolution stays
opportunistic). This module imports no tool package.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Optional

from .. import storage
from ..config import WALLET_DIVISION_IDS
from ..esi_client import ESIClient, ESIError

# Confirmed CCP live/SDE mismatch: ESI industry jobs report Reactions as
# activity_id 9, the SDE files them under 11. Same normalization
# production/esi_sync.py has always applied at ingestion.
_LIVE_REACTION_ACTIVITY_ID = 9
_SDE_REACTION_ACTIVITY_ID = 11

# CCP type IDs for the three slot-skill pairs (duplicated from
# production/constants.py so this package does not import a tool).
_SKILL_MASS_PRODUCTION = 3387
_SKILL_ADVANCED_MASS_PRODUCTION = 24625
_SKILL_MASS_REACTIONS = 45748
_SKILL_ADVANCED_MASS_REACTIONS = 45749
_SKILL_LABORATORY_OPERATION = 3406
_SKILL_ADVANCED_LABORATORY_OPERATION = 24624

WALLET_TRANSACTIONS_PAGE_SIZE = 2500

# ESI contract vocabulary used by the contracts fetcher. Duplicated from
# doctrine/constants.py so this package does not import a tool.
_CONTRACT_TYPE_ITEM_EXCHANGE = "item_exchange"
_SYNCABLE_CONTRACT_STATUSES = ("outstanding", "expired")
_FINISHED_CONTRACT_STATUSES = ("finished", "finished_issuer", "finished_contractor")
_VALIDATION_UNMATCHED = "unmatched"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_activity_id(activity_id: int) -> int:
    return _SDE_REACTION_ACTIVITY_ID if activity_id == _LIVE_REACTION_ACTIVITY_ID else activity_id


def _job_slots_from_skills(skill_levels: dict[int, int]) -> dict[str, int]:
    def slots(base_id: int, advanced_id: int) -> int:
        return 1 + skill_levels.get(base_id, 0) + skill_levels.get(advanced_id, 0)

    return {
        "manufacturing": slots(_SKILL_MASS_PRODUCTION, _SKILL_ADVANCED_MASS_PRODUCTION),
        "reaction": slots(_SKILL_MASS_REACTIONS, _SKILL_ADVANCED_MASS_REACTIONS),
        "science": slots(_SKILL_LABORATORY_OPERATION, _SKILL_ADVANCED_LABORATORY_OPERATION),
    }


def _page_wallet_transactions(fetch_page: Callable) -> list[dict]:
    all_txns: list[dict] = []
    from_id: Optional[int] = None
    while True:
        page = fetch_page(from_id)
        if not page:
            break
        all_txns.extend(page)
        oldest = min(page, key=lambda t: t["transaction_id"])
        if len(page) < WALLET_TRANSACTIONS_PAGE_SIZE:
            break
        from_id = oldest["transaction_id"]
    return all_txns


def _asset_rows(assets: list[dict], owner_name: str) -> list[tuple]:
    return [
        (a["item_id"], a["type_id"], a["location_id"], a["location_flag"],
         a["quantity"], int(bool(a.get("is_blueprint_copy"))), owner_name)
        for a in assets
    ]


def _industry_job_rows(jobs: list[dict], installer_names: dict[int, str]) -> list[tuple]:
    return [
        (j["job_id"], _normalize_activity_id(j["activity_id"]), j["blueprint_type_id"],
         j.get("product_type_id"), j["runs"], j.get("output_location_id"), j["status"],
         j["end_date"], j.get("start_date"), j.get("installer_id"),
         installer_names.get(j.get("installer_id"), str(j.get("installer_id"))))
        for j in jobs
    ]


def _blueprint_rows(bps: list[dict]) -> list[tuple]:
    return [
        (b["item_id"], b["type_id"], b["location_id"], b["location_flag"], b["quantity"],
         b["material_efficiency"], b["time_efficiency"], b["runs"])
        for b in bps
    ]


def _sell_order_rows(orders: list[dict], owner_name: str) -> list[tuple]:
    return [
        (o["order_id"], o["type_id"], o["location_id"], o["region_id"], o["volume_remain"], owner_name)
        for o in orders if not o.get("is_buy_order")
    ]


def _wallet_txn_rows(txns: list[dict], division: int) -> list[tuple]:
    return [
        (division, t["transaction_id"], t["date"], t["type_id"], t["location_id"],
         t["unit_price"], t["quantity"], bool(t.get("is_buy")), t["journal_ref_id"])
        for t in txns
    ]


def _wallet_journal_rows(entries: list[dict], division: int) -> list[tuple]:
    return [
        (division, e["id"], e["date"], e.get("ref_type") or "", e.get("amount") or 0.0)
        for e in entries
    ]


# ------------------------------------------------------------------ assets
def fetch_character_assets(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    assets = client.character_assets(owner_id, auth_role=auth_role)
    storage.replace_assets(
        "character_assets", _asset_rows(assets, owner_name),
        owner_character_id=owner_id, owner_name=owner_name,
    )
    return {"written": len(assets), "location_ids": [a["location_id"] for a in assets]}


def fetch_corporation_assets(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    assets = client.corporation_assets(owner_id, auth_role=auth_role)
    storage.replace_assets(
        "corp_assets", _asset_rows(assets, owner_name),
        owner_corporation_id=owner_id, owner_name=owner_name,
    )
    return {"written": len(assets), "location_ids": [a["location_id"] for a in assets]}


# ----------------------------------------------------------- industry jobs
def fetch_character_industry_jobs(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    jobs = client.character_industry_jobs(owner_id, auth_role=auth_role)
    installer_ids = {j.get("installer_id") for j in jobs if j.get("installer_id")}
    names = client.resolve_names(list(installer_ids)) if installer_ids else {}
    storage.replace_industry_jobs(
        "character_industry_jobs", _industry_job_rows(jobs, names),
        owner_character_id=owner_id,
    )
    return {"written": len(jobs)}


def fetch_corporation_industry_jobs(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    jobs = client.corporation_industry_jobs(owner_id, auth_role=auth_role)
    installer_ids = {j.get("installer_id") for j in jobs if j.get("installer_id")}
    names = client.resolve_names(list(installer_ids)) if installer_ids else {}
    storage.replace_industry_jobs(
        "corp_industry_jobs", _industry_job_rows(jobs, names),
        owner_corporation_id=owner_id,
    )
    return {"written": len(jobs)}


# -------------------------------------------------------------- blueprints
def fetch_character_blueprints(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    bps = client.character_blueprints(owner_id, auth_role=auth_role)
    storage.replace_blueprints(
        "character_blueprints", _blueprint_rows(bps),
        owner_character_id=owner_id,
    )
    return {"written": len(bps)}


def fetch_corporation_blueprints(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    bps = client.corporation_blueprints(owner_id, auth_role=auth_role)
    storage.replace_blueprints(
        "corp_blueprints", _blueprint_rows(bps),
        owner_corporation_id=owner_id,
    )
    return {"written": len(bps)}


# ----------------------------------------------------------- market orders
def fetch_character_market_orders(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    orders = client.character_orders(owner_id, auth_role=auth_role)
    rows = _sell_order_rows(orders, owner_name)
    storage.replace_sell_orders(
        rows, owner_character_id=owner_id, owner_name=owner_name,
    )
    return {"written": len(rows)}


def fetch_corporation_market_orders(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    orders = client.corporation_orders(owner_id, auth_role=auth_role)
    rows = _sell_order_rows(orders, owner_name)
    storage.replace_sell_orders(
        rows, owner_corporation_id=owner_id, owner_name=owner_name,
    )
    return {"written": len(rows)}


# ------------------------------------------------------------------ wallet
def fetch_character_wallet(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    txns = _page_wallet_transactions(
        lambda from_id: client.character_wallet_transactions(
            owner_id, auth_role=auth_role, from_id=from_id,
        )
    )
    journal = client.character_wallet_journal(owner_id, auth_role=auth_role)
    storage.replace_wallet_transactions(
        _wallet_txn_rows(txns, 0), owner_type="character", owner_id=owner_id,
    )
    storage.replace_wallet_journal(
        _wallet_journal_rows(journal, 0), owner_type="character", owner_id=owner_id,
    )
    return {"written": len(txns), "journal": len(journal)}


def fetch_corporation_wallet(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **kwargs,
) -> dict:
    """Union readable divisions across every candidate member role.

    A Junior Accountant may only see division 1; an Accountant listed
    later may see 2-7. Trying one role and stopping (Phase 8's original
    shape) let the replace wipe unread divisions. `candidate_auth_roles`
    is the orchestrator's full member list for this corp; a single
    `auth_role` (direct call, or one member) still works. Zero readable
    divisions is a failed fetch (skip the delete, decision 6).
    """
    roles = [r for r in (kwargs.get("candidate_auth_roles") or ()) if r]
    if not roles:
        roles = [auth_role]
    unread = list(WALLET_DIVISION_IDS)
    txn_rows: list[tuple] = []
    journal_rows: list[tuple] = []
    readable: list[int] = []
    last_error: Optional[BaseException] = None
    for role in roles:
        if not unread:
            break
        still_unread: list[int] = []
        for division in unread:
            try:
                txns = _page_wallet_transactions(
                    lambda from_id, d=division, r=role: client.corporation_wallet_transactions(
                        owner_id, d, auth_role=r, from_id=from_id,
                    )
                )
                journal = client.corporation_wallet_journal(
                    owner_id, division, auth_role=role,
                )
            except ESIError as e:
                last_error = e
                still_unread.append(division)
                continue
            readable.append(division)
            txn_rows.extend(_wallet_txn_rows(txns, division))
            journal_rows.extend(_wallet_journal_rows(journal, division))
        unread = still_unread
    if not readable:
        if last_error is not None:
            raise last_error
        raise ESIError("no corporation wallet division readable")
    storage.replace_wallet_transactions(
        txn_rows, owner_type="corporation", owner_id=owner_id,
        divisions=readable,
    )
    storage.replace_wallet_journal(
        journal_rows, owner_type="corporation", owner_id=owner_id,
        divisions=readable,
    )
    return {"written": len(txn_rows), "journal": len(journal_rows), "divisions": readable}


# ------------------------------------------------------------ wallet_balance
def fetch_character_wallet_balance(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    balance = client.character_wallet_balance(owner_id, auth_role=auth_role)
    storage.upsert_character_wallet_balance(owner_id, balance)
    return {"written": 1, "balance": balance}


def fetch_corporation_wallet_balance(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **kwargs,
) -> dict:
    """Union readable divisions across every candidate member role - same
    reasoning as fetch_corporation_wallet above (a Junior Accountant may
    only see division 1)."""
    roles = [r for r in (kwargs.get("candidate_auth_roles") or ()) if r]
    if not roles:
        roles = [auth_role]
    balances: dict[int, float] = {}
    last_error: Optional[BaseException] = None
    for role in roles:
        try:
            wallets = client.corporation_wallet_balances(owner_id, auth_role=role)
        except ESIError as e:
            last_error = e
            continue
        for w in wallets:
            balances[w["division"]] = w["balance"]
    if not balances:
        if last_error is not None:
            raise last_error
        raise ESIError("no corporation wallet division readable")
    storage.replace_corp_wallet_balances(owner_id, balances)
    return {"written": len(balances), "divisions": sorted(balances)}


# ------------------------------------------------------------------- skills
def fetch_character_skills(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **_kwargs,
) -> dict:
    skills = client.character_skills(owner_id, auth_role=auth_role)
    levels = {s["skill_id"]: s["active_skill_level"] for s in skills.get("skills", [])}
    slots = _job_slots_from_skills(levels)
    storage.upsert_character_slot_row(
        owner_name, slots["manufacturing"], slots["reaction"], slots["science"],
        owner_character_id=owner_id,
    )
    return {"written": 1, "slots": slots}


# ---------------------------------------------------------------- contracts
def _at_structure(contracts: list[dict], structure_id: Optional[int]) -> list[dict]:
    """Doctrine's original pre-filter: items are 1 ESI call per contract,
    so off-structure contracts never reach the items fetch. `structure_id`
    is passed via orchestrator extra, not imported from the doctrine
    package."""
    if structure_id is None:
        return contracts
    return [c for c in contracts if c.get("start_location_id") == structure_id]


def _issued_by_character(contract: dict, character_id: int, corporation_id: Optional[int]) -> bool:
    return contract.get("issuer_id") == character_id or (
        corporation_id is not None and contract.get("issuer_corporation_id") == corporation_id
    )


def _fetch_contract_items_for(
    client: ESIClient,
    entries: list[tuple[dict, bool, Optional[int]]],
    *,
    character_id: Optional[int],
    auth_role: str,
) -> tuple[dict[int, list[dict]], dict[int, str]]:
    """`entries`: (raw_contract, for_corp, corp_id)."""
    existing_by_id = {row[0]: row for row in storage.load_doctrine_contracts()}
    to_fetch = []
    carried: dict[int, list[tuple]] = {}
    for raw, for_corp, corp_id in entries:
        cid = raw["contract_id"]
        existing = existing_by_id.get(cid)
        if existing is not None and existing[5] == raw.get("status"):
            carried[cid] = storage.load_doctrine_contract_items(cid)
        else:
            to_fetch.append((raw, for_corp, corp_id))

    fetched: dict[int, list[dict]] = {}
    errors: dict[int, str] = {}

    def _one(entry):
        raw, for_corp, corp_id = entry
        cid = raw["contract_id"]
        try:
            if for_corp:
                items = client.corporation_contract_items(corp_id, cid, auth_role=auth_role)
            else:
                items = client.character_contract_items(character_id, cid, auth_role=auth_role)
            return cid, items, None
        except ESIError as e:
            return cid, None, str(e)

    if to_fetch:
        wrapped = storage.with_current_tenant(_one)
        with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as pool:
            futs = [pool.submit(wrapped, entry) for entry in to_fetch]
            for fut in as_completed(futs):
                cid, items, error = fut.result()
                if error is not None:
                    errors[cid] = error
                else:
                    fetched[cid] = items or []
    return {"carried": carried, "fetched": fetched, "errors": errors}


def _write_contract_snapshot(
    contracts_raw: list[dict],
    items_info: dict,
    *,
    owner_type: str,
    owner_id: int,
    auth_role: str,
    for_corp: bool,
) -> int:
    existing_by_id = {row[0]: row for row in storage.load_doctrine_contracts()}
    synced_at = _now_iso()
    contract_rows: list[tuple] = []
    item_rows: list[tuple] = []
    carried = items_info["carried"]
    fetched = items_info["fetched"]
    for raw in contracts_raw:
        cid = raw["contract_id"]
        if cid not in carried and cid not in fetched:
            continue
        existing = existing_by_id.get(cid)
        if existing is not None and existing[5] == raw.get("status"):
            matched, score, status = existing[9], existing[10], existing[11]
        else:
            matched, score, status = None, None, _VALIDATION_UNMATCHED
        contract_rows.append((
            cid, auth_role, for_corp, raw.get("issuer_id"), raw.get("start_location_id"),
            raw.get("status"), raw.get("title"), raw.get("price"), raw.get("date_expired"),
            matched, score, status, synced_at,
        ))
        if cid in carried:
            for record_id, type_id, qty, is_incl, is_single in carried[cid]:
                item_rows.append((cid, record_id, type_id, qty, is_incl, is_single))
        else:
            for it in fetched[cid]:
                item_rows.append((
                    cid, it["record_id"], it["type_id"], it["quantity"],
                    it.get("is_included", True), it.get("is_singleton", False),
                ))
    kwargs = {"owner_name": auth_role}
    if owner_type == "character":
        kwargs["owner_character_id"] = owner_id
    else:
        kwargs["owner_corporation_id"] = owner_id
    storage.replace_doctrine_sync_snapshot(contract_rows, item_rows, [], **kwargs)
    return len(contract_rows)


def fetch_character_contracts(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **kwargs,
) -> dict:
    raw_list = client.character_contracts(owner_id, auth_role=auth_role)
    corporation_id = kwargs.get("corporation_id")
    if corporation_id is None:
        try:
            corporation_id = client.character_public_info(owner_id).get("corporation_id")
        except ESIError:
            corporation_id = None
    own = [c for c in raw_list if _issued_by_character(c, owner_id, corporation_id)]
    structure_id = kwargs.get("structure_id")
    syncable = _at_structure(
        [
            c for c in own
            if c.get("type") == _CONTRACT_TYPE_ITEM_EXCHANGE
            and c.get("status") in _SYNCABLE_CONTRACT_STATUSES
        ],
        structure_id,
    )
    finished = _at_structure(
        [
            c for c in own
            if c.get("type") == _CONTRACT_TYPE_ITEM_EXCHANGE
            and c.get("status") in _FINISHED_CONTRACT_STATUSES
        ],
        structure_id,
    )
    items_info = _fetch_contract_items_for(
        client, [(c, False, None) for c in syncable],
        character_id=owner_id, auth_role=auth_role,
    )
    written = _write_contract_snapshot(
        syncable, items_info,
        owner_type="character", owner_id=owner_id, auth_role=auth_role, for_corp=False,
    )
    return {"written": written, "finished": finished, "corporation_id": corporation_id}


def fetch_corporation_contracts(
    client: ESIClient, owner_id: int, auth_role: str, owner_name: str, **kwargs,
) -> dict:
    raw_list = client.corporation_contracts(owner_id, auth_role=auth_role)
    # Contracts this corp issued, not merely assigned to it.
    own = [c for c in raw_list if c.get("issuer_corporation_id") == owner_id]
    structure_id = kwargs.get("structure_id")
    syncable = _at_structure(
        [
            c for c in own
            if c.get("type") == _CONTRACT_TYPE_ITEM_EXCHANGE
            and c.get("status") in _SYNCABLE_CONTRACT_STATUSES
        ],
        structure_id,
    )
    finished = _at_structure(
        [
            c for c in own
            if c.get("type") == _CONTRACT_TYPE_ITEM_EXCHANGE
            and c.get("status") in _FINISHED_CONTRACT_STATUSES
        ],
        structure_id,
    )
    items_info = _fetch_contract_items_for(
        client, [(c, True, owner_id) for c in syncable],
        character_id=None, auth_role=auth_role,
    )
    written = _write_contract_snapshot(
        syncable, items_info,
        owner_type="corporation", owner_id=owner_id, auth_role=auth_role, for_corp=True,
    )
    return {"written": written, "finished": finished}


# Registry-driven dispatch: (kind, owner_type) -> fetcher.
FETCHERS: dict[tuple[str, str], Callable] = {
    ("assets", "character"): fetch_character_assets,
    ("assets", "corporation"): fetch_corporation_assets,
    ("industry_jobs", "character"): fetch_character_industry_jobs,
    ("industry_jobs", "corporation"): fetch_corporation_industry_jobs,
    ("blueprints", "character"): fetch_character_blueprints,
    ("blueprints", "corporation"): fetch_corporation_blueprints,
    ("market_orders", "character"): fetch_character_market_orders,
    ("market_orders", "corporation"): fetch_corporation_market_orders,
    ("wallet", "character"): fetch_character_wallet,
    ("wallet", "corporation"): fetch_corporation_wallet,
    ("wallet_balance", "character"): fetch_character_wallet_balance,
    ("wallet_balance", "corporation"): fetch_corporation_wallet_balance,
    ("skills", "character"): fetch_character_skills,
    ("contracts", "character"): fetch_character_contracts,
    ("contracts", "corporation"): fetch_corporation_contracts,
}

def fetcher_for(data_kind: str, owner_type: str) -> Callable:
    fn = FETCHERS.get((data_kind, owner_type))
    if fn is None:
        raise KeyError(f"no fetcher for {(data_kind, owner_type)}")
    return fn
