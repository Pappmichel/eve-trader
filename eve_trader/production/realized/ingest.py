"""Archive ESI snapshot rows into the Production ledger.

Called from `esi_data.fetchers` immediately after each snapshot write.
Idempotent upserts, never deletes. Snapshot behaviour is unchanged.

Deliberate layering note: fetchers lazy-import this module (a Production
tool package). The plan places archive writes inside existing fetchers;
esi_data otherwise imports no tool package - this is the one exception
for Production realized profit (docs/PRODUCTION_REALIZED_PROFIT_PLAN.md,
CLAUDE.md "Two tools, one backend").
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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
_COURIER = "courier"

# How far back each ESI list reaches, and the row caps Phase 0 found
# (plan section 13a). A gap can only exist where one of these was exceeded.
JOURNAL_ENTRY_CAP = 10_000        # proven: newest 10,000 entries only
INDUSTRY_JOBS_ROW_CAP = 2_000     # assumed hard cap (user decision 2026-10-17)
WALLET_WINDOW_DAYS = 30
CONTRACTS_WINDOW_DAYS = 30
ORDER_HISTORY_WINDOW_DAYS = 90
INDUSTRY_JOBS_WINDOW_DAYS = 90

# Activities whose jobs consume materials (manufacturing, invention, reaction).
_ACTIVITIES_WITH_INPUTS = frozenset({1, 8, 11})


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


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _freight_rate_for_forge(cfg) -> float:
    """Same rate the planner uses for The Forge buys (plan section 5a,
    `pricing._freight_rate`): the hub table entry only in "All hubs" mode."""
    if getattr(cfg, "hub_region_id", None) == ALL_HUBS:
        return hubs.hub_freight_per_m3(
            db.THE_FORGE_REGION_ID, fallback=cfg.haul_cost_per_m3,
        )
    return float(cfg.haul_cost_per_m3)


def _check_gap(
    *,
    owner_type: str,
    owner_id: int,
    data_kind: str,
    now: datetime,
    division: int = 0,
    window_days: Optional[int] = None,
    cap: Optional[int] = None,
    row_count: int = 0,
    sync_dates: Optional[list[datetime]] = None,
    table: Optional[str] = None,
    date_column: Optional[str] = None,
) -> None:
    """Record an archive gap only where ESI can really have dropped rows:

    - windowed list: the previous archive sync is older than ESI's window,
      so rows from (previous sync, window start) may have expired unseen;
    - capped list: this response hit the row cap and its oldest row is newer
      than the newest archived one.

    A quiet period without new rows is never a gap. The first sync of an
    owner starts the archive and records nothing. The caller stamps the sync
    (`_stamp`) only after its rows were written."""
    previous = db.last_archive_sync(owner_type, owner_id, data_kind, division)
    if previous is not None and window_days is not None:
        horizon = now - timedelta(days=window_days)
        if previous < horizon:
            _record_gap(owner_type, owner_id, data_kind, previous, horizon)
    if previous is not None and cap is not None and row_count >= cap and table:
        dated = [d for d in (sync_dates or []) if d is not None]
        newest = db.newest_archived_date(
            table, owner_type, owner_id, date_column,
            division=division if table in ("ledger_wallet_transactions", "ledger_wallet_journal") else None,
        )
        if dated and newest is not None and min(dated) > newest:
            _record_gap(owner_type, owner_id, data_kind, newest, min(dated))


def _stamp(owner_type: str, owner_id: int, data_kind: str, now: datetime,
           division: int = 0) -> None:
    db.mark_archive_synced(owner_type, owner_id, data_kind, now, division)


def _record_gap(owner_type, owner_id, data_kind, start, end) -> None:
    db.record_archive_gap(owner_type, owner_id, data_kind, start, end)
    log.warning(
        "ledger archive gap for %s %s/%s: %s .. %s",
        data_kind, owner_type, owner_id, start, end,
    )


# ------------------------------------------------------------------ wallet
class _TxnEnricher:
    """Region + frozen freight for wallet rows, with per-call caches: a sync
    of one character touches thousands of rows at very few locations/types."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.home_location_id = getattr(cfg, "home_location_id", None)
        self.forge_rate = _freight_rate_for_forge(cfg)
        self._regions: dict[int, Optional[int]] = {}
        self._volumes: dict[int, Optional[float]] = {}

    def region(self, location_id: int) -> Optional[int]:
        loc = int(location_id)
        if loc not in self._regions:
            self._regions[loc] = db.region_id_for_location(loc)
        return self._regions[loc]

    def volume(self, type_id: int) -> Optional[float]:
        tid = int(type_id)
        if tid not in self._volumes:
            self._volumes[tid] = _haul_volume(tid, self.cfg)
        return self._volumes[tid]

    def freight(self, region_id, location_id, type_id, quantity, is_buy):
        """(freight_rate_per_m3, freight_m3) or (None, None)."""
        if not is_buy or region_id != db.THE_FORGE_REGION_ID:
            return None, None
        if self.home_location_id is not None and int(location_id) == int(self.home_location_id):
            return None, None
        vol = self.volume(type_id)
        if vol is None:
            return None, None
        return self.forge_rate, float(vol) * float(quantity)


