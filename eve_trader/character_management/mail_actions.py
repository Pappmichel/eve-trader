"""Mail `do_*` actions (docs/CHARACTER_MANAGEMENT_PLAN.md phase 3).

UI-agnostic, no FastAPI imports. Mail is read LIVE from ESI by default and
nothing about it is stored. A character with the "Archive mail" checkbox
ticked is read from the local archive instead (see mail_archive.py); a
unified inbox freely mixes both kinds, merged by time.

Every path is gated the same way, in this order: the character must be
registered and shared with `char_mail` (`shared_owner_ids`, so an unshared
character is invisible - never fetched, never read from the archive), and a
token must hold the mail scope (otherwise that character reports
`reauth_needed`; the others still load).

Privacy: no subject, body or sender ever reaches a log line or an error
message. ESI failures are reduced to their HTTP status (`fields.esi_failure`).
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data import select_auth_role, shared_owner_ids
from . import fields, mail_archive
from .fields import STATE_ERROR, STATE_OK, STATE_REAUTH, esi_failure

log = logging.getLogger(__name__)

TOOL_KEY = "char_mail"
PAGE_SIZE = mail_archive.PAGE_SIZE
MAIL_SCOPE = mail_archive.MAIL_SCOPE

# ESI's fixed system folders (mail label ids). Custom labels use other ids.
SYSTEM_LABELS = {1: "Inbox", 2: "Sent", 4: "Corp", 8: "Alliance", 16: "Mailing lists"}


# ---------------------------------------------------------------- resolution
def _client_and_tokens() -> tuple[ESIClient, TokenManager]:
    tokens = TokenManager(OAUTH_CONFIG)
    return ESIClient(tokens=tokens), tokens


def _mail_characters(
    character_id: Optional[int] = None, *, archived_only: bool = False,
) -> list[dict]:
    """Registered characters shared with char_mail (optionally one, or only
    the archive-enabled ones), each with `archived` and `settings`."""
    shared = set(shared_owner_ids("mail", TOOL_KEY, "character"))
    registered = fields.token_characters()
    if character_id is not None:
        try:
            character_id = int(character_id)
        except (TypeError, ValueError) as e:
            raise ActionError(f"Invalid character_id {character_id!r}") from e
        match = next((c for c in registered if c["character_id"] == character_id), None)
        if match is None:
            raise ActionError(f"Character {character_id} is not registered for ESI access.")
        if character_id not in shared:
            raise ActionError(
                f"{match['character_name']} is not shared with Mail. Tick it on the Characters page."
            )
        registered = [match]
    chars = [c for c in registered if c["character_id"] in shared]
    settings = storage.get_mail_archive_settings([c["character_id"] for c in chars])
    out = [{**c, "archived": c["character_id"] in settings, "settings": settings.get(c["character_id"])} for c in chars]
    return [c for c in out if c["archived"]] if archived_only else out


def _status(char: dict, state: str = STATE_OK, detail: Optional[str] = None) -> dict:
    out = {
        "character_id": char["character_id"], "character_name": char["character_name"],
        "archived": char["archived"], "state": state,
    }
    if detail:
        out["detail"] = detail
    return out


# ------------------------------------------------------------------- folders
def _normalize_labels(raw: dict) -> tuple[list[dict], int]:
    """ESI labels -> folder list. System folders always appear (with clean
    names, even if ESI reports "[Inbox]" or omits them); custom labels follow."""
    by_id = {int(lb["label_id"]): lb for lb in raw.get("labels", [])}
    labels = [
        {
            "label_id": lid, "name": name, "color": (by_id.get(lid) or {}).get("color"),
            "unread_count": (by_id.get(lid) or {}).get("unread_count") or 0, "system": True,
        }
        for lid, name in SYSTEM_LABELS.items()
    ]
    labels += [
        {
            "label_id": lid, "name": lb.get("name") or f"Label {lid}", "color": lb.get("color"),
            "unread_count": lb.get("unread_count") or 0, "system": False,
        }
        for lid, lb in sorted(by_id.items()) if lid not in SYSTEM_LABELS
    ]
    return labels, int(raw.get("total_unread_count") or 0)


def do_list_folders() -> dict:
    """Per character: folders with unread counts and mailing lists; plus the
    system-folder unread totals across every loaded character (unified inbox)."""
    chars = _mail_characters()
    if not chars:
        return {"characters": [], "unread": {}}
    client, tokens = _client_and_tokens()
    out, unread = [], {lid: 0 for lid in SYSTEM_LABELS}
    for c in chars:
        cid = c["character_id"]
        # Write capabilities (phase 4): `ready`, `not_enabled` (not ticked) or
        # `reauth_needed`, so the UI can offer send/organize or explain why not.
        caps = {
            "send": fields.capability_ready(cid, "mail_send", "esi-mail.send_mail.v1", tokens),
            "organize": fields.capability_ready(cid, "mail_organize", "esi-mail.organize_mail.v1", tokens),
        }
        role = select_auth_role(cid, MAIL_SCOPE, tokens=tokens)
        if role is None:
            out.append({**_status(c, STATE_REAUTH), "labels": [], "lists": [], "total_unread": 0,
                        "capabilities": caps})
            continue
        try:
            labels, total = _normalize_labels(client.character_mail_labels(cid, role))
            lists = [
                {"list_id": int(x["mailing_list_id"]), "name": x.get("name") or ""}
                for x in client.character_mail_lists(cid, role)
            ]
            state, detail = STATE_OK, None
        except ESIError as e:
            state, detail = STATE_ERROR, esi_failure(e)
            labels, total, lists = [], 0, []
            if c["archived"]:      # offline fallback: what the last refresh stored
                stored = storage.load_mail_labels(cid)
                labels, _ = _normalize_labels({"labels": [
                    {"label_id": r[0], "name": r[1], "color": r[2], "unread_count": r[3]} for r in stored
                ]})
                lists = [{"list_id": r[0], "name": r[1]} for r in storage.load_mail_lists(cid)]
        for lb in labels:
            if lb["label_id"] in unread:
                unread[lb["label_id"]] += lb["unread_count"]
        out.append({**_status(c, state, detail), "labels": labels, "lists": lists, "total_unread": total,
                    "capabilities": caps})
    return {"characters": out, "unread": {str(k): v for k, v in unread.items()}}


# --------------------------------------------------------------------- lists
def _live_headers(client: ESIClient, cid: int, role: str, label_id: Optional[int], cursor: Optional[int]) -> list[dict]:
    raw = client.character_mail_headers(
        cid, role, labels=[label_id] if label_id else None, last_mail_id=cursor,
    )
    return [
        {
            "character_id": cid, "mail_id": int(r["mail_id"]), "from_id": r.get("from"),
            "subject": r.get("subject") or "", "timestamp": r.get("timestamp"),
            "is_read": bool(r.get("is_read")), "labels": list(r.get("labels") or []),
            "recipients": [
                {"recipient_id": int(x["recipient_id"]), "recipient_type": x["recipient_type"]}
                for x in r.get("recipients") or []
            ],
        }
        for r in raw
    ]


def _next_cursor(headers: list[dict]) -> Optional[int]:
    """A full page means there may be more; the smallest id is ESI's cursor."""
    return min(h["mail_id"] for h in headers) if len(headers) >= PAGE_SIZE else None


