"""Fail-closed ledger reads (D11, unified plan U3a).

The archive and the costing read the union of owners who share a kind with
any realized-profit tool. Revoking the last such share hides that owner's
rows; rows stay in the archive and return when a share is granted again.
A report asks `shared_owners_for` for its own tool.
"""
from __future__ import annotations

from ..esi_data.access import is_shared, shared_owner_ids
from . import db

# Tools whose shares feed one realized-profit ledger.
REALIZED_TOOLS = ("production", "trading", "doctrine", "station_trading")

# Which of those tools count for each kind. Jobs and blueprints stay
# Production (plan section 5.1). Market orders and assets follow the
# realized tools that already consume them, so a Trading-only wallet still
# has orders for the fee pool and assets for opening stock.
KIND_TOOLS = {
    "wallet": ("production", "trading", "station_trading"),
    "contracts": ("production", "doctrine"),
    "industry_jobs": ("production",),
    "blueprints": ("production",),
    "market_orders": ("production", "trading", "station_trading"),
    "assets": ("production", "trading", "doctrine"),
}

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


def tools_for_kind(data_kind: str) -> tuple[str, ...]:
    return KIND_TOOLS.get(data_kind, ())


def owner_in_scope(data_kind: str, owner_type: str, owner_id: int) -> bool:
    """True when any realized tool may read this owner for `data_kind`."""
    return any(
        is_shared(data_kind, tool, owner_type, int(owner_id))
        for tool in tools_for_kind(data_kind)
    )


def owner_shared_with_production(data_kind: str, owner_type: str, owner_id: int) -> bool:
    """Ledger read gate. The name is historical: the check is the U3a union,
    not Production alone."""
    return owner_in_scope(data_kind, owner_type, int(owner_id))


def shared_owners_for(data_kind: str, tool_key: str) -> list[tuple[str, int]]:
    """Owners who share `data_kind` with one tool. Report filters use this."""
    if tool_key not in REALIZED_TOOLS:
        raise ValueError(f"unknown realized tool {tool_key!r}")
    out: list[tuple[str, int]] = []
    for owner_type in ("character", "corporation"):
        for oid in shared_owner_ids(data_kind, tool_key, owner_type):
            out.append((owner_type, int(oid)))
    return out


def shared_owners(data_kind: str) -> list[tuple[str, int]]:
    """Union of owners shared with any realized tool for `data_kind`."""
    seen: set[tuple[str, int]] = set()
    for tool in tools_for_kind(data_kind):
        seen.update(shared_owners_for(data_kind, tool))
    return sorted(seen)


# Every kind a rebuild reads. A change in who shares any of them changes
# what the derived tables may show.
SCOPE_KINDS = ("wallet", "industry_jobs", "market_orders", "contracts", "assets", "blueprints")


def owner_scope_hash() -> str:
    """Fingerprint of the realized-tool union per kind in SCOPE_KINDS.
    A rebuild stores it; the report compares it on every read and hides a
    run built from owners that are no longer shared (D11 / U3a)."""
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
    """The kind is still shared with a realized-profit tool, so its archive must stay."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(
            f"{kind} is shared with a realized-profit tool; stop sharing it before deleting archived rows"
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
