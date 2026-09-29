"""Opt-in mail archive: incremental refresh and resumable backfill
(docs/CHARACTER_MANAGEMENT_PLAN.md phase 3b, R11).

Mail is read live by default; this module only ever runs for a character whose
"Archive mail" checkbox is ticked. It deliberately lives *outside* the ESI
orchestrator (`esi_data.do_sync_*`): a first backfill of an old mailbox is
thousands of ESI pages and bodies, which must not sit inside an owner batch
(one pooled DB connection held for the whole run), and the orchestrator's
stale clear must never be able to delete an archive after one failed refresh.

The backfill runs on its own daemon thread, not `pipeline_runner`: that runner
allows one running job per *tenant*, so a backfill lasting minutes would block
Trading/Production jobs. Progress and the resume cursor live in
`char_mail_archive_settings`; a thread that died with the process is reported
as "interrupted" and continues from the cursor on the next refresh.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data import select_auth_role
from .fields import SCOPE_BY_KIND, esi_failure

log = logging.getLogger(__name__)

MAIL_SCOPE = SCOPE_BY_KIND["mail"]
PAGE_SIZE = 50                # ESI's fixed mail-header page size
REFRESH_WINDOW = 500          # newest headers re-read on a refresh (read/label changes)
BODY_BATCH = 50
_PAUSE_SECONDS = 0.1          # between pages/bodies; the ESI error budget does the real throttling

_threads: dict[tuple[str, int], threading.Thread] = {}
_threads_lock = threading.Lock()


def _label_rows(raw: dict) -> list[tuple]:
    return [
        (int(lb["label_id"]), lb.get("name") or "", lb.get("color"), lb.get("unread_count"))
        for lb in raw.get("labels", [])
    ]


def _list_rows(raw: list[dict]) -> list[tuple]:
    return [(int(x["mailing_list_id"]), x.get("name") or "") for x in raw]


def sync_labels_and_lists(character_id: int, client: ESIClient, role: str) -> None:
    storage.replace_mail_labels(character_id, _label_rows(client.character_mail_labels(character_id, role)))
    storage.replace_mail_lists(character_id, _list_rows(client.character_mail_lists(character_id, role)))


def refresh_character(character_id: int, client: ESIClient, role: str) -> dict:
    """Synchronous incremental refresh: labels + lists, then the newest
    REFRESH_WINDOW headers (new mail, and read/label changes on recent mail).
    Older history is the backfill's job, not a request's."""
    sync_labels_and_lists(character_id, client, role)
    covered, new, cursor = 0, 0, None
    while True:
        page = client.character_mail_headers(character_id, role, last_mail_id=cursor, cache=False)
        if not page:
            break
        ids = [int(h["mail_id"]) for h in page]
        known = storage.known_mail_ids(character_id, ids)
        if not storage.store_mail_headers(character_id, page):
            return {"stopped": "archive disabled", "new_mails": new, "covered": covered}
        new += len(ids) - len(known)
        covered += len(page)
        if len(page) < PAGE_SIZE or covered >= REFRESH_WINDOW:
            break
        cursor = min(ids)
    storage.update_mail_archive_state(character_id, touch_refresh=True)
    return {"new_mails": new, "covered": covered}


def _still_enabled(character_id: int) -> bool:
    return character_id in storage.get_mail_archive_settings([character_id])


def backfill_character(
    character_id: int, client: Optional[ESIClient] = None, tokens: Optional[TokenManager] = None,
) -> dict:
    """Runs (or resumes) the backfill for one archived character, in the
    current tenant scope: header history first (from the stored cursor), then
    every missing body. Resumable at any point; stops as soon as the archive
    is disabled. Errors are recorded on the settings row (redacted) - never
    raised, since this runs on a background thread."""
    settings = storage.get_mail_archive_settings([character_id]).get(character_id)
    if settings is None:
        return {"skipped": "archive not enabled"}
    tokens = tokens or TokenManager(OAUTH_CONFIG)
    client = client or ESIClient(tokens=tokens)
    role = select_auth_role(character_id, MAIL_SCOPE, tokens=tokens)
    if role is None:
        storage.update_mail_archive_state(
            character_id, backfill_state="error", backfill_error="re-auth needed (no token holds the mail scope)",
        )
        return {"error": "reauth_needed"}

    storage.update_mail_archive_state(character_id, backfill_state="running", clear_error=True)
    try:
        if not settings["headers_complete"]:
            cursor = settings["backfill_cursor"]
            while True:
                if not _still_enabled(character_id):
                    return {"stopped": "archive disabled"}
                page = client.character_mail_headers(character_id, role, last_mail_id=cursor, cache=False)
                if page and not storage.store_mail_headers(character_id, page):
                    return {"stopped": "archive disabled"}
                if len(page) < PAGE_SIZE:
                    storage.update_mail_archive_state(
                        character_id, headers_complete=True, backfill_cursor=None, set_cursor=True,
                    )
                    break
                cursor = min(int(h["mail_id"]) for h in page)
                storage.update_mail_archive_state(character_id, backfill_cursor=cursor, set_cursor=True)
                time.sleep(_PAUSE_SECONDS)

        while True:
            if not _still_enabled(character_id):
                return {"stopped": "archive disabled"}
            ids = storage.mail_ids_without_body(character_id, BODY_BATCH)
            if not ids:
                break
            for mail_id in ids:
                storage.store_mail_body(character_id, mail_id, _fetch_body(client, character_id, role, mail_id))
                time.sleep(_PAUSE_SECONDS)
    except ESIError as e:
        storage.update_mail_archive_state(character_id, backfill_state="error", backfill_error=esi_failure(e))
        return {"error": esi_failure(e)}
    except Exception:  # noqa: BLE001 - a background thread must record, not die silently
        log.exception("mail archive backfill failed for character %s", character_id)
        storage.update_mail_archive_state(
            character_id, backfill_state="error", backfill_error="unexpected error (see server log)",
        )
        return {"error": "unexpected"}

    storage.update_mail_archive_state(character_id, backfill_state="done", touch_refresh=True)
    return {"done": True}


def _fetch_body(client: ESIClient, character_id: int, role: str, mail_id: int) -> str:
    """One body. A mail ESI no longer has (deleted since the header was
    read: HTTP 404/403 for that one id) is stored as empty so the backfill
    does not retry it forever; any other failure aborts the run."""
    try:
        return client.character_mail_body(character_id, role, mail_id, cache=False).get("body") or ""
    except ESIError as e:
        status = esi_failure(e)
        if status in ("ESI returned HTTP 404", "ESI returned HTTP 403"):
            return ""
        raise


def ensure_backfill(character_id: int) -> bool:
    """Starts the backfill thread for this character unless one is already
    alive in this process. True if a thread was started."""
    tenant_id = storage.get_current_tenant()
    if not tenant_id:
        raise RuntimeError("mail backfill requires a tenant in scope")
    key = (str(tenant_id), int(character_id))
    with _threads_lock:
        existing = _threads.get(key)
        if existing is not None and existing.is_alive():
            return False
        thread = threading.Thread(
            target=storage.with_current_tenant(lambda: _run_in_tenant(str(tenant_id), int(character_id))),
            daemon=True, name=f"mail-archive-{character_id}",
        )
        _threads[key] = thread
        thread.start()
        return True


def _run_in_tenant(tenant_id: str, character_id: int) -> None:
    # Local import, same reason as the orchestrator: tenant_scope pulls the
    # tool config modules, which the package import path must not load.
    from .. import tenant_scope
    with tenant_scope.enter_tenant(tenant_id):
        backfill_character(character_id)


def backfill_alive(character_id: int) -> bool:
    tenant_id = storage.get_current_tenant()
    thread = _threads.get((str(tenant_id), int(character_id)))
    return thread is not None and thread.is_alive()
