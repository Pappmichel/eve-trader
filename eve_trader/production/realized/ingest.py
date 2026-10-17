"""Archive ESI snapshot rows into the Production ledger.

Called from `esi_data.fetchers` immediately after each snapshot write.
Idempotent upserts, never deletes. Snapshot behaviour is unchanged.

Deliberate layering note: fetchers lazy-import this module (a Production
tool package). The plan places archive writes inside existing fetchers;
esi_data otherwise imports no tool package - this is the one exception
for Production realized profit (docs/PRODUCTION_REALIZED_PROFIT_PLAN.md).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ... import hubs, storage
from ...hubs import ALL_HUBS
from .. import config as production_config
from ..engine import _haul_volume
from . import db, inputs

log = logging.getLogger(__name__)

_FINISHED_CONTRACT_STATUSES = frozenset(
    ("finished", "finished_issuer", "finished_contractor"),
)
_ITEM_EXCHANGE = "item_exchange"


def _parse_esi_time(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _freight_rate_for_forge(cfg) -> float:
    """Same rate the planner uses for The Forge buys (plan section 5a)."""
    if getattr(cfg, "hub_region_id", None) == ALL_HUBS:
        return hubs.hub_freight_per_m3(
            db.THE_FORGE_REGION_ID, fallback=cfg.haul_cost_per_m3,
        )
    return float(cfg.haul_cost_per_m3)


def _maybe_record_gap(
    *,
    owner_type: str,
    owner_id: int,
    data_kind: str,
    table: str,
    date_column: str,
    sync_dates: list[datetime],
) -> None:
    """If the oldest row of this sync is newer than the newest archived row,
    completed history in between was lost - record an archive gap."""
    dated = [d for d in sync_dates if d is not None]
    if not dated:
        return
    oldest_sync = min(dated)
    newest_archived = db.newest_archived_date(table, owner_type, owner_id, date_column)
    if newest_archived is None:
        return
    if oldest_sync > newest_archived:
        db.record_archive_gap(
            owner_type, owner_id, data_kind, newest_archived, oldest_sync,
        )
        log.warning(
            "ledger archive gap for %s %s/%s: archived newest %s, sync oldest %s",
            data_kind, owner_type, owner_id, newest_archived, oldest_sync,
        )


# ------------------------------------------------------------------ wallet
def archive_wallet(
    *,
    owner_type: str,
    owner_id: int,
    txn_rows: list[tuple],
    journal_raw: list[dict],
    journal_division: int = 0,
    cfg=None,
) -> dict:
    """Archive wallet transactions + journal after the snapshot write.

    `txn_rows` is the same shape as `storage.replace_wallet_transactions`
    ((division, transaction_id, date, type_id, location_id, unit_price,
    quantity, is_buy, journal_ref_id), ...). Journal is the raw ESI list
    (needs context_id etc.).
    """
    cfg = cfg if cfg is not None else production_config.PRODUCTION_CONFIG
    home_location_id = getattr(cfg, "home_location_id", None)
    forge_rate = _freight_rate_for_forge(cfg)

    ledger_txns: list[tuple] = []
    txn_dates: list[datetime] = []
    for r in txn_rows:
        division, txn_id, date, type_id, location_id, unit_price, quantity, is_buy, journal_ref_id = r
        region_id = db.region_id_for_location(int(location_id))
        freight_rate = None
        freight_m3 = None
        if is_buy and region_id == db.THE_FORGE_REGION_ID:
            if home_location_id is None or int(location_id) != int(home_location_id):
                vol = _haul_volume(int(type_id), cfg)
                if vol is not None:
                    freight_rate = forge_rate
                    freight_m3 = float(vol) * float(quantity)
        ledger_txns.append((
            owner_type, int(owner_id), int(division), int(txn_id), date,
            int(type_id), int(location_id), float(unit_price), int(quantity),
            bool(is_buy), int(journal_ref_id), region_id, freight_rate, freight_m3,
        ))
        dt = _parse_esi_time(date)
        if dt is not None:
            txn_dates.append(dt)

    _maybe_record_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="wallet_transactions",
        table="ledger_wallet_transactions", date_column="date", sync_dates=txn_dates,
    )
    db.upsert_ledger_wallet_transactions(ledger_txns)

    journal_rows: list[tuple] = []
    journal_dates: list[datetime] = []
    for e in journal_raw:
        jid = e.get("id")
        if jid is None:
            continue
        date = e.get("date")
        journal_rows.append((
            owner_type, int(owner_id), int(journal_division), int(jid), date,
            e.get("ref_type") or "", float(e.get("amount") or 0.0),
            e.get("context_id"), e.get("context_id_type"),
            e.get("first_party_id"), e.get("second_party_id"),
            e.get("description"), e.get("tax"), e.get("tax_receiver_id"),
        ))
        dt = _parse_esi_time(date)
        if dt is not None:
            journal_dates.append(dt)

    _maybe_record_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="wallet_journal",
        table="ledger_wallet_journal", date_column="date", sync_dates=journal_dates,
    )
    db.upsert_ledger_wallet_journal(journal_rows)
    return {"transactions": len(ledger_txns), "journal": len(journal_rows)}


def archive_corp_wallet(
    *,
    owner_id: int,
    txn_rows: list[tuple],
    journal_entries_by_division: dict[int, list[dict]],
    cfg=None,
) -> dict:
    """Corp wallet: txns already carry division; journal is per division."""
    cfg = cfg if cfg is not None else production_config.PRODUCTION_CONFIG
    # Reuse character path for txns (division already in rows).
    home_location_id = getattr(cfg, "home_location_id", None)
    forge_rate = _freight_rate_for_forge(cfg)
    ledger_txns: list[tuple] = []
    txn_dates: list[datetime] = []
    for r in txn_rows:
        division, txn_id, date, type_id, location_id, unit_price, quantity, is_buy, journal_ref_id = r
        region_id = db.region_id_for_location(int(location_id))
        freight_rate = None
        freight_m3 = None
        if is_buy and region_id == db.THE_FORGE_REGION_ID:
            if home_location_id is None or int(location_id) != int(home_location_id):
                vol = _haul_volume(int(type_id), cfg)
                if vol is not None:
                    freight_rate = forge_rate
                    freight_m3 = float(vol) * float(quantity)
        ledger_txns.append((
            "corporation", int(owner_id), int(division), int(txn_id), date,
            int(type_id), int(location_id), float(unit_price), int(quantity),
            bool(is_buy), int(journal_ref_id), region_id, freight_rate, freight_m3,
        ))
        dt = _parse_esi_time(date)
        if dt is not None:
            txn_dates.append(dt)
    _maybe_record_gap(
        owner_type="corporation", owner_id=owner_id, data_kind="wallet_transactions",
        table="ledger_wallet_transactions", date_column="date", sync_dates=txn_dates,
    )
    db.upsert_ledger_wallet_transactions(ledger_txns)

    journal_rows: list[tuple] = []
    journal_dates: list[datetime] = []
    for division, entries in journal_entries_by_division.items():
        for e in entries:
            jid = e.get("id")
            if jid is None:
                continue
            date = e.get("date")
            journal_rows.append((
                "corporation", int(owner_id), int(division), int(jid), date,
                e.get("ref_type") or "", float(e.get("amount") or 0.0),
                e.get("context_id"), e.get("context_id_type"),
                e.get("first_party_id"), e.get("second_party_id"),
                e.get("description"), e.get("tax"), e.get("tax_receiver_id"),
            ))
            dt = _parse_esi_time(date)
            if dt is not None:
                journal_dates.append(dt)
    _maybe_record_gap(
        owner_type="corporation", owner_id=owner_id, data_kind="wallet_journal",
        table="ledger_wallet_journal", date_column="date", sync_dates=journal_dates,
    )
    db.upsert_ledger_wallet_journal(journal_rows)
    return {"transactions": len(ledger_txns), "journal": len(journal_rows)}


# ----------------------------------------------------------- industry jobs
def archive_industry_jobs(
    *,
    owner_type: str,
    owner_id: int,
    jobs: list[dict],
    cfg=None,
) -> dict:
    """Archive completed+active jobs (include_completed=true payload).

    Freezes inputs only for jobs not already in the archive.
    """
    if not jobs:
        return {"jobs": 0, "inputs": 0}
    cfg = cfg if cfg is not None else production_config.PRODUCTION_CONFIG
    selected = storage.load_selected_decryptors()

    job_ids = [int(j["job_id"]) for j in jobs if j.get("job_id") is not None]
    already = db.job_ids_already_archived(owner_type, owner_id, job_ids)

    rows: list[tuple] = []
    start_dates: list[datetime] = []
    input_rows: list[tuple] = []
    for j in jobs:
        jid = j.get("job_id")
        if jid is None:
            continue
        activity_id = int(j["activity_id"])
        # Normalize live reaction id 9 -> SDE 11 (same as snapshot fetcher).
        if activity_id == 9:
            activity_id = 11
        start = j.get("start_date")
        rows.append((
            owner_type, int(owner_id), int(jid), activity_id,
            j.get("blueprint_id"), int(j["blueprint_type_id"]),
            j.get("product_type_id"), int(j["runs"]),
            j.get("licensed_runs"), j.get("successful_runs"),
            j.get("cost"), j.get("facility_id"), j.get("station_id"),
            j.get("output_location_id"), j.get("installer_id"),
            start, j.get("end_date"), j.get("completed_date"),
            j.get("status"), j.get("probability"),
        ))
        dt = _parse_esi_time(start)
        if dt is not None:
            start_dates.append(dt)

        if int(jid) not in already:
            frozen = inputs.freeze_job_inputs(
                owner_type=owner_type, owner_id=owner_id, job={**j, "activity_id": activity_id},
                cfg=cfg, selected_decryptors=selected,
            )
            for inp in frozen:
                input_rows.append(inputs.frozen_to_row(owner_type, owner_id, int(jid), inp))

    _maybe_record_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="industry_jobs",
        table="ledger_industry_jobs", date_column="start_date", sync_dates=start_dates,
    )
    db.upsert_ledger_industry_jobs(rows)
    db.insert_ledger_job_inputs(input_rows)
    return {"jobs": len(rows), "inputs": len(input_rows), "new_jobs": len(job_ids) - len(already & set(job_ids))}


# -------------------------------------------------------------- blueprints
def archive_blueprints(
    *,
    owner_type: str,
    owner_id: int,
    blueprints: list[dict],
) -> dict:
    rows = [
        (
            owner_type, int(owner_id), int(b["item_id"]), int(b["type_id"]),
            b.get("location_id"), int(b["material_efficiency"]),
            int(b["time_efficiency"]), int(b["runs"]), b.get("quantity"),
        )
        for b in blueprints
        if b.get("item_id") is not None
    ]
    db.upsert_ledger_blueprints(rows)
    return {"blueprints": len(rows)}


# ----------------------------------------------------------- market orders
def archive_market_orders(
    *,
    owner_type: str,
    owner_id: int,
    orders: list[dict],
) -> dict:
    """Open orders + order history rows (same shape from ESI)."""
    rows: list[tuple] = []
    issued_dates: list[datetime] = []
    for o in orders:
        oid = o.get("order_id")
        if oid is None:
            continue
        issued = o.get("issued")
        rows.append((
            owner_type, int(owner_id), int(oid), int(o["type_id"]),
            int(o["location_id"]), o.get("region_id"),
            bool(o.get("is_buy_order")), float(o["price"]),
            int(o.get("volume_total") or o.get("volume_remain") or 0),
            o.get("volume_remain"), issued, o.get("state"),
        ))
        dt = _parse_esi_time(issued)
        if dt is not None:
            issued_dates.append(dt)
    _maybe_record_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="market_orders",
        table="ledger_market_orders", date_column="issued", sync_dates=issued_dates,
    )
    db.upsert_ledger_market_orders(rows)
    return {"orders": len(rows)}


# ---------------------------------------------------------------- contracts
def archive_contracts(
    *,
    owner_type: str,
    owner_id: int,
    contracts: list[dict],
    items_by_contract: dict[int, list[dict]],
) -> dict:
    """Item-exchange contracts where this owner is issuer or acceptor."""
    rows: list[tuple] = []
    item_rows: list[tuple] = []
    issued_dates: list[datetime] = []
    for c in contracts:
        if c.get("type") != _ITEM_EXCHANGE:
            continue
        cid = c.get("contract_id")
        if cid is None:
            continue
        rows.append((
            owner_type, int(owner_id), int(cid),
            c.get("issuer_id"), c.get("issuer_corporation_id"), c.get("acceptor_id"),
            c.get("start_location_id"), c.get("end_location_id"),
            c.get("type") or _ITEM_EXCHANGE, c.get("status") or "",
            c.get("title"), c.get("price"), c.get("reward"),
            c.get("date_issued"), c.get("date_completed"), c.get("date_expired"),
        ))
        dt = _parse_esi_time(c.get("date_issued"))
        if dt is not None:
            issued_dates.append(dt)
        for it in items_by_contract.get(int(cid), []):
            item_rows.append((
                owner_type, int(owner_id), int(cid), int(it["record_id"]),
                int(it["type_id"]), int(it["quantity"]),
                bool(it.get("is_included", True)), bool(it.get("is_singleton", False)),
            ))
    _maybe_record_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="contracts",
        table="ledger_contracts", date_column="date_issued", sync_dates=issued_dates,
    )
    db.upsert_ledger_contracts(rows)
    db.upsert_ledger_contract_items(item_rows)
    return {"contracts": len(rows), "items": len(item_rows)}


def contract_party_matches(
    contract: dict, *, owner_type: str, owner_id: int, corporation_id: Optional[int] = None,
) -> bool:
    """True when this tenant owner is issuer or acceptor of the contract."""
    oid = int(owner_id)
    if owner_type == "character":
        if contract.get("issuer_id") == oid or contract.get("acceptor_id") == oid:
            return True
        if corporation_id is not None and contract.get("issuer_corporation_id") == int(corporation_id):
            # Character's corp issued it - still relevant for the archive when
            # the character is the issuer_id path above; corp-issued without
            # this character as issuer is the corp owner's problem.
            return contract.get("issuer_id") == oid
        return False
    return (
        contract.get("issuer_corporation_id") == oid
        or contract.get("acceptor_id") == oid
    )
