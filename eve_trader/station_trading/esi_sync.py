"""Character registration for the Station Trading tool's own "trader" role -
see eve_trader.auth.ROLE_PREFIX_TOOL / validate_role_key_for_tool for how a
stored prefix key is namespaced to this tool, and production/esi_sync.py's
PRODUCTION_ROLE_PREFIX/list_producer_characters for the precedent this
mirrors. HTTP login is the Characters page, not a prefix `/start`.

Unlike Production, there's no sync_esi()-style bulk pull here: nothing this
tool computes is worth caching ahead of time (own orders and skill levels
are both read live, on demand, by actions.py - see undercut.py and this
module's own STATION_TRADING_SCOPES for why: an undercut check is only ever
meaningful against the current live order book, and a skill level changes
rarely enough that a live per-request pull costs nothing worth caching).
"""
from __future__ import annotations

from ..auth import TokenManager
from ..config import OAUTH_CONFIG

STATION_TRADING_ROLE_PREFIX = "trader"

STATION_TRADING_SCOPES = [
    "esi-markets.read_character_orders.v1",
    "esi-skills.read_skills.v1",
]


def list_trader_characters(tm: TokenManager | None = None) -> list[tuple[str, int, str]]:
    """Deprecated (bug found 2026-09-21, same class as docs/
    ESI_ACCESS_PLAN.md's Known gap 4): discovers by the legacy `trader:<id>`
    token prefix, so a character added via the Characters page's
    add-a-character path (`esi:<id>`, gap 1) is invisible here regardless
    of sharing. Superseded by list_shared_trader_characters below. Kept
    only because deleting a function with real test coverage on a whim is
    its own risk; do not add a new caller of this one."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(STATION_TRADING_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
    return out


def list_shared_trader_characters(tm: TokenManager | None = None) -> list[tuple[str, int, str]]:
    """Returns (auth_role, character_id, character_name) for every character
    currently sharing Market Orders and/or Skills with `station_trading` -
    the sharing-based replacement for `list_trader_characters` (Known-
    gap-4-class bug, closed 2026-09-21). Same shape as production/
    esi_sync.py's `list_shared_producer_characters` - see that function's
    own docstring for the full reasoning."""
    from ..esi_data.access import shared_owner_ids
    from ..esi_data.selector import select_auth_role

    tm = tm or TokenManager(OAUTH_CONFIG)
    char_ids = sorted(
        set(shared_owner_ids("market_orders", "station_trading", "character"))
        | set(shared_owner_ids("skills", "station_trading", "character"))
    )
    out = []
    for character_id in char_ids:
        role = (
            select_auth_role(character_id, "esi-markets.read_character_orders.v1", tokens=tm)
            or select_auth_role(character_id, "esi-skills.read_skills.v1", tokens=tm)
        )
        if role is None:
            continue
        record = tm.get_record(role)
        out.append((role, character_id, record.character_name if record else str(character_id)))
    return out