def _archive_txn_rows(owner_type: str, owner_id: int, txn_rows: list[tuple],
                      enricher: _TxnEnricher) -> int:
    """Insert only rows new to the archive, then retry region resolution
    for archived rows that are still unresolved. Returns rows inserted."""
    known = db.archived_transaction_keys(owner_type, owner_id, [r[1] for r in txn_rows])
    ledger_txns: list[tuple] = []
    for r in txn_rows:
        division, txn_id, date, type_id, location_id, unit_price, quantity, is_buy, journal_ref_id = r
        if (int(division), int(txn_id)) in known:
            continue
        region_id = enricher.region(location_id)
        rate, m3 = enricher.freight(region_id, location_id, type_id, quantity, is_buy)
        ledger_txns.append((
            owner_type, int(owner_id), int(division), int(txn_id), date,
            int(type_id), int(location_id), float(unit_price), int(quantity),
            bool(is_buy), int(journal_ref_id), region_id, rate, m3,
        ))
    db.upsert_ledger_wallet_transactions(ledger_txns)

    updates: list[tuple] = []
    for division, txn_id, location_id, type_id, quantity, is_buy in db.transactions_missing_region(
            owner_type, owner_id):
        region_id = enricher.region(location_id)
        if region_id is None:
            continue
        rate, m3 = enricher.freight(region_id, location_id, type_id, quantity, is_buy)
        updates.append((region_id, rate, m3, owner_type, int(owner_id), int(division), int(txn_id)))
    db.set_transaction_region(updates)
    return len(ledger_txns)


def _journal_rows(owner_type: str, owner_id: int, division: int,
                  entries: list[dict]) -> tuple[list[tuple], list[datetime]]:
    rows: list[tuple] = []
    dates: list[datetime] = []
    for e in entries:
        jid = e.get("id")
        if jid is None:
            continue
        date = e.get("date")
        rows.append((
            owner_type, int(owner_id), int(division), int(jid), date,
            e.get("ref_type") or "", float(e.get("amount") or 0.0),
            e.get("context_id"), e.get("context_id_type"),
            e.get("first_party_id"), e.get("second_party_id"),
            e.get("description"), e.get("tax"), e.get("tax_receiver_id"),
        ))
        dt = _parse_esi_time(date)
        if dt is not None:
            dates.append(dt)
    return rows, dates


def _archive_journal(owner_type: str, owner_id: int, division: int,
                     entries: list[dict], now: datetime) -> int:
    rows, dates = _journal_rows(owner_type, owner_id, division, entries)
    _check_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="wallet_journal",
        now=now, division=division, window_days=WALLET_WINDOW_DAYS,
        cap=JOURNAL_ENTRY_CAP, row_count=len(rows), sync_dates=dates,
        table="ledger_wallet_journal", date_column="date",
    )
    db.upsert_ledger_wallet_journal(rows)
    _stamp(owner_type, owner_id, "wallet_journal", now, division)
    return len(rows)


def archive_wallet(
    *,
    owner_type: str,
    owner_id: int,
    txn_rows: list[tuple],
    journal_raw: list[dict],
    journal_division: int = 0,
    cfg=None,
    now: Optional[datetime] = None,
) -> dict:
    """Archive wallet transactions + journal after the snapshot write.

    `txn_rows` is the same shape as `storage.replace_wallet_transactions`
    ((division, transaction_id, date, type_id, location_id, unit_price,
    quantity, is_buy, journal_ref_id), ...). Journal is the raw ESI list
    (needs context_id etc.).
    """
    cfg = cfg if cfg is not None else production_config.PRODUCTION_CONFIG
    now = _now(now)
    _check_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="wallet_transactions",
        now=now, division=journal_division, window_days=WALLET_WINDOW_DAYS,
    )
    inserted = _archive_txn_rows(owner_type, owner_id, txn_rows, _TxnEnricher(cfg))
    _stamp(owner_type, owner_id, "wallet_transactions", now, journal_division)
    journal = _archive_journal(owner_type, owner_id, journal_division, journal_raw, now)
    return {"transactions": inserted, "journal": journal}


