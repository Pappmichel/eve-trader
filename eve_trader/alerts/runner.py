"""The alerts job body (docs/DISCORD_ALERTS_HANDOFF.md): one call per tenant per
scheduler tick. Only work an explicit opt-in asks for ever happens here.

Skill queue: evaluated every tick from the stored snapshot (a local read, no
ESI call). Only when a warning would go out is the queue re-read live once, so
a queue the user just extended never produces a false alarm. The on_demand
`skillqueue` snapshot itself is refreshed through `do_sync_due(demand=...)`,
restricted to rows shared with `char_alerts` so an inactive tenant's other data
is never fetched.

Mail: `mail` is live-only (never an orchestrator kind), so it is polled here,
headers only; bodies only with the `include_content` opt-in. The first poll
after an opt-in stores a baseline and sends nothing.

A failed attempt (ESI or Discord) records `last_attempt_at` and leaves the
dedupe cursor alone, so nothing is lost or duplicated and a broken destination
is retried at most every `RETRY_BACKOFF_MINUTES`, never on every tick.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, SCHEDULER_OPERATOR_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data import read_esi, select_auth_role
from ..esi_data import orchestrator as esi_orchestrator
from ..character_management import fields
from . import discord_client, logic
from .actions import KIND_BY_ALERT, TOOL_KEY, deliver

log = logging.getLogger(__name__)

RETRY_BACKOFF_MINUTES = 15
MAX_BODY_CHARS = 200
_TAG_RE = re.compile(r"<[^>]+>")

# Per-tenant in-process guard (confirmed real gap, code review 2026-10-01):
# scheduler._run_job joins a job thread for at most JOB_TIMEOUT_SECONDS (15
# min) and then abandons it, still running, if it hasn't finished - the next
# 5-minute tick would otherwise start a second run_for_tenant for the same
# tenant while the first is still mid-flight. Both would read the same
# alert_state row before either writes it, so a slow tick (ESI/Discord both
# near their own timeouts) could send the same Discord DM twice. Same shape
# as esi_data.orchestrator's own _try_begin_owner/_end_owner, one level up
# (per tenant, not per owner) since this job's unit of work is the whole
# tenant's alert pass, not one ESI owner.
_guard_mu = threading.Lock()
_in_flight: set[str] = set()


def _try_begin_tenant(tenant_id: str) -> bool:
    with _guard_mu:
        if tenant_id in _in_flight:
            return False
        _in_flight.add(tenant_id)
        return True


def _end_tenant(tenant_id: str) -> None:
    with _guard_mu:
        _in_flight.discard(tenant_id)


def _aware(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        return logic.parse_iso(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _recently_attempted(state, now: datetime, minutes: float) -> bool:
    last = _aware(state[3]) if state else None
    return last is not None and (now - last) < timedelta(minutes=minutes)


def _clean_body(text: str) -> str:
    return " ".join(_TAG_RE.sub(" ", text or "").split())[:MAX_BODY_CHARS]


def enabled_subscriptions() -> list[tuple]:
    """`(character_id, alert_type, include_content, lead_hours)` of every
    switched-on subscription of the ambient tenant."""
    return [(cid, t, inc, lead) for cid, t, en, inc, lead in storage.list_alert_subscriptions() if en]


def skillqueue_demand(subs: list[tuple]) -> set[tuple[str, int, str]]:
    return {("character", cid, "skillqueue") for cid, t, _i, _l in subs if t == logic.SKILLQUEUE_EMPTY}


def _names() -> dict[int, str]:
    return {c["character_id"]: c["character_name"] for c in fields.token_characters()}


def _send(cid: int, alert_type: str, decision: logic.Decision, now: datetime, *, mail_cursor: Optional[int] = None,
          tokens: Optional[TokenManager] = None) -> bool:
    """Deliver and record. True = sent."""
    try:
        sent = deliver(cid, alert_type, decision.message, tokens=tokens)
    except discord_client.DiscordError as e:
        log.warning("Discord alert %s for character %s not delivered: %s", alert_type, cid, e)
        storage.save_alert_state(cid, alert_type, at=now)   # attempt only: cursor stays, backoff applies
        return False
    if sent:
        storage.save_alert_state(cid, alert_type, last_seen_mail_id=mail_cursor, last_key=decision.key, sent=True, at=now)
    return sent


def _live_finish_dates(client: ESIClient, cid: int, tokens: TokenManager) -> Optional[list[Optional[str]]]:
    role = select_auth_role(cid, fields.SCOPE_BY_KIND["skillqueue"], tokens=tokens)
    if role is None:
        return None
    return [row.get("finish_date") for row in client.character_skillqueue(cid, role)]


def _run_skillqueue(cid: int, lead_hours: int, name: str, now: datetime, client: ESIClient,
                    tokens: TokenManager, freshness: dict) -> None:
    if fields.gate("skillqueue", TOOL_KEY, cid, tokens):
        return
    if not (freshness.get(cid, {}).get("skillqueue") or {}).get("last_success_at"):
        return                                              # never synced: "no rows" would read as an empty queue
    rows = read_esi("skillqueue", TOOL_KEY, owner_type="character", owner_id=cid)
    state = storage.get_alert_state(cid, logic.SKILLQUEUE_EMPTY)
    decision = logic.skillqueue_decision(
        [r["finish_date"] for r in rows], now, lead_hours, state[1] if state else None, name)
    if decision is None or _recently_attempted(state, now, RETRY_BACKOFF_MINUTES):
        return
    try:
        live = _live_finish_dates(client, cid, tokens)
    except ESIError as e:
        log.warning("Skill queue re-check for character %s failed: %s", cid, e)
        live = None
    if live is None:
        storage.save_alert_state(cid, logic.SKILLQUEUE_EMPTY, at=now)      # unverified: never alarm on stale data
        return
    decision = logic.skillqueue_decision(live, now, lead_hours, state[1] if state else None, name)
    if decision is None:                                    # the queue was extended meanwhile
        storage.save_alert_state(cid, logic.SKILLQUEUE_EMPTY, at=now)
        return
    _send(cid, logic.SKILLQUEUE_EMPTY, decision, now, tokens=tokens)


def _sender_names(client: ESIClient, ids: list[int]) -> dict[int, str]:
    """Names for mail sender ids (public ESI data). One unresolvable id 404s a
    whole batch, so a failed batch falls back to one call per id; anything
    still unresolved just shows as an unknown sender."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    try:
        return client.resolve_names_cached(ids)
    except ESIError:
        names: dict[int, str] = {}
        for i in ids:
            try:
                names.update(client.resolve_names_cached([i]))
            except ESIError:
                pass
        return names


