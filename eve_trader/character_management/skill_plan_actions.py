"""Skill plan `do_*` actions (docs/CHARACTER_MANAGEMENT_PLAN.md phase 9).

UI-agnostic, no FastAPI imports. Plans are tenant data the user writes; the
per-character progress reads the characters' skills through
`read_esi(..., "char_skill_plans")`, so only characters shared with Skill Plans
appear. Editing needs the SDE skill tables (Admin SDE preview + apply once):
without `sde_skill_meta` there are no skills to search, import or expand.
Training times are estimates (attributes as ESI reports them, no implants or
boosters, R14).
"""
from __future__ import annotations

from typing import Any, Optional

from .. import storage
from ..actions import ActionError
from ..esi_data import do_sync_for_tool, read_esi, shared_owner_ids
from . import fields, skill_check, skill_plan_logic as logic

TOOL_KEY = "char_skill_plans"
MAX_PLANS = 100
MAX_NAME = 100
MAX_DESCRIPTION = 500
MAX_IMPORT_CHARS = 50_000
NEXT_STEPS_SHOWN = 10


def _requirements(cache: Optional[dict] = None) -> logic.Requirements:
    """skill_id -> its prerequisite steps, cached per call chain (a plan
    expansion asks for the same skills repeatedly)."""
    memo: dict[int, list[logic.Step]] = {} if cache is None else cache

    def lookup(skill_id: int) -> list[logic.Step]:
        if skill_id not in memo:
            memo[skill_id] = storage.get_skill_requirements([skill_id]).get(skill_id, [])
        return memo[skill_id]
    return lookup


def _sde_ready() -> bool:
    return storage.skill_requirements_loaded()


def _need_sde() -> None:
    if not _sde_ready():
        raise ActionError(
            "Skill data is not loaded yet. Ask an admin to run an SDE preview and apply it once."
        )


def _name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ActionError("A plan needs a name.")
    if len(text) > MAX_NAME:
        raise ActionError(f"The name is too long (max {MAX_NAME} characters).")
    return text