def archive_corp_wallet(
    *,
    owner_id: int,
    txn_rows: list[tuple],
    journal_entries_by_division: dict[int, list[dict]],
    cfg=None,
    now: Optional[datetime] = None,
) -> dict:
    """Corp wallet: txns already carry division; journal and gap checks are
    per division (each division is its own ESI list with its own cap)."""
    cfg = cfg if cfg is not None else production_config.PRODUCTION_CONFIG
    now = _now(now)
    for division in sorted(journal_entries_by_division):
        _check_gap(
            owner_type="corporation", owner_id=owner_id, data_kind="wallet_transactions",
            now=now, division=division, window_days=WALLET_WINDOW_DAYS,
        )
    inserted = _archive_txn_rows("corporation", owner_id, txn_rows, _TxnEnricher(cfg))
    for division in sorted(journal_entries_by_division):
        _stamp("corporation", owner_id, "wallet_transactions", now, division)
    journal = 0
    for division, entries in sorted(journal_entries_by_division.items()):
        journal += _archive_journal("corporation", owner_id, division, entries, now)
    return {"transactions": inserted, "journal": journal}


# ----------------------------------------------------------- industry jobs
def archive_industry_jobs(
    *,
    owner_type: str,
    owner_id: int,
    jobs: list[dict],
    cfg=None,
    now: Optional[datetime] = None,
) -> dict:
    """Archive completed+active jobs (include_completed=true payload).

    Inputs are frozen the first time they can be computed: a job archived
    earlier without inputs (e.g. SDE not loaded yet) is retried on the next
    sync; a job that has inputs is never recomputed.
    """
    now = _now(now)
    if not jobs:
        _check_gap(owner_type=owner_type, owner_id=owner_id, data_kind="industry_jobs",
                   now=now, window_days=INDUSTRY_JOBS_WINDOW_DAYS)
        _stamp(owner_type, owner_id, "industry_jobs", now)
        return {"jobs": 0, "inputs": 0}
    cfg = cfg if cfg is not None else production_config.PRODUCTION_CONFIG
    selected = storage.load_selected_decryptors()

    job_ids = [int(j["job_id"]) for j in jobs if j.get("job_id") is not None]
    has_inputs = db.job_ids_with_inputs(owner_type, owner_id, job_ids)
    known_jobs = db.industry_job_ids_present(owner_type, owner_id, job_ids)

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

        if activity_id in _ACTIVITIES_WITH_INPUTS and int(jid) not in has_inputs:
            frozen = inputs.freeze_job_inputs(
                owner_type=owner_type, owner_id=owner_id, job={**j, "activity_id": activity_id},
                cfg=cfg, selected_decryptors=selected,
            )
            for inp in frozen:
                input_rows.append(inputs.frozen_to_row(owner_type, owner_id, int(jid), inp))

    _check_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="industry_jobs",
        now=now, window_days=INDUSTRY_JOBS_WINDOW_DAYS,
        cap=INDUSTRY_JOBS_ROW_CAP, row_count=len(rows), sync_dates=start_dates,
        table="ledger_industry_jobs", date_column="start_date",
    )
    db.upsert_ledger_industry_jobs(rows)
    db.insert_ledger_job_inputs(input_rows)
    _stamp(owner_type, owner_id, "industry_jobs", now)
    # Planned cost is frozen only for jobs that were not already archived.
    # A lookup failure must not fail the ESI sync, and it never calls ESI.
    new_jobs = [j for j in jobs if j.get("job_id") is not None and int(j["job_id"]) not in known_jobs]
    try:
        from . import planned
        planned.freeze_for_new_jobs(owner_type, int(owner_id), new_jobs)
    except Exception:  # noqa: BLE001
        log.exception("ledger planned-cost freeze failed for %s %s", owner_type, owner_id)
    return {"jobs": len(rows), "inputs": len(input_rows)}


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
    now: Optional[datetime] = None,
) -> dict:
    """Open orders + order history rows (same shape from ESI)."""
    now = _now(now)
    rows: list[tuple] = []
    for o in orders:
        oid = o.get("order_id")
        if oid is None:
            continue
        rows.append((
            owner_type, int(owner_id), int(oid), int(o["type_id"]),
            int(o["location_id"]), o.get("region_id"),
            bool(o.get("is_buy_order")), float(o["price"]),
            int(o.get("volume_total") or o.get("volume_remain") or 0),
            o.get("volume_remain"), o.get("issued"), o.get("state"),
        ))
    _check_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="market_orders",
        now=now, window_days=ORDER_HISTORY_WINDOW_DAYS,
    )
    db.upsert_ledger_market_orders(rows)
    _stamp(owner_type, owner_id, "market_orders", now)
    return {"orders": len(rows)}


