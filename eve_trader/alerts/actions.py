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

from .. import storage, tenant_eligibility
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
KIND_BY_ALERT = {
    logic.SKILLQUEUE_EMPTY: "skillqueue", logic.MAIL_NEW: "mail",
    **{t: "planets" for t in logic.PI_ALERT_TYPES},
    logic.CONTRACT_STATUS: "contracts",
}


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


def _doctrine_granted() -> bool:
    return tenant_eligibility.may_use("doctrine", tenant_eligibility.granted_tools(_tenant()))


def _filters_dict(row: Optional[tuple]) -> dict:
    f = logic.ContractFilters()
    if row is not None:
        interval, finished, expired, doctrine_only, sold_out_only = row
        f = logic.ContractFilters(int(interval) if interval else None, bool(finished), bool(expired),
                                  bool(doctrine_only), bool(sold_out_only))
    return {"min_interval_minutes": f.min_interval_minutes, "notify_finished": f.notify_finished,
            "notify_expired": f.notify_expired, "doctrine_only": f.doctrine_only,
            "sold_out_only": f.sold_out_only}


def _contract_filters(value: Any) -> logic.ContractFilters:
    """Validated filters from a request dict (GH #382)."""
    if not isinstance(value, dict):
        raise ActionError("contract_filters must be an object.")
    interval = value.get("min_interval_minutes")
    if interval in ("", 0):
        interval = None
    f = logic.ContractFilters(
        min_interval_minutes=None if interval is None else _int(interval, "min_interval_minutes"),
        notify_finished=bool(value.get("notify_finished", True)),
        notify_expired=bool(value.get("notify_expired", True)),
        doctrine_only=bool(value.get("doctrine_only", False)),
        sold_out_only=bool(value.get("sold_out_only", False)),
    )
    error = logic.contract_filter_error(f)
    if error:
        raise ActionError(error)
    if f.needs_doctrine and not _doctrine_granted():
        raise ActionError("The Doctrine filters need access to the Doctrine tool.")
    return f


# ------------------------------------------------------------------ settings
def do_get_alert_settings() -> dict:
    cfg = load_config()
    shared = {a: set(shared_owner_ids(kind, TOOL_KEY, "character")) for a, kind in KIND_BY_ALERT.items()}
    subs = {(cid, t): (en, inc, lead) for cid, t, en, inc, lead in storage.list_alert_subscriptions()}
    contract_filters = storage.list_contract_alert_filters()
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
        alerts[logic.CONTRACT_STATUS]["contract_filters"] = _filters_dict(contract_filters.get(cid))
        characters.append({**c, "alerts": alerts})
    return {
        "bot_configured": cfg.can_send,
        "link_configured": cfg.can_link,
        "linked": storage.get_alert_destination() is not None,
        "doctrine_granted": _doctrine_granted(),
        "characters": characters,
    }


def do_set_subscription(
    character_id: int, alert_type: str, enabled: bool, include_content: bool = False,
    lead_hours: Optional[int] = None, contract_filters: Optional[dict] = None,
) -> dict:
    """`contract_filters` (contract alerts only) replaces the stored filters;
    None keeps them."""
    character_id = _int(character_id)
    alert_type = _alert_type(alert_type)
    if include_content and alert_type != logic.MAIL_NEW:
        raise ActionError("Only mail alerts have a content option.")
    if contract_filters is not None and alert_type != logic.CONTRACT_STATUS:
        raise ActionError("Only contract alerts have filters.")
    filters = _contract_filters(contract_filters) if contract_filters is not None else None
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
    if filters is not None:
        storage.set_contract_alert_filters(
            character_id, filters.min_interval_minutes, filters.notify_finished, filters.notify_expired,
            filters.doctrine_only, filters.sold_out_only)
    if enabled and not was_enabled:
        # Fresh opt-in starts from a clean baseline: no stale dedupe key, and
        # no announcement of mail that predates the opt-in.
        storage.reset_alert_state(character_id, alert_type)
        if alert_type in logic.PI_ALERT_TYPES:
            storage.reset_pi_alert_state(character_id, alert_type)
        if alert_type == logic.CONTRACT_STATUS:
            storage.reset_contract_alert_state(character_id)
    out = {"character_id": character_id, "alert_type": alert_type, "enabled": bool(enabled),
           "include_content": bool(include_content), "lead_hours": lead_hours}
    if alert_type == logic.CONTRACT_STATUS:
        out["contract_filters"] = _filters_dict(storage.get_contract_alert_filters(character_id))
    return out


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