def _run_mail(cid: int, include_content: bool, name: str, now: datetime, poll_minutes: float,
              client: ESIClient, tokens: TokenManager) -> None:
    state = storage.get_alert_state(cid, logic.MAIL_NEW)
    if _recently_attempted(state, now, poll_minutes):
        return
    if fields.gate("mail", TOOL_KEY, cid, tokens):
        return
    role = select_auth_role(cid, fields.SCOPE_BY_KIND["mail"], tokens=tokens)
    if role is None:
        return
    last_seen = state[0] if state else None
    try:
        headers = client.character_mail_headers(cid, role, cache=False)
    except ESIError as e:
        log.warning("Mail poll for character %s failed: %s", cid, e)
        storage.save_alert_state(cid, logic.MAIL_NEW, at=now)
        return
    bodies: dict[int, str] = {}
    senders: dict[int, str] = {}
    if last_seen is not None:
        fresh_headers = sorted(
            (h for h in headers if int(h["mail_id"]) > last_seen and not h.get("is_read")),
            key=lambda h: int(h["mail_id"]),
        )[:logic.MAX_MAILS_LISTED]
        senders = _sender_names(client, [int(h["from"]) for h in fresh_headers if h.get("from")])
        fresh = [int(h["mail_id"]) for h in fresh_headers] if include_content else []
        for mail_id in fresh:
            try:
                bodies[mail_id] = _clean_body(client.character_mail_body(cid, role, mail_id, cache=False).get("body", ""))
            except ESIError:
                pass                                        # a subject-only line beats no alert
    result = logic.mail_decision(headers, last_seen, include_content, name, bodies, senders)
    cursor = result.newest_id if result.newest_id is not None else 0     # 0 = empty inbox baseline
    if result.baseline or result.decision is None:
        storage.save_alert_state(cid, logic.MAIL_NEW, last_seen_mail_id=cursor, at=now)
        return
    _send(cid, logic.MAIL_NEW, result.decision, now, mail_cursor=cursor, tokens=tokens)


def run_for_tenant(
    *, now: Optional[datetime] = None, client: Optional[ESIClient] = None,
    tokens: Optional[TokenManager] = None, poll_minutes: Optional[float] = None,
) -> dict:
    """Run every enabled subscription of the ambient tenant. Returns counts
    for logging/tests. Caller must have entered the tenant scope.

    Guarded per tenant (`_try_begin_tenant`/`_end_tenant`): a run already in
    flight for this tenant returns immediately with `skipped: "in_flight"`
    instead of starting a second pass that would read the same `alert_state`
    row the first hasn't written yet and could re-send an already-delivered
    alert (see the module-level guard's own comment)."""
    tenant_id = storage.get_current_tenant()
    if not tenant_id:
        raise RuntimeError("alerts run_for_tenant requires a tenant in scope")
    tenant_id = str(tenant_id)
    if not _try_begin_tenant(tenant_id):
        return {"subscriptions": 0, "ran": False, "skipped": "in_flight"}
    try:
        now = now or datetime.now(timezone.utc)
        subs = enabled_subscriptions()
        if not subs or storage.get_alert_destination() is None:
            return {"subscriptions": len(subs), "ran": False}
        tokens = tokens or TokenManager(OAUTH_CONFIG)
        client = client or ESIClient(tokens=tokens)
        poll = SCHEDULER_OPERATOR_CONFIG.alerts_mail_poll_minutes if poll_minutes is None else poll_minutes
        demand = skillqueue_demand(subs)
        if demand:
            try:                                            # only rows shared with char_alerts: never the tenant's other data
                esi_orchestrator.do_sync_due(client=client, granted_tools={TOOL_KEY}, demand=demand, now=now)
            except Exception as e:  # noqa: BLE001 - a failed refresh must not stop mail alerts
                log.warning("Alert skill queue sync failed: %s", e)
        names = _names()
        freshness = {cid: fields.freshness_by_kind(cid) for cid in {s[0] for s in subs}}
        for cid, alert_type, include_content, lead_hours in subs:
            name = names.get(cid, f"#{cid}")
            try:
                if alert_type == logic.SKILLQUEUE_EMPTY:
                    _run_skillqueue(cid, lead_hours, name, now, client, tokens, freshness)
                elif alert_type == logic.MAIL_NEW:
                    _run_mail(cid, include_content, name, now, poll, client, tokens)
            except Exception as e:  # noqa: BLE001 - one character's failure must not skip the others
                log.warning("Alert %s for character %s failed: %s", alert_type, cid, e)
        return {"subscriptions": len(subs), "ran": True}
    finally:
        _end_tenant(tenant_id)
