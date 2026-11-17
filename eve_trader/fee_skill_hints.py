"""I/O half of the fee hints: one chosen character's skills and standings ->
the fees `fee_skills` computes, for a Settings page to show beside its fee
fields.

Each tool picks one character in its Settings (`fee_skill_character_id` /
`pi_fee_skill_character_id`). Picking one changes no fee by itself: the page
shows the value with "Use this value", and the click goes through the
tool's normal settings save.

Skills and standings are read only through the fail-closed accessor, gated
like the Character Management pages (`character_management.fields`): a
character that does not share the kind with this tool yields `not_shared`,
one without a token holding the scope `reauth_needed`, one never synced
`not_synced`. Standings are an on-demand kind (the scheduler does not fetch
them); without them the broker fee is still shown, labeled as an upper bound,
because a positive standing can only lower it.
"""
from __future__ import annotations

from typing import Optional

from . import fee_skills, storage

REASON_NO_CHARACTER = "no_character_selected"
REASON_PLAYER_STRUCTURE = "player_structure"

# NPC station ids live in this range; an id outside it that the SDE does not
# know is a player structure (ids above 10^12 are not all structures, but a
# station id never leaves this range).
_NPC_STATION_IDS = range(60_000_000, 64_000_000)

_MESSAGES = {
    REASON_NO_CHARACTER: "Choose the character whose skills count (field \"Fee skills character\").",
    "not_shared": "The chosen character does not share this data with this tool (Characters page, Access).",
    "reauth_needed": "No token of the chosen character holds the scope. Re-authorize it on the Characters page.",
    "not_synced": "The chosen character's data has not been synced yet (Characters page).",
    "error": "The chosen character's data could not be read.",
    REASON_PLAYER_STRUCTURE: (
        "A player structure's owner sets its broker fee; skills and standings do not change it."),
}

_UPPER_BOUND_TEXT = {
    "not_shared": "Standings are not shared with this tool, so they are left out.",
    "reauth_needed": "No token holds the standings scope, so standings are left out.",
    "not_synced": "Standings have not been synced (they are fetched on demand), so they are left out.",
    "error": "Standings could not be read, so they are left out.",
    "no_station": "No station is known for this hub, so standings are left out.",
    "station_owner_unknown": "The SDE names no owner for this station (refresh the SDE), so standings are left out.",
}


def message(reason: Optional[str]) -> Optional[str]:
    return None if reason is None else _MESSAGES.get(reason, reason)


def _names() -> dict[int, str]:
    from .character_management import fields

    return {int(c["character_id"]): c["character_name"] for c in fields.token_characters()}


def candidates(tool_key: str) -> list[dict]:
    """Characters that share `skills` with `tool_key` (the ones worth picking),
    with whether they also share `standings`. Sorted by name."""
    from .esi_data.access import shared_owner_ids

    names = _names()
    skills = set(shared_owner_ids("skills", tool_key, "character"))
    standings = set(shared_owner_ids("standings", tool_key, "character"))
    out = [
        {"character_id": cid, "character_name": names.get(cid) or f"#{cid}",
         "shares_standings": cid in standings}
        for cid in skills
    ]
    return sorted(out, key=lambda c: (c["character_name"].lower(), c["character_id"]))


def classify_location(location_id: Optional[int]) -> tuple[str, Optional[dict]]:
    """('none' | 'npc_station' | 'player_structure', SDE owner or None)."""
    if not location_id:
        return "none", None
    owner = storage.npc_station_owner(int(location_id))
    if owner is not None:
        return "npc_station", owner
    if int(location_id) in _NPC_STATION_IDS:
        return "npc_station", None
    return "player_structure", None


def _skill_part(setting: str, rate: Optional[float], reason: Optional[str]) -> dict:
    return {
        "setting": setting,
        "source": "skills",
        "suggested": None if rate is None else round(rate, 4),
        "rate": rate,
        "characters": 0 if rate is None else 1,
        "upper_bound": False,
        "reason": reason,
        "message": message(reason),
    }