def _list_names(client: ESIClient, char: dict, role: str) -> dict[int, str]:
    try:
        return {int(x["mailing_list_id"]): x.get("name") or "" for x in client.character_mail_lists(
            char["character_id"], role)}
    except ESIError:
        return {r[0]: r[1] for r in storage.load_mail_lists(char["character_id"])} if char["archived"] else {}


def _recipient_name(r: dict, names: dict[int, str], list_names: dict[int, dict[int, str]]) -> Optional[str]:
    """Characters/corps/alliances come from /universe/names; a mailing list's
    name only from a subscribing character's own lists endpoint."""
    if r["recipient_type"] != "mailing_list":
        return names.get(r["recipient_id"])
    for lists in list_names.values():
        if lists.get(r["recipient_id"]):
            return lists[r["recipient_id"]]
    return f"Mailing list {r['recipient_id']}"


def _enrich_and_group(
    headers: list[dict], chars_by_id: dict[int, dict], client: ESIClient,
    list_names: dict[int, dict[int, str]],
) -> list[dict]:
    """Group per-character headers by mail_id (one row, "received by A, B" -
    docs/CHARACTER_MANAGEMENT_PLAN.md R4), attach names, sort newest first."""
    ids: set[int] = set()
    for h in headers:
        if h["from_id"]:
            ids.add(int(h["from_id"]))
        ids.update(r["recipient_id"] for r in h["recipients"] if r["recipient_type"] != "mailing_list")
    try:
        names = client.resolve_names_cached(sorted(ids))
    except ESIError:
        names = {}      # names are cosmetic; ids still render

    def ts_key(h: dict):
        return (fields.parse_dt(h["timestamp"]) or fields.parse_dt("1970-01-01T00:00:00Z"), h["mail_id"])

    grouped: dict[int, dict] = {}
    for h in sorted(headers, key=ts_key, reverse=True):
        row = grouped.get(h["mail_id"])
        if row is None:
            row = grouped[h["mail_id"]] = {
                "mail_id": h["mail_id"], "from_id": h["from_id"],
                "from_name": names.get(int(h["from_id"])) if h["from_id"] else None,
                "subject": h["subject"], "timestamp": h["timestamp"],
                "recipients": [
                    {**r, "name": _recipient_name(r, names, list_names)} for r in h["recipients"]
                ],
                "received_by": [],
            }
        char = chars_by_id[h["character_id"]]
        row["received_by"].append({
            "character_id": h["character_id"], "character_name": char["character_name"],
            "is_read": h["is_read"], "labels": h["labels"], "archived": char["archived"],
        })
    for row in grouped.values():
        row["is_read"] = all(r["is_read"] for r in row["received_by"])
    return list(grouped.values())


