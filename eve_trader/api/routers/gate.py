"""Access-gate housekeeping endpoints: "am I logged in" and "log out". The
actual login redirect/callback reuses auth.py's existing
/api/auth/gate/start + /api/auth/callback (role_prefix="gate") instead of a
separate route pair here - see auth.py's callback() gate branch - so this
router stays intentionally small, and EVE SSO only needs the one already-
registered redirect_uri (adding a second registered callback URL per app
isn't guaranteed to be supported the same way across CCP dev-portal
versions, not worth the risk when reusing the existing one works fine).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, Response

from ... import access_policy, storage
from ...access_gate import (
    ALL_TOOL_KEYS, SESSION_COOKIE_NAME, authorize_session_cookie, clear_session_cookie,
    read_session_token,
)
from ...config import ACCESS_CONFIG

log = logging.getLogger(__name__)

router = APIRouter()


def _suspended_after_recheck(session) -> bool:
    """Same lazy re-check the middleware uses, run here because /status is
    exempt from the middleware. A stale suspension can clear when the
    character is back on the allowlist; admins and an empty allowlist are
    never suspended."""
    exempt = "admin" in session.tool_keys or not session.allowlist_active
    if exempt:
        if session.access_suspended:
            try:
                access_policy.refresh_registered(session.character_id, session.tool_keys, force=False)
            except Exception:
                log.exception("gate status could not clear a stale suspension flag")
        return False
    if access_policy.recheck_due(
        session.affiliation_checked_at, session.tool_keys, session.allowlist_active,
    ):
        try:
            verdict = access_policy.refresh_registered(session.character_id, session.tool_keys, force=False)
        except Exception:
            log.exception("gate status affiliation re-check failed")
            return True
        return verdict == access_policy.Verdict.SUSPENDED
    return bool(session.access_suspended)


def _status_extras(session) -> dict:
    pending = None
    if session is not None and "admin" in session.tool_keys:
        pending = storage.count_pending_access_requests()
    return {
        "suspended": False if session is None else _suspended_after_recheck(session),
        "pending_access_requests": pending,
    }


@router.get("/status")
def status(session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    """Deliberately public (excluded from AccessGateMiddleware, see
    api/app.py) even when the gate is enabled - the frontend needs to be able
    to ask "am I logged in" without that question itself requiring a login.

    `tools` drives which cards Landing.tsx renders - purely informational,
    NOT the enforcement point (AccessGateMiddleware's own per-request check
    is, see access_gate.authorize_session_cookie). Gate disabled: every tool
    (trusted local operator). Gate enabled: tools come from the same
    registry+grant read as the middleware, so a revoked/reassigned cookie
    reports logged out, not a stale tool list."""
    if not ACCESS_CONFIG.access_gate_enabled:
        data = read_session_token(session_cookie) if session_cookie else None
        return {
            "enabled": False,
            "logged_in": data is not None,
            "character_name": data["character_name"] if data else None,
            "tools": list(ALL_TOOL_KEYS),
            "suspended": False,
            "pending_access_requests": None,
        }
    session = authorize_session_cookie(session_cookie)
    if session is None:
        return {
            "enabled": True, "logged_in": False, "character_name": None, "tools": [],
            "suspended": False, "pending_access_requests": None,
        }
    extras = _status_extras(session)
    return {
        "enabled": True,
        "logged_in": True,
        "character_name": session.character_name,
        "tools": session.tool_keys,
        **extras,
    }


@router.post("/logout")
def logout(response: Response, session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    data = read_session_token(session_cookie) if session_cookie else None
    if data and data.get("character_id") is not None:
        try:
            storage.revoke_sessions_for_character(int(data["character_id"]))
        except Exception:
            log.exception("session revoke on logout failed")
    clear_session_cookie(response)
    return {"ok": True}
