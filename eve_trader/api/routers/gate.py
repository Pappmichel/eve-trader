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

from fastapi import APIRouter, Cookie, Response

from ...access_gate import ALL_TOOL_KEYS, SESSION_COOKIE_NAME, clear_session_cookie, read_session_token, tools_for
from ...config import ACCESS_CONFIG

router = APIRouter()


@router.get("/status")
def status(session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    """Deliberately public (excluded from AccessGateMiddleware, see
    api/app.py) even when the gate is enabled - the frontend needs to be able
    to ask "am I logged in" without that question itself requiring a login.

    `tools` drives which cards Landing.tsx renders - purely informational,
    NOT the enforcement point (AccessGateMiddleware's own per-request check
    is, see access_gate.tools_for's docstring for why both share that one
    function). Gate disabled: every tool (same "no login wall, nothing to
    filter" behavior this app had before tool grants existed at all). Gate
    enabled but not logged in: no tools - nothing to show yet."""
    data = read_session_token(session_cookie) if session_cookie else None
    if not ACCESS_CONFIG.access_gate_enabled:
        tools = list(ALL_TOOL_KEYS)
    elif data is not None and data.get("tenant_id") is not None:
        # A still-valid cookie from before tenant_id existed (see api/app.py's
        # own comment on the same gap) has no tenant to resolve tools from -
        # same "not authenticated enough" treatment, just degrading to an
        # empty tools list here rather than a 401 (this endpoint is public).
        tools = tools_for(data["tenant_id"], data["character_id"])
    else:
        tools = []
    return {
        "enabled": ACCESS_CONFIG.access_gate_enabled,
        "logged_in": data is not None,
        "character_name": data["character_name"] if data else None,
        "tools": tools,
    }


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}