def skill_fee_hints(
    tool_key: str,
    character_id: Optional[int],
    *,
    location_id: Optional[int],
    sales_tax_setting: Optional[str] = None,
    broker_setting: Optional[str] = None,
) -> dict:
    """Skill-based sales tax and/or broker fee of one character for one
    market location. Pass the setting names to compute (None skips one)."""
    from .auth import TokenManager
    from .character_management import fields
    from .config import OAUTH_CONFIG

    kind, owner = classify_location(location_id)
    out: dict = {
        "tool_key": tool_key,
        "character_id": character_id,
        "character_name": None,
        "candidates": candidates(tool_key),
        "reason": None,
        "message": None,
        "skills_as_of": None,
        "skill_levels": None,
        "location": {
            "location_id": location_id, "kind": kind,
            "name": (owner or {}).get("station_name"),
            "corporation_id": (owner or {}).get("corporation_id"),
            "faction_id": (owner or {}).get("faction_id"),
        },
        "sales_tax": None,
        "broker_fee": None,
    }

    def _finish(reason: Optional[str], levels: Optional[dict] = None, standings: Optional[dict] = None) -> dict:
        out["reason"], out["message"] = reason, message(reason)
        if sales_tax_setting:
            rate = None if levels is None else fee_skills.sales_tax_rate(
                levels.get(fee_skills.SKILL_ACCOUNTING, 0))
            out["sales_tax"] = _skill_part(sales_tax_setting, rate, reason)
        if broker_setting:
            out["broker_fee"] = _broker_part(broker_setting, kind, owner, levels, standings, reason)
        return out

    if character_id is None:
        return _finish(REASON_NO_CHARACTER)
    cid = int(character_id)
    out["character_name"] = _names().get(cid) or f"#{cid}"
    tokens = TokenManager(OAUTH_CONFIG)
    freshness = fields.freshness_by_kind(cid)
    skills = fields.snapshot("skills", tool_key, cid, tokens, freshness,
                             shape=fee_skills.skill_levels, table="skills")
    if skills["state"] != fields.STATE_OK:
        return _finish(skills["state"])
    levels = skills["value"]
    out["skills_as_of"] = skills.get("synced_at")
    out["skill_levels"] = {
        "accounting": levels.get(fee_skills.SKILL_ACCOUNTING, 0),
        "broker_relations": levels.get(fee_skills.SKILL_BROKER_RELATIONS, 0),
    }
    standings = None
    if broker_setting and kind == "npc_station":
        standings = fields.snapshot("standings", tool_key, cid, tokens, freshness, shape=list)
    return _finish(None, levels, standings)


def _broker_part(setting: str, kind: str, owner: Optional[dict], levels: Optional[dict],
                 standings: Optional[dict], reason: Optional[str]) -> dict:
    part = _skill_part(setting, None, reason)
    part.update({"source": "skills_standings", "standings": None, "upper_bound_reason": None,
                 "upper_bound_message": None})
    if reason is not None:
        return part
    if kind == "player_structure":
        part["reason"], part["message"] = REASON_PLAYER_STRUCTURE, message(REASON_PLAYER_STRUCTURE)
        return part
    br = levels.get(fee_skills.SKILL_BROKER_RELATIONS, 0)
    faction = corporation = 0.0
    bound: Optional[str] = None
    if kind == "none":
        bound = "no_station"
    elif owner is None or owner.get("corporation_id") is None:
        bound = "station_owner_unknown"
    elif standings is None or standings["state"] != "ok":
        bound = "not_synced" if standings is None else standings["state"]
    else:
        rows = standings["value"]
        faction = fee_skills.standing_from(rows, "faction", owner.get("faction_id"))
        corporation = fee_skills.standing_from(rows, "npc_corp", owner.get("corporation_id"))
        part["standings"] = {
            "faction_id": owner.get("faction_id"), "faction_standing": faction,
            "corporation_id": owner.get("corporation_id"), "corporation_standing": corporation,
            "as_of": standings.get("synced_at"),
        }
    rate = fee_skills.npc_broker_fee_rate(br, faction, corporation)
    part.update({
        "suggested": round(rate, 4), "rate": rate, "characters": 1,
        "source": "skills" if bound else "skills_standings",
        "upper_bound": bound is not None,
        "upper_bound_reason": bound,
        "upper_bound_message": None if bound is None else _UPPER_BOUND_TEXT.get(bound, bound),
    })
    return part
