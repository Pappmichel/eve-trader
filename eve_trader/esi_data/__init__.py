"""Character-centric ESI access: registry, fetchers, orchestrator, accessor,
Characters `do_*` (`docs/ESI_ACCESS_PLAN.md`).

This package is the third cross-cutting module alongside `portfolio.py`
and `scheduler.py`. It names tools as strings and imports no tool
package — not `eve_trader.production`, not `eve_trader.doctrine`, not
`eve_trader.sorting`. A registry that imported a tool to ask "do you
consume assets?" would rebuild the coupling this package exists to
break. Same precedent as `auth.TOOL_ROLE_PREFIXES` and
`access_gate.ALL_TOOL_KEYS`.
"""

from .registry import (
    ACCESS_CAPABILITIES,
    GROUP_1,
    GROUP_2,
    GROUP_3,
    OWNED_DATA_KINDS,
    TIER_FREQUENT,
    TIER_NORMAL,
    TIER_RARE,
    AccessCapability,
    OwnedDataKind,
    all_registry_scopes,
    consuming_tool_keys,
    fetcher_scope_map,
)
from .backfill import backfill_conservative_sharing, prefixes_holding_kind
from .stale import DEFAULT_STALE_CLEAR_MULTIPLES, clear_stale_owner_kind
from .access import AccessorError, is_shared, read_esi, shared_owner_ids
from .orchestrator import (
    DEFAULT_TIER_INTERVAL_HOURS,
    do_sync_all,
    do_sync_due,
    do_sync_for_tool,
)
from .selector import (
    REAUTH_NEEDED,
    character_has_token_pool,
    delete_strict_subset_tokens,
    normalize_scopes,
    reauth_write_role,
    select_auth_role,
)

__all__ = (
    "ACCESS_CAPABILITIES",
    "GROUP_1",
    "GROUP_2",
    "GROUP_3",
    "OWNED_DATA_KINDS",
    "TIER_FREQUENT",
    "TIER_NORMAL",
    "TIER_RARE",
    "AccessCapability",
    "OwnedDataKind",
    "all_registry_scopes",
    "consuming_tool_keys",
    "fetcher_scope_map",
    "backfill_conservative_sharing",
    "prefixes_holding_kind",
    "DEFAULT_STALE_CLEAR_MULTIPLES",
    "clear_stale_owner_kind",
    "AccessorError",
    "is_shared",
    "read_esi",
    "shared_owner_ids",
    "DEFAULT_TIER_INTERVAL_HOURS",
    "do_sync_all",
    "do_sync_due",
    "do_sync_for_tool",
    "REAUTH_NEEDED",
    "character_has_token_pool",
    "delete_strict_subset_tokens",
    "normalize_scopes",
    "reauth_write_role",
    "select_auth_role",
)
