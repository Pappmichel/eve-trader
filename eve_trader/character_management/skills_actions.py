"""Skills `do_*` actions (docs/CHARACTER_MANAGEMENT_PLAN.md phase 2).

UI-agnostic, no FastAPI imports. Reads the per-skill rows, attribute block,
SP totals and skill queue the skills fetchers store, always through
`read_esi(..., "char_skills")` so the Characters-page sharing matrix decides
what shows up. Skill names, groups and ranks come from the SDE cache; until
Admin has applied an SDE refresh that carries `dgmTypeAttributes` the ranks
are simply absent (names/groups only need the older tables).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_data import do_sync_for_tool, read_esi, shared_owner_ids
from . import fields

log = logging.getLogger(__name__)

TOOL_KEY = "char_skills"

# EVE's skill-extractor rule of thumb: a character keeps at least 5,000,000 SP
# and an extractor removes 500,000. This is an *upper bound* - the game also
# limits what can be pulled out of individual skills - so every caller labels
# it an estimate (docs/CHARACTER_MANAGEMENT_PLAN.md R14).
_EXTRACTION_FLOOR_SP = 5_000_000
_EXTRACTOR_SP = 500_000

# SP needed in total to reach level 1..5 is 250 * rank * sqrt(32)^(level-1)
# (250, 1414, 8000, 45255, 256000 for a rank-1 skill).
_LEVEL_V_BASE_SP = 256_000


def extractable_estimate(total_sp: Optional[int]) -> Optional[int]:
    """Skill extractors' worth of SP above the 5,000,000 SP floor (upper
    bound, see module constants). None when the total is unknown."""
    if total_sp is None:
        return None
    return max(0, (int(total_sp) - _EXTRACTION_FLOOR_SP) // _EXTRACTOR_SP)


def sp_to_level_v(rank: Optional[float], skillpoints: int) -> Optional[int]:
    """SP still missing to level V, or None when the rank is not known yet
    (SDE cache without dgmTypeAttributes)."""
    if rank is None:
        return None
    return max(0, round(_LEVEL_V_BASE_SP * rank) - int(skillpoints))


def _token_characters() -> list[dict]:
    """Every character with a token, sorted by name. No network: unlike
    esi_data.actions.do_list_token_characters this does not look up corps."""
    tm = TokenManager(OAUTH_CONFIG)
    by_id: dict[int, str] = {}
    for rec in tm.list_records():
        by_id[rec.character_id] = by_id.get(rec.character_id) or rec.character_name or ""
    return sorted(
        ({"character_id": cid, "character_name": name or f"#{cid}"} for cid, name in by_id.items()),
        key=lambda c: (c["character_name"].lower(), c["character_id"]),
    )


def _skill_name(catalog: dict[int, dict], skill_id: int) -> str:
    return (catalog.get(skill_id) or {}).get("name") or f"Skill {skill_id}"


def _attributes_value(row: Optional[dict]) -> Optional[dict]:
    if row is None or row.get("charisma") is None:
        return None
    return {
        k: row[k] for k in (
            "charisma", "intelligence", "memory", "perception", "willpower",
            "bonus_remaps", "last_remap_date", "accrued_remap_cooldown_date",
        )
    }


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _queue_value(rows: list[dict], catalog: dict[int, dict], now: datetime) -> dict:
    """Queue rows (one character, in order) -> entries plus a summary.

    `paused`: entries exist but none carries a finish date (ESI omits the
    dates of a paused queue). `ends_at`: the last finish date. `empty`: no
    entries at all. Entries whose finish date already passed are still
    listed - the snapshot can be older than the queue."""
    entries = [
        {
            "queue_position": r["queue_position"], "skill_id": r["skill_id"],
            "name": _skill_name(catalog, r["skill_id"]), "finished_level": r["finished_level"],
            "start_date": r["start_date"], "finish_date": r["finish_date"],
            "training_start_sp": r["training_start_sp"], "level_start_sp": r["level_start_sp"],
            "level_end_sp": r["level_end_sp"],
        }
        for r in sorted(rows, key=lambda r: r["queue_position"])
    ]
    dated = [_parse_dt(e["finish_date"]) for e in entries]
    known = [d for d in dated if d is not None]
    current = next(
        (e for e, d in zip(entries, dated) if d is not None and d > now), None,
    )
    return {
        "entries": entries,
        "length": len(entries),
        "empty": not entries,
        "paused": bool(entries) and not known,
        "current": current,
        "ends_at": max(known).isoformat() if known else None,
    }


def do_skills_overview() -> dict:
    """One row per registered ESI character with SP totals, attributes and a
    queue summary."""
    characters = _token_characters()
    if not characters:
        return {"characters": []}
    tokens = TokenManager(OAUTH_CONFIG)
    now = datetime.now(timezone.utc)

    queue_rows = read_esi("skillqueue", TOOL_KEY, owner_type="character")
    catalog = storage.get_skill_catalog({r["skill_id"] for r in queue_rows})

    out = []
    for c in characters:
        cid = c["character_id"]
        freshness = fields.freshness_by_kind(cid)

        def summary_shape(rows: list[dict], _cid: int = cid) -> dict:
            row = rows[0] if rows else None
            total = row["total_sp"] if row else None
            return {
                "total_sp": total,
                "unallocated_sp": row["unallocated_sp"] if row else None,
                "extractable_estimate": extractable_estimate(total),
                "attributes": _attributes_value(row),
            }

        def queue_shape(rows: list[dict], _cid: int = cid) -> dict:
            return _queue_value([r for r in rows if r["owner_id"] == _cid], catalog, now)

        out.append({
            **c,
            "summary": fields.snapshot(
                "skills", TOOL_KEY, cid, tokens, freshness, summary_shape, table="attributes",
            ),
            "queue": fields.snapshot("skillqueue", TOOL_KEY, cid, tokens, freshness, queue_shape),
            "freshness": {k: freshness[k] for k in ("skills", "skillqueue") if k in freshness},
        })
    return {"characters": out}


def _group_skills(skill_rows: list[dict], catalog: dict[int, dict]) -> list[dict]:
    groups: dict[Any, dict] = {}
    for r in skill_rows:
        meta = catalog.get(r["skill_id"]) or {}
        gid = meta.get("group_id")
        group = groups.setdefault(gid, {
            "group_id": gid, "group_name": meta.get("group_name") or "Unknown group",
            "skills": [], "total_sp": 0, "maxed": 0,
        })
        rank = meta.get("rank")
        group["skills"].append({
            "skill_id": r["skill_id"], "name": _skill_name(catalog, r["skill_id"]),
            "rank": rank, "active_level": r["active_level"], "trained_level": r["trained_level"],
            "skillpoints": r["skillpoints_in_skill"],
            "sp_to_level_v": sp_to_level_v(rank, r["skillpoints_in_skill"]),
        })
        group["total_sp"] += r["skillpoints_in_skill"]
        group["maxed"] += int(r["trained_level"] >= 5)
    out = sorted(groups.values(), key=lambda g: g["group_name"].lower())
    for g in out:
        g["skills"].sort(key=lambda s: s["name"].lower())
    return out


def _registered_character(character_id: Any) -> dict:
    try:
        character_id = int(character_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid character_id {character_id!r}") from e
    match = next((c for c in _token_characters() if c["character_id"] == character_id), None)
    if match is None:
        raise ActionError(f"Character {character_id} is not registered for ESI access.")
    return match


def do_character_skills(character_id: int) -> dict:
    """Skills grouped by SDE skill group, SP totals, attributes and the queue
    for one character."""
    char = _registered_character(character_id)
    cid = char["character_id"]
    tokens = TokenManager(OAUTH_CONFIG)
    freshness = fields.freshness_by_kind(cid)
    now = datetime.now(timezone.utc)

    def skills_shape(rows: list[dict]) -> list[dict]:
        catalog = storage.get_skill_catalog({r["skill_id"] for r in rows})
        return _group_skills(rows, catalog)

    def queue_shape(rows: list[dict]) -> dict:
        catalog = storage.get_skill_catalog({r["skill_id"] for r in rows})
        return _queue_value(rows, catalog, now)

    def summary_shape(rows: list[dict]) -> dict:
        row = rows[0] if rows else None
        total = row["total_sp"] if row else None
        return {
            "total_sp": total,
            "unallocated_sp": row["unallocated_sp"] if row else None,
            "extractable_estimate": extractable_estimate(total),
            "attributes": _attributes_value(row),
        }

    return {
        **char,
        "summary": fields.snapshot(
            "skills", TOOL_KEY, cid, tokens, freshness, summary_shape, table="attributes",
        ),
        "skills": fields.snapshot("skills", TOOL_KEY, cid, tokens, freshness, skills_shape),
        "queue": fields.snapshot("skillqueue", TOOL_KEY, cid, tokens, freshness, queue_shape),
        "freshness": {k: freshness[k] for k in ("skills", "skillqueue") if k in freshness},
    }


def do_skill_matrix() -> dict:
    """Every skill trained by at least one shared character, one column per
    character: `levels` maps str(character_id) -> {active, trained}. Only
    characters shared with `char_skills` appear; unshared ones are listed in
    `hidden_characters` so the UI can say why a column is missing."""
    shared = set(shared_owner_ids("skills", TOOL_KEY, "character"))
    registered = _token_characters()
    tokens = TokenManager(OAUTH_CONFIG)
    usable = [c for c in registered if c["character_id"] in shared]
    rows = read_esi("skills", TOOL_KEY, owner_type="character")
    catalog = storage.get_skill_catalog({r["skill_id"] for r in rows})

    by_skill: dict[int, dict] = {}
    for r in rows:
        by_skill.setdefault(r["skill_id"], {})[str(r["owner_id"])] = {
            "active": r["active_level"], "trained": r["trained_level"],
        }
    groups: dict[Any, dict] = {}
    for skill_id, levels in by_skill.items():
        meta = catalog.get(skill_id) or {}
        gid = meta.get("group_id")
        group = groups.setdefault(gid, {
            "group_id": gid, "group_name": meta.get("group_name") or "Unknown group", "skills": [],
        })
        group["skills"].append({
            "skill_id": skill_id, "name": _skill_name(catalog, skill_id), "levels": levels,
        })
    ordered = sorted(groups.values(), key=lambda g: g["group_name"].lower())
    for g in ordered:
        g["skills"].sort(key=lambda s: s["name"].lower())

    reauth = [
        c["character_id"] for c in usable
        if fields.gate("skills", TOOL_KEY, c["character_id"], tokens) == fields.STATE_REAUTH
    ]
    return {
        "characters": usable,
        "hidden_characters": [c for c in registered if c["character_id"] not in shared],
        "reauth_needed": reauth,
        "groups": ordered,
    }


def do_sync_char_skills() -> dict:
    """Refresh the kinds shared with Skills (skills + attributes + slots, and
    the skill queue). See fields.summarise_sync for the response shape."""
    return fields.summarise_sync(do_sync_for_tool(TOOL_KEY))
