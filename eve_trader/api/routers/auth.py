"""EVE SSO login as a proper web redirect flow, replacing TokenManager's old
blocking local-HTTP-server browser flow (auth.py's get_token_interactive*):
that approach worked when a single Python process could `webbrowser.open()`
itself and block waiting for the callback - now the frontend *is* the
browser, so it navigates to the authorize URL directly, and this router's
own /callback route (which must be OAUTH_CONFIG.redirect_uri) receives the
code. TokenManager's token storage/refresh (_to_record, _save, get_token) is
reused unchanged - only how the interactive flow is *triggered* changes.
"""
from __future__ import annotations

import time
import urllib.parse

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ...auth import TokenManager, _make_pkce_pair
from ...config import OAUTH_CONFIG
from ...production import esi_sync

router = APIRouter()

FRONTEND_ORIGIN = "http://localhost:5173"

# state -> {verifier, role_prefix, scopes, multi}, pruned after use/on expiry.
# Single local user/process - an in-memory dict is sufficient (same lifetime
# assumption as the old approach's temporary HTTP server).
_pending: dict[str, dict] = {}
_PENDING_TTL = 600


def _prune_pending() -> None:
    now = time.time()
    expired = [s for s, v in _pending.items() if now - v["created_at"] > _PENDING_TTL]
    for s in expired:
        _pending.pop(s, None)


def _scopes_for(role_prefix: str) -> list[str]:
    if role_prefix == "producer":
        return esi_sync.PRODUCTION_SCOPES
    return list(OAUTH_CONFIG.scopes)


@router.get("/{role_prefix}/start")
def start_login(role_prefix: str):
    """role_prefix: "buyer" | "seller" (single, fixed role) or "producer"
    (multi-character - final role resolved after login as "producer:<id>")."""
    if not OAUTH_CONFIG.client_id:
        raise HTTPException(500, "EVE_SSO_CLIENT_ID is not set (.env).")
    _prune_pending()
    verifier, challenge = _make_pkce_pair()
    state = urllib.parse.quote(f"{role_prefix}-{time.time_ns()}")
    scopes = _scopes_for(role_prefix)
    _pending[state] = {
        "verifier": verifier, "role_prefix": role_prefix, "scopes": scopes, "created_at": time.time(),
    }
    params = {
        "response_type": "code",
        "redirect_uri": OAUTH_CONFIG.redirect_uri,
        "client_id": OAUTH_CONFIG.client_id,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"url": f"{OAUTH_CONFIG.authorize_url}?{urllib.parse.urlencode(params)}"}


@router.get("/callback")
def callback(code: str | None = None, state: str | None = None, error_description: str | None = None):
    """This route's full URL must be set as OAUTH_CONFIG.redirect_uri
    (EVE_SSO_CALLBACK_HOST/PORT env vars) and match the EVE dev-portal app's
    registered callback URL exactly."""
    if error_description or not code or not state:
        return RedirectResponse(f"{FRONTEND_ORIGIN}/?auth=error&message={urllib.parse.quote(error_description or 'missing code/state')}")

    pending = _pending.pop(state, None)
    if pending is None:
        return RedirectResponse(f"{FRONTEND_ORIGIN}/?auth=error&message=state_expired_or_unknown")

    tm = TokenManager(OAUTH_CONFIG)
    try:
        token_json = tm._exchange_code(code, pending["verifier"])
        character_id, character_name = tm._verify(token_json["access_token"])
    except (requests.RequestException, KeyError, ValueError) as e:
        # Confirmed real gap: this used to only catch requests.HTTPError
        # (raise_for_status' 4xx/5xx case) - a network-level failure
        # (ConnectionError/Timeout - siblings of HTTPError under
        # RequestException, not subclasses of it) or a malformed response
        # (KeyError on token_json["access_token"]/data["CharacterID"], or a
        # ValueError from a non-JSON body) escaped this try entirely,
        # defeating the whole point of this route (always redirect back to
        # the frontend, even on failure) with a raw FastAPI 500 in the
        # middle of the SSO redirect instead.
        return RedirectResponse(f"{FRONTEND_ORIGIN}/?auth=error&message={urllib.parse.quote(str(e))}")

    role_prefix = pending["role_prefix"]
    final_role = role_prefix if role_prefix in ("buyer", "seller") else f"{role_prefix}:{character_id}"
    record = tm._to_record(final_role, token_json, " ".join(pending["scopes"]),
                            character_id=character_id, character_name=character_name)
    tm._tokens[final_role] = record
    tm._save()

    return RedirectResponse(f"{FRONTEND_ORIGIN}/?auth=success&role={urllib.parse.quote(final_role)}"
                             f"&character={urllib.parse.quote(character_name)}")


@router.get("/status")
def auth_status():
    tm = TokenManager(OAUTH_CONFIG)
    status = {}
    for role in ("buyer", "seller"):
        if tm.has_token(role):
            try:
                rec = tm.get_token(role)
                status[role] = f"{rec.character_name} ({rec.character_id})"
            except Exception as e:  # noqa: BLE001
                status[role] = f"Token error: {e}"
        else:
            status[role] = None
    return status