def do_list_mails(
    label_id: Optional[int] = None, character_id: Optional[int] = None,
    cursors: Optional[dict[str, Optional[int]]] = None,
) -> dict:
    """One page (50 per character) of one folder, merged across the selected
    character(s). First call: `cursors=None`. "Load more": pass back the
    `next_cursors` of the previous response - only characters listed there
    are fetched again, each from its own cursor."""
    chars = _mail_characters(character_id)
    if cursors is not None:
        wanted = {int(k) for k, v in cursors.items() if v}
        chars = [c for c in chars if c["character_id"] in wanted]
    if not chars:
        return {"mails": [], "next_cursors": {}, "characters": []}
    client, tokens = _client_and_tokens()

    all_headers: list[dict] = []
    statuses, next_cursors, list_names = [], {}, {}
    for c in chars:
        cid = c["character_id"]
        cursor = int(cursors[str(cid)]) if cursors and cursors.get(str(cid)) else None
        role = select_auth_role(cid, MAIL_SCOPE, tokens=tokens)
        if role is None:
            statuses.append(_status(c, STATE_REAUTH))
            continue
        try:
            if c["archived"]:
                page = storage.load_mail_archive_page(cid, label_id, cursor, PAGE_SIZE)
            else:
                page = _live_headers(client, cid, role, label_id, cursor)
        except ESIError as e:
            statuses.append(_status(c, STATE_ERROR, esi_failure(e)))
            continue
        statuses.append(_status(c))
        all_headers.extend(page)
        nxt = _next_cursor(page)
        if nxt is not None:
            next_cursors[str(cid)] = nxt
        if any(r["recipient_type"] == "mailing_list" for h in page for r in h["recipients"]):
            list_names[cid] = _list_names(client, c, role)

    mails = _enrich_and_group(all_headers, {c["character_id"]: c for c in chars}, client, list_names)
    return {"mails": mails, "next_cursors": next_cursors, "characters": statuses}


