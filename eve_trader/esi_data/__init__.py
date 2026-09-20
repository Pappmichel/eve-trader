"""Character-centric ESI access: registry, and later fetchers / orchestrator
/ Characters `do_*` (`docs/ESI_ACCESS_PLAN.md`).

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
from .backfill import backfill_conservative_sharing

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
)
