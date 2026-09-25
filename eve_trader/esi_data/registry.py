"""ESI data-kind registry: names, scopes, consuming tool keys, corp roles,
default freshness tiers, and group-3 access capabilities.

Pure data. Tool keys are strings (the same strings as
`access_gate.ALL_TOOL_KEYS`). This module must not import a tool package
to ask what it consumes — see `docs/ESI_ACCESS_PLAN.md` settled
decision 8 and this package's docstring.

Kind `key` values are the stable identifiers later phases store on the
sharing / freshness tables. Labels match the plan's vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Freshness tiers (settled decision 5). Group 3 is not scheduled.
TIER_FREQUENT = "frequent"
TIER_NORMAL = "normal"
TIER_RARE = "rare"

GROUP_1 = 1  # owned data, character and corporation variant
GROUP_2 = 2  # owned data, character only
GROUP_3 = 3  # access capabilities: no freshness, no per-tool sharing


@dataclass(frozen=True)
class OwnedDataKind:
    """Group 1 or 2. `consuming_tools` are tool_key strings, not imports."""
    key: str
    label: str
    group: int
    character_scope: str
    corporation_scope: Optional[str]
    corp_roles: tuple[str, ...]
    consuming_tools: tuple[str, ...]
    freshness_tier: str


@dataclass(frozen=True)
class AccessCapability:
    """Group 3. On or off for a character. No tool dimension, no freshness.

    Structure name resolution carries both character and corporation
    scopes on one capability, not two kinds.
    """
    key: str
    label: str
    character_scope: str
    corporation_scope: Optional[str]
    corp_roles: tuple[str, ...]


# Consuming sets are what the code actually reads today
# (`docs/ESI_ACCESS_PLAN.md` "Data kinds"), not what a sidebar offers.
# `refining`, `admin` are not consumers. `"characters"` is the management
# tool (Phase 6 grant), not a consumer of raw ESI rows. `"portfolio"`
# became a real consumer of assets/blueprints/wallet_balance in the
# Portfolio rework (PORTFOLIO_REWORK_PLAN.md) - Total Wealth.
OWNED_DATA_KINDS: tuple[OwnedDataKind, ...] = (
    OwnedDataKind(
        key="assets",
        label="Assets",
        group=GROUP_1,
        character_scope="esi-assets.read_assets.v1",
        corporation_scope="esi-assets.read_corporation_assets.v1",
        corp_roles=("Director",),
        # "portfolio" added for Total Wealth (PORTFOLIO_REWORK_PLAN.md
        # section 4) - a character sharing assets with another tool does
        # NOT also expose them to Portfolio; this is its own opt-in row.
        consuming_tools=("production", "doctrine", "sorting", "trading", "portfolio"),
        freshness_tier=TIER_NORMAL,
    ),
    OwnedDataKind(
        key="industry_jobs",
        label="Industry Jobs",
        group=GROUP_1,
        character_scope="esi-industry.read_character_jobs.v1",
        corporation_scope="esi-industry.read_corporation_jobs.v1",
        corp_roles=("Director",),
        consuming_tools=("production",),
        freshness_tier=TIER_NORMAL,
    ),
    OwnedDataKind(
        key="blueprints",
        label="Blueprints",
        group=GROUP_1,
        character_scope="esi-characters.read_blueprints.v1",
        corporation_scope="esi-corporations.read_blueprints.v1",
        corp_roles=("Director",),
        # "portfolio" added for Total Wealth - same opt-in reasoning as
        # assets above.
        consuming_tools=("production", "portfolio"),
        freshness_tier=TIER_RARE,
    ),
    OwnedDataKind(
        key="market_orders",
        label="Market Orders",
        group=GROUP_1,
        character_scope="esi-markets.read_character_orders.v1",
        corporation_scope="esi-markets.read_corporation_orders.v1",
        corp_roles=("Accountant", "Trader"),
        consuming_tools=("trading", "production", "station_trading"),
        freshness_tier=TIER_FREQUENT,
    ),
    OwnedDataKind(
        key="contracts",
        label="Contracts",
        group=GROUP_1,
        character_scope="esi-contracts.read_character_contracts.v1",
        corporation_scope="esi-contracts.read_corporation_contracts.v1",
        # ESIClient.corporation_contracts documents the scope only — do
        # not invent a Director-or-otherwise role to fill the table.
        corp_roles=(),
        consuming_tools=("doctrine",),
        freshness_tier=TIER_NORMAL,
    ),
    OwnedDataKind(
        key="wallet",
        label="Wallet",
        group=GROUP_1,
        character_scope="esi-wallet.read_character_wallet.v1",
        corporation_scope="esi-wallet.read_corporation_wallets.v1",
        # Plan table said Accountant; Phase 8 / ESI swagger lists
        # Accountant and Junior_Accountant identically on the corp
        # wallet routes. Both, not a newly invented role.
        corp_roles=("Accountant", "Junior_Accountant"),
        consuming_tools=("trading",),
        freshness_tier=TIER_FREQUENT,
    ),
    OwnedDataKind(
        key="wallet_balance",
        label="Wallet Balance",
        group=GROUP_1,
        # Same scopes as "wallet" above (esi-wallet.read_character_wallet.v1
        # covers both the transactions/journal endpoints and the plain
        # balance endpoint - no new grant needed). A separate data kind,
        # not folded into "wallet", because it's the only one Portfolio's
        # Total Wealth needs shared with it (PORTFOLIO_REWORK_PLAN.md
        # section 3) - Trading's own wallet reconciliation keeps consuming
        # "wallet" unchanged.
        character_scope="esi-wallet.read_character_wallet.v1",
        corporation_scope="esi-wallet.read_corporation_wallets.v1",
        corp_roles=("Accountant", "Junior_Accountant"),
        consuming_tools=("portfolio",),
        freshness_tier=TIER_FREQUENT,
    ),
    OwnedDataKind(
        key="skills",
        label="Skills",
        group=GROUP_2,
        character_scope="esi-skills.read_skills.v1",
        corporation_scope=None,
        corp_roles=(),
        consuming_tools=("production", "station_trading"),
        freshness_tier=TIER_RARE,
    ),
)

ACCESS_CAPABILITIES: tuple[AccessCapability, ...] = (
    AccessCapability(
        key="structure_name_resolution",
        label="Structure name resolution",
        character_scope="esi-universe.read_structures.v1",
        corporation_scope="esi-corporations.read_structures.v1",
        corp_roles=("Station_Manager",),
    ),
    AccessCapability(
        key="structure_market_book",
        label="Structure market book",
        character_scope="esi-markets.structure_markets.v1",
        corporation_scope=None,
        corp_roles=(),
    ),
    AccessCapability(
        key="corporation_roles",
        label="Corporation roles",
        # docs/ESI_ACCESS_PLAN.md Known gap 2 (the Corporations table's
        # role warning): ESI does not expose a character's corp roles
        # without this scope, and nothing in this app requested it before
        # gap 2 closed. Character-only - there is no separate "corporation
        # roles of corporation X" endpoint; each member reports their own.
        character_scope="esi-characters.read_corporation_roles.v1",
        corporation_scope=None,
        corp_roles=(),
    ),
)


def fetcher_scope_map() -> dict[tuple[str, str], tuple[str, ...]]:
    """`(kind_or_capability_key, owner_type)` → scopes that fetch needs.

    Group 3 is included so every registry scope appears here even though
    those capabilities are not orchestrator kinds (Access asks which
    characters can provide the capability; name resolution stays
    opportunistic). `owner_type` is `'character'` or `'corporation'`.
    """
    out: dict[tuple[str, str], tuple[str, ...]] = {}
    for kind in OWNED_DATA_KINDS:
        out[(kind.key, "character")] = (kind.character_scope,)
        if kind.corporation_scope is not None:
            out[(kind.key, "corporation")] = (kind.corporation_scope,)
    for cap in ACCESS_CAPABILITIES:
        out[(cap.key, "character")] = (cap.character_scope,)
        if cap.corporation_scope is not None:
            out[(cap.key, "corporation")] = (cap.corporation_scope,)
    return out


def all_registry_scopes() -> frozenset[str]:
    scopes: set[str] = set()
    for kind in OWNED_DATA_KINDS:
        scopes.add(kind.character_scope)
        if kind.corporation_scope is not None:
            scopes.add(kind.corporation_scope)
    for cap in ACCESS_CAPABILITIES:
        scopes.add(cap.character_scope)
        if cap.corporation_scope is not None:
            scopes.add(cap.corporation_scope)
    return frozenset(scopes)


def consuming_tool_keys() -> frozenset[str]:
    keys: set[str] = set()
    for kind in OWNED_DATA_KINDS:
        keys.update(kind.consuming_tools)
    return frozenset(keys)
