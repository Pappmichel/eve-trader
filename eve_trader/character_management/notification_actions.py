"""Notifications `do_*` actions (docs/CHARACTER_MANAGEMENT_PLAN.md phase 6).

UI-agnostic, no FastAPI imports. Notifications are a snapshot kind: the fetcher
mirrors what ESI currently returns; everything here reads through
`read_esi(..., "char_notifications")`, so the Characters-page sharing matrix
decides which characters appear. ESI has no write for notifications, so "read"
is this app's own flag (`character_notification_reads`); a notification counts
as read if ESI already said so *or* the flag is set.

`text` is YAML. It is parsed with a safe loader and only ever used to build a
short display line - a body that does not parse, or a type this module does not
know, still shows up, under its humanised type name.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from ruamel.yaml import YAML

from .. import storage
from ..actions import ActionError
from ..esi_data import do_sync_for_tool, read_esi, shared_owner_ids
from . import fields

log = logging.getLogger(__name__)

TOOL_KEY = "char_notifications"
MAX_PAGE = 200
DEFAULT_PAGE = 50
MAX_TEXT_CHARS = 20_000       # a notification body is small; refuse to parse absurd ones

_yaml = YAML(typ="safe")

# (prefix or exact name) -> category. First match wins; anything else is "other".
_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("Structure", "structures"), ("OwnershipTransferred", "structures"),
    ("Sov", "sovereignty"), ("Entosis", "sovereignty"), ("Infrastructure", "sovereignty"),
    ("War", "war"), ("AllWar", "war"), ("Corpwar", "war"), ("Ally", "war"), ("Mercenary", "war"),
    ("Corp", "corporation"), ("CharLeftCorp", "corporation"), ("CharAppAccept", "corporation"),
    ("Alliance", "corporation"), ("Moon", "moon"), ("Tower", "starbase"), ("Kill", "combat"),
)

CATEGORY_LABELS = {
    "structures": "Structures", "sovereignty": "Sovereignty", "war": "War", "corporation": "Corporation",
    "moon": "Moon mining", "starbase": "Starbases", "combat": "Combat", "other": "Other",
}


def category_of(notification_type: str) -> str:
    for prefix, category in _CATEGORY_RULES:
        if notification_type.startswith(prefix):
            return category
    return "other"


def humanise_type(notification_type: str) -> str:
    """`StructureLostShields` -> `Structure lost shields`."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", notification_type or "").strip()
    return (words[:1].upper() + words[1:].lower()) if words else "Notification"


def parse_body(text: Optional[str]) -> dict:
    """The YAML body as a dict; {} for anything missing, oversized, malformed or
    not a mapping (a notification must never break the list)."""
    if not text or len(text) > MAX_TEXT_CHARS:
        return {}
    try:
        data = _yaml.load(text)
    except Exception:  # noqa: BLE001 - any parser failure just means "no details"
        return {}
    if not isinstance(data, dict) or not all(isinstance(k, str) for k in data):
        return {}
    return data


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _structure_type_id(body: dict) -> Optional[int]:
    info = body.get("structureShowInfoData")
    if isinstance(info, (list, tuple)) and len(info) >= 2:
        return _int(info[1])
    return _int(body.get("structureTypeID"))


def _percent(body: dict, key: str) -> Optional[str]:
    try:
        return f"{float(body[key]):.0f}%"
    except (KeyError, TypeError, ValueError):
        return None


def summarise(notification_type: str, body: dict, system_names: dict, type_names: dict) -> str:
    """One display line: the humanised type, plus where (solar system) and what
    (structure type) when the body says so, plus the shield/armor/hull
    percentages of an attack."""
    line = humanise_type(notification_type)
    details: list[str] = []
    system_id = _int(body.get("solarsystemID") or body.get("solarSystemID"))
    if system_id:
        details.append(system_names.get(system_id) or f"system #{system_id}")
    type_id = _structure_type_id(body)
    if type_id:
        details.append(type_names.get(type_id) or f"type #{type_id}")
    if notification_type == "StructureUnderAttack":
        levels = [
            f"{label} {value}" for label, key in (("shield", "shieldPercentage"), ("armor", "armorPercentage"),
                                                  ("hull", "hullPercentage"))
            if (value := _percent(body, key))
        ]
        if levels:
            details.append(", ".join(levels))
    return f"{line} - {' - '.join(details)}" if details else line


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _shared_characters() -> tuple[list[dict], list[dict]]:
    registered = fields.token_characters()
    shared = set(shared_owner_ids("notifications", TOOL_KEY, "character"))
    return ([c for c in registered if c["character_id"] in shared],
            [c for c in registered if c["character_id"] not in shared])


