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


def cutover_t0_for_production():
    """Oldest wallet transaction among owners shared for wallet, or None."""
    owners = shared_owners("wallet")
    if not owners:
        return None
    return db.oldest_wallet_transaction_date(owners)