def do_search_mail(q: str, character_id: Optional[int] = None, limit: int = 100) -> dict:
    """Full-text search (subject + body) over ARCHIVED characters only: live
    mail is never indexed, so it cannot be searched server-side. The response
    says which characters were searched and which could not be."""
    q = (q or "").strip()
    if len(q) < 2:
        raise ActionError("Enter at least 2 characters to search.")
    chars = _mail_characters(character_id)
    searched = [c for c in chars if c["archived"]]
    unsearchable = [_status(c) for c in chars if not c["archived"]]
    headers = storage.search_mail_archive([c["character_id"] for c in searched], q, min(max(limit, 1), 200))
    client, _tokens = _client_and_tokens()
    mails = _enrich_and_group(headers, {c["character_id"]: c for c in chars}, client, {})
    return {
        "mails": mails, "searched": [_status(c) for c in searched], "unsearchable": unsearchable,
    }


# ---------------------------------------------------------------------- open
def do_open_mail(character_id: int, mail_id: int) -> dict:
    """One mail with its body. Live character: fetched from ESI (10 minute
    in-memory cache, never stored). Archived character: from the archive; a
    body the backfill has not reached yet is fetched live and stored."""
    (char,) = _mail_characters(character_id)
    try:
        mail_id = int(mail_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid mail_id {mail_id!r}") from e
    cid = char["character_id"]
    client, tokens = _client_and_tokens()
    role = select_auth_role(cid, MAIL_SCOPE, tokens=tokens)
    if role is None:
        raise ActionError(
            f"{char['character_name']} needs a re-authorize with the mail scope (Characters page)."
        )

    archived_row = storage.get_archived_mail(cid, mail_id) if char["archived"] else None
    if archived_row is not None and archived_row["body"] is not None and archived_row["body"] != "":
        return _open_result(char, archived_row, archived_row["body"], client, tokens)
    try:
        raw = client.character_mail_body(cid, role, mail_id)
    except ESIError as e:
        raise ActionError(f"Could not load this mail ({esi_failure(e)}).") from e
    body = raw.get("body") or ""
    if archived_row is not None:
        storage.store_mail_body(cid, mail_id, body)
        detail = archived_row
    else:
        detail = {
            "mail_id": mail_id, "from_id": raw.get("from"), "subject": raw.get("subject") or "",
            "timestamp": raw.get("timestamp"), "is_read": bool(raw.get("read")),
            "labels": list(raw.get("labels") or []),
            "recipients": [
                {"recipient_id": int(x["recipient_id"]), "recipient_type": x["recipient_type"]}
                for x in raw.get("recipients") or []
            ],
        }
    return _open_result(char, detail, body, client, tokens)


def _open_result(char: dict, detail: dict, body: str, client: ESIClient, tokens: TokenManager) -> dict:
    role = select_auth_role(char["character_id"], MAIL_SCOPE, tokens=tokens)
    lists = _list_names(client, char, role) if role and any(
        r["recipient_type"] == "mailing_list" for r in detail["recipients"]
    ) else {}
    (row,) = _enrich_and_group(
        [{**detail, "character_id": char["character_id"]}],
        {char["character_id"]: char}, client, {char["character_id"]: lists},
    )
    row.update({"body": body, "character_id": char["character_id"], "archived": char["archived"]})
    return row


# ------------------------------------------------------------------- archive
def _archive_status_row(char: dict, registered_only: dict, tokens: TokenManager, shared: bool) -> dict:
    cid = char["character_id"]
    settings = registered_only.get(cid)
    enabled = settings is not None
    state = settings["backfill_state"] if settings else "off"
    if state == "running" and not mail_archive.backfill_alive(cid):
        state = "interrupted"      # the thread died with the process; a refresh resumes it
    return {
        "character_id": cid, "character_name": char["character_name"],
        "shared": shared, "archive_enabled": enabled,
        "reauth_needed": select_auth_role(cid, MAIL_SCOPE, tokens=tokens) is None,
        "backfill_state": state,
        "headers_complete": bool(settings and settings["headers_complete"]),
        "error": settings["backfill_error"] if settings else None,
        "last_refresh_at": settings["last_refresh_at"] if settings else None,
        "counts": storage.mail_archive_counts(cid) if enabled else {"headers": 0, "bodies": 0},
    }


def do_mail_archive_status() -> dict:
    """Every registered character with its archive checkbox, whether Mail may
    use it at all (shared), and the backfill progress."""
    registered = fields.token_characters()
    shared = set(shared_owner_ids("mail", TOOL_KEY, "character"))
    settings = storage.get_mail_archive_settings([c["character_id"] for c in registered])
    tokens = TokenManager(OAUTH_CONFIG)
    return {"characters": [
        _archive_status_row(c, settings, tokens, c["character_id"] in shared) for c in registered
    ]}


def do_set_mail_archive(character_id: int, enabled: bool, confirm_delete: bool = False) -> dict:
    """Ticks or unticks a character's "Archive mail" checkbox.

    Enabling starts the backfill. Disabling DELETES the character's archive
    (the explicit deletion path - decision 4); when anything is archived the
    caller must pass `confirm_delete=True`, so a stray API call cannot wipe a
    mailbox."""
    registered = fields.token_characters()
    char = next((c for c in registered if c["character_id"] == int(character_id)), None)
    if char is None:
        raise ActionError(f"Character {character_id} is not registered for ESI access.")
    cid = char["character_id"]
    tokens = TokenManager(OAUTH_CONFIG)

    if enabled:
        if cid not in set(shared_owner_ids("mail", TOOL_KEY, "character")):
            raise ActionError(
                f"{char['character_name']} is not shared with Mail. Tick it on the Characters page first."
            )
        if select_auth_role(cid, MAIL_SCOPE, tokens=tokens) is None:
            raise ActionError(
                f"{char['character_name']} needs a re-authorize with the mail scope (Characters page)."
            )
        storage.enable_mail_archive(cid)
        mail_archive.ensure_backfill(cid)
    else:
        counts = storage.mail_archive_counts(cid)
        if counts["headers"] > 0 and not confirm_delete:
            raise ActionError(
                f"Turning the archive off deletes {counts['headers']} archived mail(s) of "
                f"{char['character_name']}. Confirm to delete them."
            )
        deleted = storage.delete_mail_archive(cid)
        return {"character_id": cid, "archive_enabled": False, "deleted": deleted}

    settings = storage.get_mail_archive_settings([cid])
    return _archive_status_row(char, settings, tokens, True)


def do_refresh_mail_archive(character_id: Optional[int] = None) -> dict:
    """Incremental refresh (synchronous, bounded) of the archived characters,
    then resumes the backfill wherever history or bodies are still missing."""
    chars = _mail_characters(character_id, archived_only=True)
    client, tokens = _client_and_tokens()
    results = []
    for c in chars:
        cid = c["character_id"]
        role = select_auth_role(cid, MAIL_SCOPE, tokens=tokens)
        if role is None:
            results.append(_status(c, STATE_REAUTH))
            continue
        try:
            summary = mail_archive.refresh_character(cid, client, role)
        except ESIError as e:
            results.append(_status(c, STATE_ERROR, esi_failure(e)))
            continue
        settings = storage.get_mail_archive_settings([cid]).get(cid)
        if settings and (
            not settings["headers_complete"] or storage.mail_ids_without_body(cid, 1)
        ):
            mail_archive.ensure_backfill(cid)
        results.append({**_status(c), **summary})
    return {"characters": results}
