"""Which of my characters can fly which doctrine fitting?
(docs/CHARACTER_MANAGEMENT_PLAN.md phase 5b).

Cross-tool by nature: Doctrine owns the fittings, Skills owns the skill levels.
This reads Doctrine's stored fittings through `storage` (never importing the
doctrine package) and the characters' skills through `read_esi(..., "char_skills")`,
so only characters shared with Skills appear. The HTTP route additionally
requires the `doctrine` grant (a `char_skills` grant alone must not expose
another tool's fittings) - see api/routers/char_skills.py.

Required skills come from the SDE (`sde_skill_requirements`): the hull plus every
fitted module, charge and drone, then each skill's own prerequisites, expanded
recursively to the highest level any path needs. Training time is an ESTIMATE:
SP still missing / (primary + secondary/2) SP per minute from the attributes ESI
reports, summed over the missing skills, without boosters and assuming an Omega
clone (docs/CHARACTER_MANAGEMENT_PLAN.md R14).
"""
from __future__ import annotations

import math
from typing import Optional

from .. import storage
from ..actions import ActionError
from ..esi_data import read_esi, shared_owner_ids
from . import fields

TOOL_KEY = "char_skills"

# Attribute ids used by sde_skill_meta.primary_attribute / secondary_attribute.
_ATTRIBUTE_NAME = {164: "charisma", 165: "intelligence", 166: "memory", 167: "perception", 168: "willpower"}


def sp_for_level(rank: float, level: int) -> int:
    """Total SP a skill of this rank has when it reaches `level` (1-5):
    250 * rank * sqrt(32)^(level-1), rounded up as the game shows it (a rank-1
    skill: 250, 1,415, 8,000, 45,255, 256,000)."""
    return math.ceil(250 * rank * 32 ** ((level - 1) / 2))


def required_skills(type_ids: set[int]) -> dict[int, int]:
    """{skill_id: highest level needed} to use every type in `type_ids`,
    including each skill's own prerequisites, transitively.

    A skill's prerequisites are what it takes to train it at all, whatever
    level is asked for, so each skill is expanded once; `expanded` also makes a
    (never expected) cycle in the data harmless."""
    needed: dict[int, int] = {}
    expanded: set[int] = set()
    frontier = set(type_ids)
    while frontier:
        next_frontier: set[int] = set()
        for skills in storage.get_skill_requirements(frontier).values():
            for skill_id, level in skills:
                if level > needed.get(skill_id, 0):
                    needed[skill_id] = level
                if skill_id not in expanded:
                    next_frontier.add(skill_id)
        expanded |= next_frontier
        frontier = next_frontier
    return needed


def training_seconds(
    missing: list[dict], catalog: dict[int, dict], attributes: Optional[dict],
) -> Optional[int]:
    """Estimated seconds to train every missing skill, or None when a needed
    rank/attribute is unknown (SDE without dgmTypeAttributes, or ESI attributes
    not synced)."""
    if not missing:
        return 0
    if not attributes:
        return None
    total_minutes = 0.0
    for m in missing:
        meta = catalog.get(m["skill_id"]) or {}
        rank, primary, secondary = meta.get("rank"), meta.get("primary_attribute"), meta.get("secondary_attribute")
        p_name, s_name = _ATTRIBUTE_NAME.get(primary), _ATTRIBUTE_NAME.get(secondary)
        if rank is None or not p_name or not s_name:
            return None
        p_val, s_val = attributes.get(p_name), attributes.get(s_name)
        if p_val is None or s_val is None:
            return None
        sp_per_minute = p_val + s_val / 2
        total_minutes += m["sp_remaining"] / sp_per_minute
    return math.ceil(total_minutes * 60)


def _fitting_type_ids(fitting_row: tuple) -> set[int]:
    fitting_id, hull_type_id = str(fitting_row[0]), int(fitting_row[4])
    ids = {hull_type_id}
    ids.update(int(item[2]) for item in storage.load_fitting_items(fitting_id))
    return ids


def do_doctrine_skill_check(doctrine_id: Optional[str] = None) -> dict:
    """Per active fitting (optionally one doctrine), per character shared with
    Skills: can they fly it, what is missing, and a training-time estimate."""
    if not storage.skill_requirements_loaded():
        return {"sde_ready": False, "fittings": [], "characters": [], "hidden_characters": []}

    registered = fields.token_characters()
    shared = set(shared_owner_ids("skills", TOOL_KEY, "character"))
    chars = [c for c in registered if c["character_id"] in shared]
    hidden = [c for c in registered if c["character_id"] not in shared]

    skill_rows = read_esi("skills", TOOL_KEY, owner_type="character")
    attr_rows = {r["owner_id"]: r for r in read_esi("skills", TOOL_KEY, owner_type="character", table="attributes")}
    levels: dict[int, dict[int, dict]] = {}
    for r in skill_rows:
        levels.setdefault(r["owner_id"], {})[r["skill_id"]] = r

    fittings = [f for f in storage.list_active_fittings() if doctrine_id is None or str(f[1]) == str(doctrine_id)]
    if doctrine_id is not None and not fittings and storage.get_doctrine(str(doctrine_id)) is None:
        raise ActionError(f"Doctrine {doctrine_id} does not exist.")
    doctrine_names = {str(d[0]): d[1] for d in storage.list_doctrines()}

    per_fitting: list[tuple[tuple, dict[int, int]]] = []
    all_skill_ids: set[int] = set()
    all_type_ids: set[int] = set()
    for f in fittings:
        type_ids = _fitting_type_ids(f)
        all_type_ids |= type_ids
        need = required_skills(type_ids)
        per_fitting.append((f, need))
        all_skill_ids |= set(need)
    catalog = storage.get_skill_catalog(all_skill_ids)
    type_names = {tid: (row[2] if row else None) for tid, row in storage.get_sde_types_bulk(sorted(all_type_ids)).items()}

    out = []
    for f, need in per_fitting:
        per_char = []
        for c in chars:
            have = levels.get(c["character_id"], {})
            missing = []
            for skill_id, level in sorted(need.items()):
                row = have.get(skill_id)
                trained = row["trained_level"] if row else 0
                if trained >= level:
                    continue
                meta = catalog.get(skill_id) or {}
                rank = meta.get("rank")
                current_sp = row["skillpoints_in_skill"] if row else 0
                missing.append({
                    "skill_id": skill_id, "name": meta.get("name") or f"Skill {skill_id}",
                    "needed": level, "have": trained,
                    "sp_remaining": max(0, sp_for_level(rank, level) - current_sp) if rank is not None else None,
                })
            trainable = [m for m in missing if m["sp_remaining"] is not None]
            seconds = (
                training_seconds(trainable, catalog, attr_rows.get(c["character_id"]))
                if len(trainable) == len(missing) else None
            )
            per_char.append({
                "character_id": c["character_id"], "can_fly": not missing,
                "missing": missing, "train_seconds": seconds,
            })
        out.append({
            "fitting_id": str(f[0]), "name": f[2], "variant_label": f[3],
            "doctrine_id": str(f[1]), "doctrine_name": doctrine_names.get(str(f[1])),
            "hull_type_id": int(f[4]), "hull_name": type_names.get(int(f[4])),
            "required_skills": len(need),
            "characters": per_char,
        })
    return {
        "sde_ready": True, "fittings": out, "characters": chars, "hidden_characters": hidden,
    }
