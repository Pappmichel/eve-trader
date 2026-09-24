"""Age-limit clear for per-owner ESI snapshot partitions (decision 6).

`stale_clear_multiples` is a parameter (default
`DEFAULT_STALE_CLEAR_MULTIPLES`). Phase 7 added
`TradingConfig.esi_stale_clear_multiples`; the orchestrator passes it
in. Do not add the field a second time or re-derive the clear.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .. import storage

# Same knob for every freshness tier (decision 6). Frequent data that has
# failed for 3× its interval is dropped; rare data gets a proportionally
# longer grace. Phase 7 reads this default into config.
DEFAULT_STALE_CLEAR_MULTIPLES = 3

# skills -> character_slots is deliberately absent: that write path is an
# UPSERT so excluded_from_planning survives a re-sync (GitHub issue #39).
_KIND_TABLES: dict[str, dict[str, tuple[str, ...]]] = {
    "assets": {
        "character": ("character_assets",),
        "corporation": ("corp_assets",),
    },
    "industry_jobs": {
        "character": ("character_industry_jobs",),
        "corporation": ("corp_industry_jobs",),
    },
    "blueprints": {
        "character": ("character_blueprints",),
        "corporation": ("corp_blueprints",),
    },
    "market_orders": {
        "character": ("character_sell_orders",),
        "corporation": ("character_sell_orders",),
    },
    "contracts": {
        "character": ("doctrine_contracts",),
        "corporation": ("doctrine_contracts",),
    },
    "wallet": {
        "character": ("esi_wallet_transactions", "esi_wallet_journal"),
        "corporation": ("esi_wallet_transactions", "esi_wallet_journal"),
    },
    "wallet_balance": {
        "character": ("character_wallet_balances",),
        "corporation": ("corp_wallet_balances",),
    },
}


def _hours_since(ts, now: datetime) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 3600.0


def clear_stale_owner_kind(
    owner_type: str,
    owner_id: int,
    data_kind: str,
    *,
    tier_interval_hours: float,
    stale_clear_multiples: float = DEFAULT_STALE_CLEAR_MULTIPLES,
    now: Optional[datetime] = None,
    owner_name: Optional[str] = None,
) -> bool:
    """Delete this owner's partition of `data_kind` if `last_success_at` is
    older than `tier_interval_hours * stale_clear_multiples`.

    Returns True if a clear ran. Missing freshness row, NULL
    `last_success_at`, or age still inside the grace window: no-op
    (decision 6 keeps existing rows). `character_slots` is never cleared.
    """
    if data_kind == "skills":
        return False
    tables = _KIND_TABLES.get(data_kind, {}).get(owner_type)
    if not tables:
        return False
    clock = now or datetime.now(timezone.utc)
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT last_success_at FROM esi_freshness "
            "WHERE owner_type = ? AND owner_id = ? AND data_kind = ?",
            (owner_type, owner_id, data_kind),
        ).fetchone()
    if row is None:
        return False
    age_hours = _hours_since(row[0], clock)
    if age_hours is None:
        return False
    if age_hours <= tier_interval_hours * stale_clear_multiples:
        return False
    kwargs: dict = {"owner_name": owner_name}
    if owner_type == "character":
        kwargs["owner_character_id"] = owner_id
    else:
        kwargs["owner_corporation_id"] = owner_id
    for table in tables:
        storage.delete_owner_snapshot_rows(table, **kwargs)
    return True
