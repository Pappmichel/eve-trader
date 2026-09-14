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

from ... import storage
from ...access_gate import (
    ALL_TOOL_KEYS, SESSION_COOKIE_NAME, authorize_session_cookie, clear_session_cookie,
    read_session_token,
)
from ...config import ACCESS_CONFIG

log = logging.getLogger(__name__)

router = APIRouter()


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
        }
    session = authorize_session_cookie(session_cookie)
    if session is None:
        return {
            "enabled": True, "logged_in": False, "character_name": None, "tools": [],
        }
    return {
        "enabled": True,
        "logged_in": True,
        "character_name": session.character_name,
        "tools": session.tool_keys,
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
