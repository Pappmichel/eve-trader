"""Mail write actions (docs/CHARACTER_MANAGEMENT_PLAN.md phase 4): send,
mark read, set labels, delete, create/delete labels, recipient lookup.

These act on a character's behalf in the game, so every one of them checks, in
this order and failing closed:
1. the character is registered and shared with `char_mail`;
2. the write *capability* is ticked (`mail_send` / `mail_organize` on the
   Characters page) - the user's explicit consent - AND a stored token holds
   its scope (`fields.capability_ready`); a tick without the scope tells the
   user to re-authorize;
3. only then is ESI called.

Sending is never retried (a retry can deliver twice, R3): an unknown outcome
becomes an `ActionError` telling the user to check the Sent folder first. No
subject, body or ESI response text ever reaches an error message or a log.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .. import storage
from ..actions import ActionError
from ..esi_client import ESIClient, ESIDeliveryUnknown, ESIError, ESIHTTPError
from . import fields, mail_actions, mail_archive
from .fields import esi_failure

log = logging.getLogger(__name__)

SEND_KEY, SEND_SCOPE = "mail_send", "esi-mail.send_mail.v1"
ORGANIZE_KEY, ORGANIZE_SCOPE = "mail_organize", "esi-mail.organize_mail.v1"

MAX_RECIPIENTS = 50      # ESI's per-mail recipient cap
MAX_SUBJECT = 1000
MAX_BODY = 10000
MAX_LABEL_NAME = 40
# ESI accepts exactly these label colours.
LABEL_COLORS = (
    "#0000fe", "#006634", "#0099ff", "#00ff33", "#01ffff", "#349800", "#660066", "#666666",
    "#999999", "#99ffff", "#9a0000", "#ccff9a", "#e6e6e6", "#fe0000", "#ff6600", "#ffff01",
    "#ffffcd", "#ffffff",
)
_RECIPIENT_TYPES = ("character", "corporation", "alliance", "mailing_list")
_SYSTEM_LABEL_IDS = tuple(mail_actions.SYSTEM_LABELS)
_COST = re.compile(r"(?i)(?:cost|charge|cspa)[^0-9]{0,40}([0-9][0-9.,]*)")


def _writer(character_id: Any, capability_key: str, scope: str, what: str):
    """(character, role, client) for a write, or an ActionError naming exactly
    what is missing."""
    (char,) = mail_actions._mail_characters(character_id)
    client, tokens = mail_actions._client_and_tokens()
    state = fields.capability_ready(char["character_id"], capability_key, scope, tokens)
    name = char["character_name"]
    if state == "not_enabled":
        label = "Send mail" if capability_key == SEND_KEY else "Organize mail"
        raise ActionError(
            f"{name} may not {what}: tick \"{label}\" for this character on the Characters page, "
            f"then re-authorize."
        )
    if state == fields.STATE_REAUTH:
        raise ActionError(
            f"{name} needs a re-authorize with the {what} scope (Characters page) before this works."
        )
    role = mail_actions.select_auth_role(char["character_id"], scope, tokens=tokens)
    return char, role, client


def _invalidate(character_id: int) -> None:
    """The next list/folder read must show the change, not a 30 s old page."""
    tenant_id = storage.get_current_tenant()
    if tenant_id:
        ESIClient.invalidate_live_character_caches(str(tenant_id), character_id, prefix="mail_")


def _int(value: Any, what: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid {what}: {value!r}") from e
    if n <= 0:
        raise ActionError(f"Invalid {what}: {value!r}")
    return n


def _failed(what: str, e: ESIError) -> ActionError:
    return ActionError(f"Could not {what} ({esi_failure(e)}).")


# ---------------------------------------------------------------- recipients
def _own_lists(client: ESIClient, char: dict, role: str) -> dict[int, str]:
    read_role = mail_actions.select_auth_role(
        char["character_id"], mail_actions.MAIL_SCOPE, tokens=mail_actions._client_and_tokens()[1],
    ) or role
    try:
        return {int(x["mailing_list_id"]): x.get("name") or "" for x in client.character_mail_lists(
            char["character_id"], read_role)}
    except ESIError:
        return {}


def _resolve_recipients(client: ESIClient, char: dict, role: str, recipients: list[dict]) -> list[dict]:
    """[{type, id?, name?}] -> [{recipient_id, recipient_type, name}], deduped.
    Entries with an id are kept as given (a mailing list must be one the sender
    subscribes to); name-only entries are resolved exactly (case-insensitive)."""
    if not isinstance(recipients, list) or not recipients:
        raise ActionError("Add at least one recipient.")
    if len(recipients) > MAX_RECIPIENTS:
        raise ActionError(f"A mail can have at most {MAX_RECIPIENTS} recipients.")
    own_lists = None
    resolved: dict[int, dict] = {}       # input position -> recipient, so the mail keeps the user's order
    name_only: list[dict] = []
    for idx, r in enumerate(recipients):
        rtype = (r.get("type") or "").strip()
        if rtype and rtype not in _RECIPIENT_TYPES:
            raise ActionError(f"Unknown recipient type {rtype!r}.")
        if r.get("id") is not None:
            if not rtype:
                raise ActionError("A recipient with an id needs a type.")
            rid = _int(r["id"], "recipient id")
            if rtype == "mailing_list":
                own_lists = own_lists if own_lists is not None else _own_lists(client, char, role)
                if rid not in own_lists:
                    raise ActionError("You can only mail a mailing list this character is subscribed to.")
                name = own_lists[rid]
            else:
                name = r.get("name")
            resolved[idx] = {"recipient_id": rid, "recipient_type": rtype, "name": name}
        elif (r.get("name") or "").strip():
            name_only.append({"idx": idx, "type": rtype, "name": r["name"].strip()})
        else:
            raise ActionError("Every recipient needs a name or an id.")

    unresolved: list[str] = []
    if name_only:
        own_lists = own_lists if own_lists is not None else _own_lists(client, char, role)
        by_list_name = {n.lower(): (i, n) for i, n in own_lists.items()}
        lookup_names = [e["name"] for e in name_only if e["type"] != "mailing_list"
                        and e["name"].lower() not in by_list_name]
        found = client.resolve_recipient_names(lookup_names) if lookup_names else {}
        for e in name_only:
            want = e["name"].lower()
            hit: Optional[dict] = None
            if e["type"] in ("", "mailing_list") and want in by_list_name:
                list_id, list_name = by_list_name[want]
                hit = {"recipient_id": list_id, "recipient_type": "mailing_list", "name": list_name}
            elif e["type"] != "mailing_list":
                for kind in ([e["type"]] if e["type"] else ["character", "corporation", "alliance"]):
                    match = next((x for x in found.get(kind, []) if x["name"].lower() == want), None)
                    if match:
                        hit = {"recipient_id": int(match["id"]), "recipient_type": kind, "name": match["name"]}
                        break
            if hit is None:
                unresolved.append(e["name"])
            else:
                resolved[e["idx"]] = hit
    if unresolved:
        raise ActionError(f"Could not find: {', '.join(unresolved)}.")

    unique: dict[tuple[str, int], dict] = {}
    for idx in sorted(resolved):
        r = resolved[idx]
        unique.setdefault((r["recipient_type"], r["recipient_id"]), r)
    return list(unique.values())


def do_search_recipients(character_id: int, q: str) -> dict:
    """Autocomplete for the compose form: the sender's own mailing lists plus
    a public ESI search over characters, corporations and alliances."""
    q = (q or "").strip()
    if len(q) < 3:
        raise ActionError("Type at least 3 characters to search.")
    (char,) = mail_actions._mail_characters(character_id)
    client, tokens = mail_actions._client_and_tokens()
    results: list[dict] = []
    role = mail_actions.select_auth_role(char["character_id"], mail_actions.MAIL_SCOPE, tokens=tokens)
    if role:
        results.extend(
            {"type": "mailing_list", "id": i, "name": n}
            for i, n in _own_lists(client, char, role).items() if q.lower() in n.lower()
        )
    try:
        results.extend(client.search_entities(q))
    except ESIError as e:
        raise _failed("search for recipients", e) from e
    return {"results": results[:24]}


# ------------------------------------------------------------------- sending
def _cost_from(body: str) -> Optional[float]:
    match = _COST.search(body or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "").rstrip("."))
    except ValueError:
        return None


def do_send_mail(
    from_character_id: int, recipients: list[dict], subject: str, body: str, approved_cost: int = 0,
) -> dict:
    """Sends one mail as `from_character_id`.

    Returns `{"sent": True, "mail_id", "recipients"}`. If ESI wants a CSPA
    charge approved first, returns `{"sent": False, "needs_approval": True,
    "cost"}` and sends nothing - the UI asks, then repeats the call with
    `approved_cost`."""
    char, role, client = _writer(from_character_id, SEND_KEY, SEND_SCOPE, "send mail")
    cid = char["character_id"]
    subject = (subject or "").strip()
    body = body or ""
    if not subject:
        raise ActionError("A mail needs a subject.")
    if len(subject) > MAX_SUBJECT:
        raise ActionError(f"The subject can be at most {MAX_SUBJECT} characters.")
    if len(body) > MAX_BODY:
        raise ActionError(f"The message can be at most {MAX_BODY} characters (it has {len(body)}).")
    try:
        approved = int(approved_cost or 0)
    except (TypeError, ValueError) as e:
        raise ActionError("Invalid approved_cost.") from e
    if approved < 0:
        raise ActionError("Invalid approved_cost.")

    try:
        resolved = _resolve_recipients(client, char, role, recipients)
    except ESIError as e:
        raise _failed("look up the recipients", e) from e

    payload = {
        "approved_cost": approved, "body": body, "subject": subject,
        "recipients": [
            {"recipient_id": r["recipient_id"], "recipient_type": r["recipient_type"]} for r in resolved
        ],
    }
    try:
        mail_id = client.send_mail(cid, role, payload)
    except ESIDeliveryUnknown:
        _invalidate(cid)
        raise ActionError(
            "ESI did not confirm the delivery, so the mail may or may not have been sent. "
            "Check the Sent folder before sending it again."
        ) from None
    except ESIHTTPError as e:
        cost = _cost_from(e.body) if e.status in (400, 402, 403) else None
        if cost is not None and cost > approved:
            return {"sent": False, "needs_approval": True, "cost": cost}
        raise ActionError(f"ESI refused to send the mail ({esi_failure(e)}).") from None
    except ESIError as e:
        raise _failed("send the mail", e) from e

    _invalidate(cid)
    if char["archived"]:
        # The archive is a copy of the mailbox: the sent mail belongs in it now,
        # not only after the next refresh. Guarded like every archive write.
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if storage.store_mail_headers(cid, [{
            "mail_id": mail_id, "from": cid, "subject": subject, "timestamp": now,
            "is_read": True, "labels": [2],
            "recipients": [{"recipient_id": r["recipient_id"], "recipient_type": r["recipient_type"]}
                           for r in resolved],
        }]):
            storage.store_mail_body(cid, mail_id, body)
    return {"sent": True, "mail_id": mail_id, "recipients": resolved}


# ------------------------------------------------------------------ organize
def do_mark_mail_read(character_id: int, mail_id: int, read: bool = True) -> dict:
    char, role, client = _writer(character_id, ORGANIZE_KEY, ORGANIZE_SCOPE, "organize mail")
    cid, mid = char["character_id"], _int(mail_id, "mail_id")
    try:
        client.update_mail(cid, role, mid, {"read": bool(read)})
    except ESIError as e:
        raise _failed("change the read state", e) from e
    _invalidate(cid)
    if char["archived"]:
        storage.set_archived_mail_read(cid, mid, bool(read))
    return {"character_id": cid, "mail_id": mid, "read": bool(read)}


def do_set_mail_labels(character_id: int, mail_id: int, labels: list[int]) -> dict:
    """Replaces the mail's label set (ESI semantics). System labels (Inbox,
    Sent, ...) in the list are passed through unchanged."""
    char, role, client = _writer(character_id, ORGANIZE_KEY, ORGANIZE_SCOPE, "organize mail")
    cid, mid = char["character_id"], _int(mail_id, "mail_id")
    if not isinstance(labels, list):
        raise ActionError("labels must be a list of label ids.")
    ids = sorted({_int(x, "label id") for x in labels})
    try:
        client.update_mail(cid, role, mid, {"labels": ids})
    except ESIError as e:
        raise _failed("set the labels", e) from e
    _invalidate(cid)
    if char["archived"]:
        storage.set_archived_mail_labels(cid, mid, ids)
    return {"character_id": cid, "mail_id": mid, "labels": ids}


def do_delete_mail(character_id: int, mail_id: int) -> dict:
    """Deletes the mail IN THE GAME and, if the character is archived, from the
    archive as well (an explicit user action; the UI confirms first)."""
    char, role, client = _writer(character_id, ORGANIZE_KEY, ORGANIZE_SCOPE, "organize mail")
    cid, mid = char["character_id"], _int(mail_id, "mail_id")
    try:
        client.delete_mail(cid, role, mid)
    except ESIError as e:
        raise _failed("delete the mail", e) from e
    _invalidate(cid)
    if char["archived"]:
        storage.delete_archived_mail(cid, mid)
    return {"character_id": cid, "mail_id": mid, "deleted": True}


def do_create_mail_label(character_id: int, name: str, color: Optional[str] = None) -> dict:
    char, role, client = _writer(character_id, ORGANIZE_KEY, ORGANIZE_SCOPE, "organize mail")
    cid = char["character_id"]
    name = (name or "").strip()
    color = (color or "#ffffff").lower()
    if not name:
        raise ActionError("A label needs a name.")
    if len(name) > MAX_LABEL_NAME:
        raise ActionError(f"A label name can be at most {MAX_LABEL_NAME} characters.")
    if color not in LABEL_COLORS:
        raise ActionError("EVE only accepts its fixed label colours; pick one from the list.")
    try:
        label_id = client.create_mail_label(cid, role, name, color)
    except ESIError as e:
        raise _failed("create the label", e) from e
    _invalidate(cid)
    _resync_archive_labels(char, client)
    return {"character_id": cid, "label_id": label_id, "name": name, "color": color}


def do_delete_mail_label(character_id: int, label_id: int) -> dict:
    char, role, client = _writer(character_id, ORGANIZE_KEY, ORGANIZE_SCOPE, "organize mail")
    cid, lid = char["character_id"], _int(label_id, "label id")
    if lid in _SYSTEM_LABEL_IDS:
        raise ActionError("The built-in folders (Inbox, Sent, Corp, Alliance, Mailing lists) cannot be deleted.")
    try:
        client.delete_mail_label(cid, role, lid)
    except ESIError as e:
        raise _failed("delete the label", e) from e
    _invalidate(cid)
    _resync_archive_labels(char, client)
    return {"character_id": cid, "label_id": lid, "deleted": True}


def _resync_archive_labels(char: dict, client: ESIClient) -> None:
    """Best-effort: keep an archived character's stored label list current."""
    if not char["archived"]:
        return
    tokens = mail_actions._client_and_tokens()[1]
    role = mail_actions.select_auth_role(char["character_id"], mail_actions.MAIL_SCOPE, tokens=tokens)
    if role is None:
        return
    try:
        mail_archive.sync_labels_and_lists(char["character_id"], client, role)
    except ESIError:
        log.info("could not refresh stored mail labels for character %s", char["character_id"])
