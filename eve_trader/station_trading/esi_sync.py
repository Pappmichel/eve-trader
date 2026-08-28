"""Character registration for the Station Trading tool's own "trader" role -
see api/routers/auth.py's ROLE_PREFIX_TOOL/_scopes_for for how a role_prefix
becomes a real login, and production/esi_sync.py's PRODUCTION_ROLE_PREFIX/
list_producer_characters for the precedent this mirrors.

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
    """Returns (role_key, character_id, character_name) for every registered
    trader character, e.g. [("trader:2112625428", 2112625428, "Some Character")]
    - see production/esi_sync.py's list_producer_characters for why get_record
    (no refresh) is used here, not get_token."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(STATION_TRADING_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
    return out
