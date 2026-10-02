"""Discord alert `do_*` actions (CLAUDE.md "Discord alerts").

UI-agnostic, no FastAPI imports. Every alert is opt-in per character x type,
default off, and needs BOTH the opt-in and an `esi_sharing` row for the
`char_alerts` tool (fail-closed through `is_shared`). `deliver` re-checks all
of it at send time so a revoke takes effect immediately, even mid-run - it is
the one send path the scheduler job will use.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_data import shared_owner_ids
from ..character_management import fields
from . import discord_client, logic
from .config import load_config

log = logging.getLogger(__name__)

TOOL_KEY = "char_alerts"
LINK_STATE_MAX_AGE_SECONDS = 600
# alert type -> the ESI data kind that must be shared with `char_alerts`
KIND_BY_ALERT = {logic.SKILLQUEUE_EMPTY: "skillqueue", logic.MAIL_NEW: "mail"}


def _serializer() -> URLSafeTimedSerializer:
    if not OAUTH_CONFIG.session_secret_key:
        raise ActionError("SESSION_SECRET_KEY is not set on this server; Discord linking is unavailable.")
    return URLSafeTimedSerializer(OAUTH_CONFIG.session_secret_key, salt="eve-trader-discord-link")


def _tenant() -> str:
    tenant = storage.get_current_tenant()
    if not tenant:
        raise ActionError("No tenant context.")
    return str(tenant)


def _int(value: Any, what: str = "character_id") -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid {what} {value!r}") from e
    if number <= 0:
        raise ActionError(f"Invalid {what} {number}.")
    return number


def _alert_type(value: Any) -> str:
    if value not in logic.ALERT_TYPES:
        raise ActionError(f"Unknown alert type {value!r}.")
    return value


# ------------------------------------------------------------------ settings
def do_get_alert_settings() -> dict:
    cfg = load_config()
    shared = {a: set(shared_owner_ids(kind, TOOL_KEY, "character")) for a, kind in KIND_BY_ALERT.items()}
    subs = {(cid, t): (en, inc, lead) for cid, t, en, inc, lead in storage.list_alert_subscriptions()}
    characters = []
    for c in fields.token_characters():
        cid = c["character_id"]
        alerts = {}
        for alert in logic.ALERT_TYPES:
            enabled, include, lead = subs.get((cid, alert), (False, False, logic.DEFAULT_LEAD_HOURS))
            alerts[alert] = {
                "shared": cid in shared[alert], "enabled": bool(enabled),
                "include_content": bool(include), "lead_hours": int(lead),
            }
        characters.append({**c, "alerts": alerts})
    return {
        "bot_configured": cfg.can_send,
        "link_configured": cfg.can_link,
        "linked": storage.get_alert_destination() is not None,
        "characters": characters,
    }


def do_set_subscription(
    character_id: int, alert_type: str, enabled: bool, include_content: bool = False,
    lead_hours: Optional[int] = None,
) -> dict:
    character_id = _int(character_id)
    alert_type = _alert_type(alert_type)
    if include_content and alert_type != logic.MAIL_NEW:
        raise ActionError("Only mail alerts have a content option.")
    previous = storage.get_alert_subscription(character_id, alert_type)
    if lead_hours is None:
        lead_hours = int(previous[2]) if previous else logic.DEFAULT_LEAD_HOURS
    lead_hours = _int(lead_hours, "lead_hours")
    if not logic.MIN_LEAD_HOURS <= lead_hours <= logic.MAX_LEAD_HOURS:
        raise ActionError(f"lead_hours must be {logic.MIN_LEAD_HOURS}-{logic.MAX_LEAD_HOURS}.")
    if enabled:
        if storage.get_alert_destination() is None:
            raise ActionError("Link your Discord account first.")
        if character_id not in shared_owner_ids(KIND_BY_ALERT[alert_type], TOOL_KEY, "character"):
            raise ActionError(
                f"Share {KIND_BY_ALERT[alert_type]} of this character with Discord Alerts on the Characters page first."
            )
    was_enabled = bool(previous and previous[0])
    storage.upsert_alert_subscription(character_id, alert_type, bool(enabled), bool(include_content), lead_hours)
    if enabled and not was_enabled:
        # Fresh opt-in starts from a clean baseline: no stale dedupe key, and
        # no announcement of mail that predates the opt-in.
        storage.reset_alert_state(character_id, alert_type)
    return {"character_id": character_id, "alert_type": alert_type, "enabled": bool(enabled),
            "include_content": bool(include_content), "lead_hours": lead_hours}


# ------------------------------------------------------------- account link
def do_start_discord_link() -> dict:
    if not load_config().can_link:
        raise ActionError("Discord linking is not configured on this server.")
    state = _serializer().dumps({"tenant_id": _tenant(), "nonce": secrets.token_hex(8)})
    return {"url": discord_client.authorize_url(state)}


def do_finish_discord_link(code: str, state: str) -> dict:
    """Callback half of the link. `state` must have been issued by
    `do_start_discord_link` for THIS tenant, so a link can not be planted into
    another tenant's session."""
    try:
        payload = _serializer().loads(state, max_age=LINK_STATE_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as e:
        raise ActionError("The Discord link request expired or is invalid. Start again.") from e
    if payload.get("tenant_id") != _tenant():
        raise ActionError("The Discord link request belongs to a different session. Start again.")
    if not code:
        raise ActionError("Discord did not return an authorization code.")
    try:
        user_id = discord_client.exchange_code_for_user_id(code)
    except discord_client.DiscordError as e:
        raise ActionError(str(e)) from e
    storage.set_alert_destination(user_id)
    return {"linked": True}


def do_unlink_discord() -> dict:
    """Removes the destination and switches every subscription off."""
    return {"unlinked": storage.delete_alert_destination()}


def do_send_test_message() -> dict:
    user_id = storage.get_alert_destination()
    if user_id is None:
        raise ActionError("Link your Discord account first.")
    try:
        discord_client.send_dm(user_id, "EVE Trader: test message. Discord alerts are set up.")
    except discord_client.DiscordError as e:
        raise ActionError(str(e)) from e
    return {"sent": True}


# ---------------------------------------------------------------- delivery
def deliver(character_id: int, alert_type: str, message: str, tokens: Optional[TokenManager] = None) -> bool:
    """Send one alert DM, re-checking at send time that the opt-in is still on,
    the destination exists, the kind is still shared with `char_alerts` and a
    token still holds its scope. False = deliberately not sent (revoked);
    raises `DiscordError` on a delivery failure so the caller can keep its
    dedupe cursor where it is and back off."""
    alert_type = _alert_type(alert_type)
    sub = storage.get_alert_subscription(character_id, alert_type)
    if not sub or not sub[0]:
        return False
    user_id = storage.get_alert_destination()
    if user_id is None:
        return False
    if fields.gate(KIND_BY_ALERT[alert_type], TOOL_KEY, character_id, tokens or TokenManager(OAUTH_CONFIG)):
        return False
    discord_client.send_dm(user_id, message)
    return True