def _clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def do_list_notifications(
    character_id: Optional[int] = None, type_filter: Optional[str] = None, category: Optional[str] = None,
    unread_only: bool = False, limit: int = DEFAULT_PAGE, offset: int = 0,
) -> dict:
    """Notifications of every character shared with Notifications, newest
    first, filtered and paged. `types`/`categories` count what the filters can
    still select (ignoring the type/category filter itself, so the pickers stay
    populated)."""
    chars, hidden = _shared_characters()
    names = {c["character_id"]: c["character_name"] for c in chars}
    if character_id is not None:
        character_id = _int(character_id)
        if character_id not in names:
            raise ActionError(f"Character {character_id} is not shared with Notifications.")
    wanted = [character_id] if character_id is not None else list(names)
    rows = [r for r in read_esi("notifications", TOOL_KEY, owner_type="character") if r["owner_id"] in wanted]
    reads = storage.load_notification_reads(wanted)
    freshness = {c["character_id"]: fields.freshness_by_kind(c["character_id"]).get("notifications") for c in chars}

    def is_read(r: dict) -> bool:
        return r["esi_is_read"] or (r["owner_id"], r["notification_id"]) in reads

    base = [r for r in rows if not unread_only or not is_read(r)]
    types: dict[str, int] = {}
    categories: dict[str, int] = {}
    for r in base:
        types[r["type"]] = types.get(r["type"], 0) + 1
        categories[category_of(r["type"])] = categories.get(category_of(r["type"]), 0) + 1
    matching = [
        r for r in base
        if (not type_filter or r["type"] == type_filter) and (not category or category_of(r["type"]) == category)
    ]
    limit = _clamp(limit, DEFAULT_PAGE, 1, MAX_PAGE)
    offset = _clamp(offset, 0, 0, 10 ** 9)
    page = matching[offset:offset + limit]

    bodies = [parse_body(r["text"]) for r in page]
    system_ids = {i for b in bodies if (i := _int(b.get("solarsystemID") or b.get("solarSystemID")))}
    type_ids = {i for b in bodies if (i := _structure_type_id(b))}
    system_names = storage.get_solar_system_names(system_ids) if system_ids else {}
    type_names = (
        {tid: row[2] for tid, row in storage.get_sde_types_bulk(sorted(type_ids)).items() if row} if type_ids else {}
    )
    items = [
        {
            "character_id": r["owner_id"], "character_name": names.get(r["owner_id"]),
            "notification_id": r["notification_id"], "type": r["type"], "category": category_of(r["type"]),
            "sent_at": _iso(r["sent_at"]), "summary": summarise(r["type"], body, system_names, type_names),
            "read": is_read(r), "read_in_game": r["esi_is_read"],
            "sender_id": r["sender_id"], "sender_type": r["sender_type"],
        }
        for r, body in zip(page, bodies)
    ]
    unread_total = sum(1 for r in rows if not is_read(r))
    return {
        "items": items, "total": len(matching), "unread_total": unread_total,
        "types": sorted(({"type": t, "label": humanise_type(t), "count": n} for t, n in types.items()),
                        key=lambda x: x["label"]),
        "categories": [
            {"category": c, "label": CATEGORY_LABELS.get(c, c), "count": n} for c, n in sorted(categories.items())
        ],
        "characters": [
            {"character_id": c["character_id"], "character_name": c["character_name"],
             "synced_at": _iso((freshness.get(c["character_id"]) or {}).get("last_success_at")),
             "last_error": (freshness.get(c["character_id"]) or {}).get("last_error")}
            for c in chars
        ],
        "hidden_characters": hidden,
    }


def do_get_notification(character_id: int, notification_id: int) -> dict:
    """One notification with its parsed body (a flat, display-only view of the
    YAML: scalars as text, nested values as compact text)."""
    chars, _ = _shared_characters()
    cid, nid = _int(character_id), _int(notification_id)
    if cid not in {c["character_id"] for c in chars}:
        raise ActionError(f"Character {character_id} is not shared with Notifications.")
    row = next(
        (r for r in read_esi("notifications", TOOL_KEY, owner_type="character", owner_id=cid)
         if r["notification_id"] == nid), None,
    )
    if row is None:
        raise ActionError("Notification not found (it may have aged out of ESI's list).")
    body = parse_body(row["text"])
    details = [{"key": str(k), "value": _display(v)} for k, v in body.items()]
    system_id = _int(body.get("solarsystemID") or body.get("solarSystemID"))
    type_id = _structure_type_id(body)
    system_names = storage.get_solar_system_names([system_id]) if system_id else {}
    type_names = (
        {t: r[2] for t, r in storage.get_sde_types_bulk([type_id]).items() if r} if type_id else {}
    )
    return {
        "character_id": cid, "notification_id": nid, "type": row["type"], "category": category_of(row["type"]),
        "sent_at": _iso(row["sent_at"]), "summary": summarise(row["type"], body, system_names, type_names),
        "details": details, "parsed": bool(body),
        "read": row["esi_is_read"] or (cid, nid) in storage.load_notification_reads([cid]),
    }


def _display(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(_display(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_display(v)}" for k, v in value.items())
    return "" if value is None else str(value)


def do_set_notifications_read(character_id: int, notification_ids: list, read: bool = True) -> dict:
    chars, _ = _shared_characters()
    cid = _int(character_id)
    if cid not in {c["character_id"] for c in chars}:
        raise ActionError(f"Character {character_id} is not shared with Notifications.")
    if not isinstance(notification_ids, list) or not notification_ids or len(notification_ids) > 500:
        raise ActionError("Provide between 1 and 500 notification ids.")
    ids = [i for i in (_int(n) for n in notification_ids) if i is not None]
    if not ids:
        raise ActionError("Notification ids must be numbers.")
    return {"changed": storage.set_notifications_read(cid, ids, bool(read))}


def do_sync_notifications() -> dict:
    """Refresh the notification snapshot for every character shared with
    Notifications (same response shape as every sub-tool's sync)."""
    return fields.summarise_sync(do_sync_for_tool(TOOL_KEY))
