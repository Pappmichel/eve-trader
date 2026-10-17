"""Fail-closed ledger reads (D11).

Every archive read checks `is_shared(owner, kind, "production")` at read
time. Revoking a share hides that owner's rows from costing and the report;
rows stay in the archive and return if the share is granted again.
"""
from __future__ import annotations

from ...esi_data.access import is_shared, shared_owner_ids
from . import db

# data_kind each ledger table is gated on
_KIND_BY_TABLE = {
    "ledger_wallet_transactions": "wallet",
    "ledger_wallet_journal": "wallet",
    "ledger_industry_jobs": "industry_jobs",
    "ledger_job_inputs": "industry_jobs",
    "ledger_blueprints": "blueprints",
    "ledger_market_orders": "market_orders",
    "ledger_contracts": "contracts",
    "ledger_contract_items": "contracts",
    "ledger_archive_gaps": None,  # gaps are tenant-wide diagnostics; no owner share
}


def owner_shared_with_production(data_kind: str, owner_type: str, owner_id: int) -> bool:
    return is_shared(data_kind, "production", owner_type, int(owner_id))


def shared_owners(data_kind: str) -> list[tuple[str, int]]:
    """Every (owner_type, owner_id) shared with Production for `data_kind`."""
    out: list[tuple[str, int]] = []
    for owner_type in ("character", "corporation"):
        for oid in shared_owner_ids(data_kind, "production", owner_type):
            out.append((owner_type, int(oid)))
    return out


# Every kind a rebuild reads. A change in who shares any of them changes
# what the derived tables may show.
SCOPE_KINDS = ("wallet", "industry_jobs", "market_orders", "contracts", "assets", "blueprints")


def owner_scope_hash() -> str:
    """Fingerprint of the owners shared with Production per kind in
    SCOPE_KINDS. A rebuild stores it; the report compares it on every read
    and hides a run built from owners that are no longer shared (D11)."""
    import hashlib
    import json

    scope = {kind: sorted([t, int(i)] for t, i in shared_owners(kind)) for kind in SCOPE_KINDS}
    text = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def list_wallet_transactions_for_production(
    owner_type: str, owner_id: int,
) -> list[tuple]:
    if not owner_shared_with_production("wallet", owner_type, owner_id):
        return []
    return db.list_ledger_wallet_transactions(owner_type, owner_id)


def list_industry_jobs_for_production(
    owner_type: str, owner_id: int,
) -> list[tuple]:
    if not owner_shared_with_production("industry_jobs", owner_type, owner_id):
        return []
    return db.list_ledger_industry_jobs(owner_type, owner_id)


def list_job_inputs_for_production(
    owner_type: str, owner_id: int, job_id: int,
) -> list[tuple]:
    if not owner_shared_with_production("industry_jobs", owner_type, owner_id):
        return []
    return db.list_ledger_job_inputs(owner_type, owner_id, job_id)


def count_archived_for_production(
    table: str, owner_type: str, owner_id: int,
) -> int:
    """Row count visible to Production for this owner, or 0 when not shared."""
    kind = _KIND_BY_TABLE.get(table)
    if kind is None:
        return db.count_ledger_rows(table, owner_type, owner_id)
    if not owner_shared_with_production(kind, owner_type, owner_id):
        return 0
    return db.count_ledger_rows(table, owner_type, owner_id)


# ------------------------------------------------------------------ bulk (Phase 2)


def list_all_wallet_transactions_for_production() -> list[tuple]:
    owners = shared_owners("wallet")
    if not owners:
        return []
    return db.list_wallet_transactions_for_owners(owners)


def list_all_wallet_journal_for_production() -> list[tuple]:
    owners = shared_owners("wallet")
    if not owners:
        return []
    return db.list_wallet_journal_for_owners(owners)


def list_all_market_orders_for_production() -> list[tuple]:
    owners = shared_owners("market_orders")
    if not owners:
        return []
    return db.list_market_orders_for_owners(owners)


def list_all_industry_jobs_for_production() -> list[tuple]:
    owners = shared_owners("industry_jobs")
    if not owners:
        return []
    return db.list_industry_jobs_for_owners(owners)


def list_all_job_inputs_for_production() -> list[tuple]:
    owners = shared_owners("industry_jobs")
    if not owners:
        return []
    return db.list_job_inputs_for_owners(owners)


class ArchiveStillShared(Exception):
    """The kind is still shared with Production, so its archive must stay."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(
            f"{kind} is shared with Production; stop sharing it before deleting archived rows"
        )


def _check_hidden_archive(owner_type: str, owner_id: int, kind: str) -> None:
    if owner_type not in ("character", "corporation"):
        raise ValueError("owner_type must be character or corporation")
    db.hidden_archive_kind(kind)
    if owner_shared_with_production(kind, owner_type, int(owner_id)):
        raise ArchiveStillShared(kind)


def count_hidden_archive_rows(owner_type: str, owner_id: int, kind: str) -> int:
    """How many archived rows of `kind` this owner has. Refuses while the
    kind is shared with Production."""
    _check_hidden_archive(owner_type, owner_id, kind)
    return db.count_owner_archive_rows(owner_type, int(owner_id), kind)


def delete_hidden_archive_rows(owner_type: str, owner_id: int, kind: str) -> int:
    """Delete that owner's archive rows, sync state and gaps for `kind`.
    Refuses while the kind is shared with Production."""
    _check_hidden_archive(owner_type, owner_id, kind)
    return db.delete_owner_archive_kind(owner_type, int(owner_id), kind)


def cutover_t0_for_production():
    """Cutover T0 = start of the shortest complete window (plan section 6).

    Transactions reach 30 days back, the journal only its newest 10,000
    entries (Phase 0 13a) - about a week for a busy seller. A wallet whose
    oldest archived transaction is older than its oldest archived journal
    entry lost journal history, so sales before that point have no sales tax
    or broker fees: T0 moves to the latest such journal start. A quiet
    wallet whose journal simply starts with its first activity is not
    truncated and does not move T0. None while no wallet is archived."""
    owners = shared_owners("wallet")
    if not owners:
        return None
    starts = db.wallet_window_starts(owners)
    if not starts:
        return None
    t0 = min(row[3] for row in starts)
    for _ot, _oid, _div, tx_start, journal_start in starts:
        if journal_start is not None and tx_start < journal_start:
            t0 = max(t0, journal_start)
    return t0