# ---------------------------------------------------------------- contracts
def archive_contracts(
    *,
    owner_type: str,
    owner_id: int,
    contracts: list[dict],
    items_by_contract: dict[int, list[dict]],
    now: Optional[datetime] = None,
) -> dict:
    """Item-exchange contracts where this owner is issuer or acceptor, plus
    finished courier contracts this owner issued. Couriers are archived for
    the freight comparison and never enter costing. Item lines freeze The
    Forge freight the first time they are inserted."""
    now = _now(now)
    rows: list[tuple] = []
    item_rows: list[tuple] = []
    enricher = _TxnEnricher(production_config.PRODUCTION_CONFIG)
    for c in contracts:
        ctype = c.get("type")
        issued = contract_issuer_matches(c, owner_type=owner_type, owner_id=owner_id)
        if ctype == _COURIER:
            if (c.get("status") or "") != "finished" or not issued:
                continue
        elif ctype != _ITEM_EXCHANGE:
            continue
        cid = c.get("contract_id")
        if cid is None:
            continue
        start = c.get("start_location_id")
        region = enricher.region(int(start)) if start is not None else None
        rows.append((
            owner_type, int(owner_id), int(cid),
            c.get("issuer_id"), c.get("issuer_corporation_id"), c.get("acceptor_id"),
            start, c.get("end_location_id"),
            ctype, c.get("status") or "",
            c.get("title"), c.get("price"), c.get("reward"),
            c.get("date_issued"), c.get("date_completed"), c.get("date_expired"),
            c.get("volume"), c.get("collateral"), region,
        ))
        if ctype != _ITEM_EXCHANGE:
            continue
        for it in items_by_contract.get(int(cid), []):
            quantity = int(it["quantity"])
            rate, m3, frozen = None, None, False
            if start is not None:
                rate, m3, frozen = _contract_item_freight(enricher, int(start), int(it["type_id"]), quantity)
            item_rows.append((
                owner_type, int(owner_id), int(cid), int(it["record_id"]),
                int(it["type_id"]), quantity,
                bool(it.get("is_included", True)), bool(it.get("is_singleton", False)),
                rate, m3, frozen,
            ))
    _check_gap(
        owner_type=owner_type, owner_id=owner_id, data_kind="contracts",
        now=now, window_days=CONTRACTS_WINDOW_DAYS,
    )
    db.upsert_ledger_contracts(rows)
    db.upsert_ledger_contract_items(item_rows)
    _stamp(owner_type, owner_id, "contracts", now)
    return {"contracts": len(rows), "items": len(item_rows)}


def _contract_item_freight(enricher: _TxnEnricher, start_location: int, type_id: int, quantity: float):
    """(rate, m3, frozen). Unresolved region stays unfrozen."""
    region = enricher.region(int(start_location))
    if region is None:
        return None, None, False
    rate, m3 = enricher.freight(region, int(start_location), int(type_id), quantity, True)
    return rate, m3, True


def contract_issuer_matches(
    contract: dict, *, owner_type: str, owner_id: int,
) -> bool:
    """True when this owner issued the contract. A character matches
    issuer_id; a corporation matches issuer_corporation_id."""
    oid = int(owner_id)
    if owner_type == "character":
        return contract.get("issuer_id") == oid
    return contract.get("issuer_corporation_id") == oid


def contract_party_matches(
    contract: dict, *, owner_type: str, owner_id: int, corporation_id: Optional[int] = None,
) -> bool:
    """True when this tenant owner is issuer or acceptor of the contract."""
    oid = int(owner_id)
    if owner_type == "character":
        return contract.get("issuer_id") == oid or contract.get("acceptor_id") == oid
    return (
        contract.get("issuer_corporation_id") == oid
        or contract.get("acceptor_id") == oid
    )