def _description(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_DESCRIPTION:
        raise ActionError(f"The description is too long (max {MAX_DESCRIPTION} characters).")
    return text


def _plan_row(plan_id: Any) -> tuple:
    try:
        pid = int(plan_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid plan id {plan_id!r}") from e
    row = storage.get_skill_plan(pid)
    if row is None:
        raise ActionError(f"Plan {pid} does not exist.")
    return row


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError) as e:
        raise ActionError("The level must be a number from 1 to 5.") from e
    if not 1 <= level <= 5:
        raise ActionError("The level must be a number from 1 to 5.")
    return level


def _known_skill(skill_id: Any) -> int:
    try:
        sid = int(skill_id)
    except (TypeError, ValueError) as e:
        raise ActionError("Invalid skill id.") from e
    meta = storage.get_skill_catalog([sid]).get(sid)
    if meta is None or meta.get("rank") is None:
        raise ActionError(f"Skill {sid} is not a known skill.")
    return sid


def _view(row: tuple, steps: list[logic.Step]) -> dict:
    catalog = storage.get_skill_catalog({s for s, _ in steps})
    return {
        "plan_id": int(row[0]), "name": row[1], "description": row[2],
        "created_at": _iso(row[3]), "updated_at": _iso(row[4]),
        "steps": [
            {
                "position": pos, "skill_id": sid, "level": level, "level_label": logic.level_label(level),
                "name": (catalog.get(sid) or {}).get("name") or f"Skill {sid}",
                "group_name": (catalog.get(sid) or {}).get("group_name"),
                "rank": (catalog.get(sid) or {}).get("rank"),
            }
            for pos, (sid, level) in enumerate(steps)
        ],
        "sde_ready": _sde_ready(),
    }


# --------------------------------------------------------------------- CRUD
def do_list_plans() -> dict:
    return {
        "plans": [
            {"plan_id": int(r[0]), "name": r[1], "description": r[2], "created_at": _iso(r[3]),
             "updated_at": _iso(r[4]), "step_count": int(r[5])}
            for r in storage.list_skill_plans()
        ],
        "sde_ready": _sde_ready(),
    }


def do_create_plan(name: Any, description: Any = "", text: Optional[str] = None) -> dict:
    """A new plan, optionally filled from the client's plan text. `unresolved`
    lists the lines that could not be used (unparsed, or not a skill name)."""
    name, description = _name(name), _description(description)
    if storage.count_skill_plans() >= MAX_PLANS:
        raise ActionError(f"There are already {MAX_PLANS} plans. Delete one first.")
    steps: list[logic.Step] = []
    unresolved: list[str] = []
    ids: dict[str, int] = {}
    if text and text.strip():
        _need_sde()
        if len(text) > MAX_IMPORT_CHARS:
            raise ActionError("That text is too long to import.")
        entries, unresolved = logic.parse_plan_text(text)
        ids = storage.find_skill_ids_by_name(n for n, _ in entries)
        wanted: list[logic.Step] = []
        for skill_name, level in entries:
            sid = ids.get(skill_name.lower())
            if sid is None:
                unresolved.append(f"{skill_name} {logic.level_label(level)}")
            else:
                wanted.append((sid, level))
        steps, _ = logic.add_with_prerequisites([], wanted, _requirements())
        if len(steps) > logic.MAX_STEPS:
            raise ActionError(f"That plan has {len(steps)} steps; the limit is {logic.MAX_STEPS}.")
    plan_id = storage.create_skill_plan(name, description)
    if steps:
        storage.replace_skill_plan_items(plan_id, steps)
    out = _view(_plan_row(plan_id), steps)
    out["unresolved"] = unresolved
    out["steps_added_for_prerequisites"] = _extra(steps, text, ids)
    return out


def _extra(steps: list[logic.Step], text: Optional[str], ids: dict[str, int]) -> int:
    """How many steps were inserted only as prerequisites (in the plan but not
    named by the imported text)."""
    if not text or not text.strip():
        return 0
    entries, _ = logic.parse_plan_text(text)
    named = {(ids[n.lower()], lvl) for n, lvl in entries if n.lower() in ids}
    return len([s for s in steps if s not in named])


def do_get_plan(plan_id: Any) -> dict:
    row = _plan_row(plan_id)
    return _view(row, storage.load_skill_plan_items(int(row[0])))


def do_update_plan(plan_id: Any, name: Any, description: Any = "") -> dict:
    row = _plan_row(plan_id)
    storage.update_skill_plan(int(row[0]), _name(name), _description(description))
    return do_get_plan(row[0])


def do_delete_plan(plan_id: Any) -> dict:
    row = _plan_row(plan_id)
    storage.delete_skill_plan(int(row[0]))
    return {"deleted": int(row[0])}


# --------------------------------------------------------------------- steps
def do_add_skill(plan_id: Any, skill_id: Any, level: Any) -> dict:
    """Adds `skill` up to `level`, together with every lower level and every
    prerequisite that is not in the plan yet."""
    row = _plan_row(plan_id)
    _need_sde()
    sid, lvl = _known_skill(skill_id), _level(level)
    current = storage.load_skill_plan_items(int(row[0]))
    updated, added = logic.add_with_prerequisites(current, [(sid, lvl)], _requirements())
    if len(updated) > logic.MAX_STEPS:
        raise ActionError(f"A plan can have at most {logic.MAX_STEPS} steps.")
    if added:
        storage.replace_skill_plan_items(int(row[0]), updated)
    out = _view(_plan_row(row[0]), updated)
    out["added"] = added
    return out


def do_remove_step(plan_id: Any, skill_id: Any, level: Any) -> dict:
    """Removes the step and every step that needed it (a higher level of the
    same skill, or a skill that has it as a prerequisite)."""
    row = _plan_row(plan_id)
    step = (int(skill_id), _level(level))
    current = storage.load_skill_plan_items(int(row[0]))
    if step not in current:
        raise ActionError("That step is not in the plan.")
    kept = logic.prune_after_removal(current, step, _requirements())
    storage.replace_skill_plan_items(int(row[0]), kept)
    out = _view(_plan_row(row[0]), kept)
    out["removed"] = len(current) - len(kept)
    return out


def do_reorder(plan_id: Any, order: Any) -> dict:
    """Replaces the order. `order` must be exactly the plan's steps, each after
    everything it needs - a reorder can never break the plan."""
    row = _plan_row(plan_id)
    if not isinstance(order, list):
        raise ActionError("Provide the steps as a list.")
    try:
        wanted = [(int(o["skill_id"]), int(o["level"])) for o in order]
    except (KeyError, TypeError, ValueError) as e:
        raise ActionError("Each step needs a skill_id and a level.") from e
    current = storage.load_skill_plan_items(int(row[0]))
    if sorted(wanted) != sorted(current):
        raise ActionError("The new order must contain exactly the plan's steps.")
    if not logic.is_valid_order(wanted, _requirements()):
        raise ActionError("A skill cannot come before something it needs.")
    storage.replace_skill_plan_items(int(row[0]), wanted)
    return do_get_plan(row[0])


def do_export_plan(plan_id: Any) -> dict:
    row = _plan_row(plan_id)
    steps = storage.load_skill_plan_items(int(row[0]))
    names = {sid: (meta.get("name") or f"Skill {sid}") for sid, meta in storage.get_skill_catalog({s for s, _ in steps}).items()}
    return {"name": row[1], "text": logic.format_plan_text(steps, names)}


def do_search_skills(query: Any) -> dict:
    text = str(query or "").strip()
    if len(text) < 2:
        return {"skills": []}
    return {"skills": [{"skill_id": int(r[0]), "name": r[1], "group_name": r[2]} for r in storage.search_skills(text[:60])]}


# ------------------------------------------------------------------ progress
def do_sync_plans() -> dict:
    """Refresh the skills of every character shared with Skill Plans (same
    response shape as every sub-tool's sync)."""
    return fields.summarise_sync(do_sync_for_tool(TOOL_KEY))


def do_plan_progress(plan_id: Any) -> dict:
    """Per character shared with Skill Plans: steps done, SP and estimated time
    left, and the next steps in plan order (which is already a valid training
    order)."""
    row = _plan_row(plan_id)
    steps = storage.load_skill_plan_items(int(row[0]))
    registered = fields.token_characters()
    shared = set(shared_owner_ids("skills", TOOL_KEY, "character"))
    chars = [c for c in registered if c["character_id"] in shared]
    hidden = [c for c in registered if c["character_id"] not in shared]
    catalog = storage.get_skill_catalog({s for s, _ in steps})
    skills_by_char: dict[int, dict[int, dict]] = {}
    for r in read_esi("skills", TOOL_KEY, owner_type="character"):
        skills_by_char.setdefault(r["owner_id"], {})[r["skill_id"]] = r
    attrs = {r["owner_id"]: r for r in read_esi("skills", TOOL_KEY, owner_type="character", table="attributes")}

    def name_of(sid: int) -> str:
        return (catalog.get(sid) or {}).get("name") or f"Skill {sid}"

    out = []
    for c in chars:
        have = skills_by_char.get(c["character_id"])
        if have is None and c["character_id"] not in attrs:
            out.append({**c, "synced": False})
            continue
        have = have or {}
        pending: list[dict] = []
        done = 0
        for sid, level in steps:
            trained = (have.get(sid) or {}).get("trained_level", 0)
            sp_left = logic.step_sp_remaining(
                (catalog.get(sid) or {}).get("rank"), level, trained, (have.get(sid) or {}).get("skillpoints_in_skill", 0),
            )
            if trained >= level:
                done += 1
            else:
                pending.append({"skill_id": sid, "level": level, "level_label": logic.level_label(level),
                                "name": name_of(sid), "sp_remaining": sp_left})
        known = [p for p in pending if p["sp_remaining"] is not None]
        seconds = (
            skill_check.training_seconds(known, catalog, attrs.get(c["character_id"]))
            if len(known) == len(pending) else None
        )
        out.append({
            **c, "synced": True, "steps_total": len(steps), "steps_done": done,
            "sp_remaining": sum(p["sp_remaining"] for p in known) if len(known) == len(pending) else None,
            "train_seconds": seconds, "next_steps": pending[:NEXT_STEPS_SHOWN],
        })
    return {"plan_id": int(row[0]), "characters": out, "hidden_characters": hidden}
