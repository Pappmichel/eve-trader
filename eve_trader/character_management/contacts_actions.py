"""Contacts & Calendar `do_*` actions (docs/CHARACTER_MANAGEMENT_PLAN.md phase 8).

UI-agnostic, no FastAPI imports. Both kinds are live-only: read from ESI per
request (short cache), never stored. Read-only - nothing here changes a contact
or answers an event. Every field goes through the usual gate (shared with
`char_contacts` -> token holds the scope -> data), so a character that is not
shared is never fetched.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from . import fields

TOOL_KEY = "char_contacts"

_CONTACT_TYPES = ("character", "corporation", "alliance", "faction")


def _client_and_tokens() -> tuple[ESIClient, TokenManager]:
    tokens = TokenManager(OAUTH_CONFIG)
    return ESIClient(tokens=tokens), tokens


def _names(client: ESIClient, ids) -> dict[int, str]:
    unique = [i for i in dict.fromkeys(int(i) for i in ids if i)]
    if not unique:
        return {}
    try:
        return client.resolve_names_cached(unique)
    except ESIError:
        return {}


def _registered(character_id: Any) -> dict:
    try:
        cid = int(character_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid character_id {character_id!r}") from e
    match = next((c for c in fields.token_characters() if c["character_id"] == cid), None)
    if match is None:
        raise ActionError(f"Character {cid} is not registered for ESI access.")
    return match


def do_list_characters() -> dict:
    """One row per registered character with the gate state of each kind - no
    ESI call, so the page can render its selector instantly."""
    tokens = TokenManager(OAUTH_CONFIG)
    rows = []
    for c in fields.token_characters():
        cid = c["character_id"]
        rows.append({
            **c,
            "contacts": fields.field(fields.gate("contacts", TOOL_KEY, cid, tokens) or fields.STATE_OK),
            "calendar": fields.field(fields.gate("calendar", TOOL_KEY, cid, tokens) or fields.STATE_OK),
        })
    return {"characters": rows}


def do_get_contacts(character_id: int) -> dict:
    """Contacts with names, standing and label names, best standing first."""
    _registered(character_id)
    cid = int(character_id)
    client, tokens = _client_and_tokens()

    def fetch(role: str) -> tuple[list, list]:
        return client.character_contacts(cid, role), client.character_contact_labels(cid, role)

    def shape(raw: tuple[list, list]) -> dict:
        contacts, labels = raw
        label_names = {int(l["label_id"]): l["label_name"] for l in labels}
        names = _names(client, [c["contact_id"] for c in contacts])
        rows = [
            {
                "contact_id": int(c["contact_id"]),
                "name": names.get(int(c["contact_id"])) or f"#{c['contact_id']}",
                "contact_type": c.get("contact_type") if c.get("contact_type") in _CONTACT_TYPES else "other",
                "standing": float(c.get("standing", 0)),
                "is_blocked": bool(c.get("is_blocked", False)),
                "is_watched": bool(c.get("is_watched", False)),
                "labels": [label_names[i] for i in c.get("label_ids") or [] if i in label_names],
            }
            for c in contacts
        ]
        rows.sort(key=lambda r: (-r["standing"], r["name"].lower()))
        return {"contacts": rows, "labels": sorted(label_names.values(), key=str.lower)}

    return fields.live("contacts", TOOL_KEY, cid, tokens, fetch, shape)


def do_get_calendar(character_id: int) -> dict:
    """Upcoming events (ESI returns up to 50), soonest first."""
    _registered(character_id)
    cid = int(character_id)
    client, tokens = _client_and_tokens()

    def shape(events: list) -> list[dict]:
        rows = [
            {
                "event_id": int(e["event_id"]), "title": e.get("title") or "(untitled)",
                "event_date": e.get("event_date"), "importance": e.get("importance"),
                "response": e.get("event_response"),
            }
            for e in events
        ]
        rows.sort(key=lambda r: r["event_date"] or "")
        return rows

    return fields.live("calendar", TOOL_KEY, cid, tokens, lambda role: client.character_calendar(cid, role), shape)


def do_get_calendar_event(character_id: int, event_id: int) -> dict:
    """One event's details (one ESI call, made only when the user opens it). The
    event id must come from this character's own calendar list, so the route
    cannot be used to probe arbitrary ids."""
    _registered(character_id)
    cid = int(character_id)
    try:
        eid = int(event_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid event_id {event_id!r}") from e
    client, tokens = _client_and_tokens()

    def fetch(role: str) -> dict:
        if eid not in {int(e["event_id"]) for e in client.character_calendar(cid, role)}:
            raise ActionError("That event is not on this character's calendar.")
        return client.character_calendar_event(cid, role, eid)

    def shape(e: dict) -> dict:
        return {
            "event_id": eid, "title": e.get("title"), "date": e.get("date"), "duration": e.get("duration"),
            "importance": e.get("importance"), "owner_name": e.get("owner_name"),
            "owner_type": e.get("owner_type"), "response": e.get("response"), "text": e.get("text"),
        }

    return fields.live("calendar", TOOL_KEY, cid, tokens, fetch, shape)
