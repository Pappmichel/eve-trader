"""PI-relevant character skills (D3): planets per character (Interplanetary
Consolidation), maximum Command Center level (Command Center Upgrades) and
Customs Code Expertise. Only characters whose `skills` are shared with the
`pi` tool count. When none do, the manual settings apply.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import constants as C
from .config import PiConfig

log = logging.getLogger("eve_trader.pi.skills")

_SKILLS = {
    C.SKILL_INTERPLANETARY_CONSOLIDATION: "interplanetary_consolidation",
    C.SKILL_COMMAND_CENTER_UPGRADES: "command_center_upgrades",
    C.SKILL_CUSTOMS_CODE_EXPERTISE: "customs_code_expertise",
}


def skill_levels_from_rows(rows: list[dict]) -> dict[int, dict[str, int]]:
    """read_esi("skills", "pi") rows -> {character_id: {skill key: level}}.
    A shared character without a row for a skill has level 0 in it."""
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        cid = int(r["owner_id"])
        levels = out.setdefault(cid, {k: 0 for k in _SKILLS.values()})
        key = _SKILLS.get(int(r.get("skill_id") or 0))
        if key:
            levels[key] = int(r.get("active_level") or 0)
    return out


def character_capacity(levels: Optional[dict[str, int]], cfg: PiConfig) -> dict:
    if levels is None:
        return {
            "source": "manual",
            "planets": int(cfg.pi_planets_per_character),
            "cc_level": int(cfg.pi_cc_level),
            "customs_code_expertise": int(cfg.pi_customs_code_expertise_level),
        }
    return {
        "source": "esi",
        "planets": C.BASE_PLANETS_PER_CHARACTER + levels["interplanetary_consolidation"],
        "cc_level": levels["command_center_upgrades"],
        "customs_code_expertise": levels["customs_code_expertise"],
    }


def characters_overview(cfg: PiConfig, token_characters: list[dict], esi_rows: list[dict]) -> dict:
    """Characters that share skills with Planetary Industry, plus the slot
    total used by chains and system analysis. A token character whose skills
    are not shared is left out: giving every alt the manual planet count and
    Command Center level counted trading characters as PI colonies. When
    nobody shares skills, the manual `pi_characters` x
    `pi_planets_per_character` applies and the character list stays empty."""
    levels = skill_levels_from_rows(esi_rows)
    characters = []
    for c in token_characters:
        cid = int(c["character_id"])
        if cid not in levels:
            continue
        cap = character_capacity(levels[cid], cfg)
        characters.append({"character_id": cid, "character_name": c.get("character_name"), **cap})
    if characters:
        slots = sum(c["planets"] for c in characters)
        max_cc = max(c["cc_level"] for c in characters)
        count = len(characters)
    else:
        slots = int(cfg.pi_characters) * int(cfg.pi_planets_per_character)
        max_cc = int(cfg.pi_cc_level)
        count = int(cfg.pi_characters)
    return {
        "characters": characters,
        "total_slots": slots,
        "max_cc_level": max_cc,
        "character_count": count,
    }


def load_overview(cfg: PiConfig) -> dict:
    """I/O wrapper: token characters + shared ESI skill rows."""
    from ..auth import TokenManager
    from ..config import OAUTH_CONFIG
    from ..esi_data import read_esi

    by_id: dict[int, str] = {}
    for rec in TokenManager(OAUTH_CONFIG).list_records():
        by_id[rec.character_id] = by_id.get(rec.character_id) or rec.character_name or ""
    tokens = sorted(
        ({"character_id": cid, "character_name": name or f"#{cid}"} for cid, name in by_id.items()),
        key=lambda c: (c["character_name"].lower(), c["character_id"]),
    )
    try:
        rows = read_esi("skills", "pi", owner_type="character")
    except Exception:  # noqa: BLE001 - no share / accessor error -> manual values
        log.info("PI skills not readable from ESI, using manual settings", exc_info=True)
        rows = []
    return characters_overview(cfg, tokens, rows)
