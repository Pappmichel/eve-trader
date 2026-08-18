"""EVE SSO login as a proper web redirect flow, replacing TokenManager's old
blocking local-HTTP-server browser flow (auth.py's get_token_interactive*):
that approach worked when a single Python process could `webbrowser.open()`
itself and block waiting for the callback - now the frontend *is* the
browser, so it navigates to the authorize URL directly, and this router's
own /callback route (which must be OAUTH_CONFIG.redirect_uri) receives the
code. TokenManager's token storage/refresh (_to_record, _save, get_token) is
reused unchanged - only how the interactive flow is *triggered* changes.

Also handles role_prefix="gate" - the access-gate identity-only login (see
access_gate.py, api/routers/gate.py) - through this same /callback route
rather than a separate one, so EVE SSO only needs the one already-registered
redirect_uri; callback() branches on role_prefix before reaching the normal
TokenManager persistence, since a gate login is never stored there.
"""
from __future__ import annotations

import time
import urllib.parse

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ... import storage
from ...access_gate import resolve_corp_alliance, set_session_cookie
from ...auth import TokenManager, _make_pkce_pair
from ...config import OAUTH_CONFIG
from ...production import esi_sync

router = APIRouter()

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
    if role_prefix == "gate":
        return []  # identity only - see access_gate.py
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
        # Stashed for /callback (an AccessGateMiddleware-exempt path with no
        # automatic ambient tenant of its own) to pick back up - guaranteed
        # non-None here for buyer/seller/producer (their /start routes are
        # NOT gate-exempt, so the middleware has already set a real tenant
        # by the time this handler runs); may be None for role_prefix="gate"
        # (that one *is* exempt, by design - harmless, since the gate branch
        # of /callback resolves its own tenant fresh via the registry).
        "tenant_id": storage.get_current_tenant(),
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
        return RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?auth=error&message={urllib.parse.quote(error_description or 'missing code/state')}")

    pending = _pending.pop(state, None)
    if pending is None:
        return RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?auth=error&message=state_expired_or_unknown")

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
        return RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?auth=error&message={urllib.parse.quote(str(e))}")

    role_prefix = pending["role_prefix"]

    if role_prefix == "gate":
        # Identity-only login (see access_gate.py) - never persisted to
        # TokenManager/tokens.json, unlike every other role below: the
        # resulting session cookie IS the whole credential, re-verified via
        # EVE SSO on every future login rather than refreshed from a stored
        # token.
        try:
            corporation_id, alliance_id = resolve_corp_alliance(character_id)
        except (requests.RequestException, KeyError, ValueError):
            # A character-level registry entry should still work even if
            # this particular corp/alliance lookup has a transient hiccup -
            # only the alliance/corp-level check degrades, not the whole login.
            corporation_id, alliance_id = None, None
        tenant_id = storage.resolve_tenant_id(character_id, corporation_id, alliance_id)
        if tenant_id is None:
            return RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?gate=denied")
        resp = RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?gate=success&character={urllib.parse.quote(character_name)}")
        set_session_cookie(resp, character_id, character_name, tenant_id)
        return resp

    final_role = role_prefix if role_prefix in ("buyer", "seller") else f"{role_prefix}:{character_id}"
    # /callback is AccessGateMiddleware-exempt, so no ambient tenant is set
    # automatically here - use the one /start captured before redirecting to
    # EVE SSO (falling back to DEFAULT_TENANT_ID for a hand-constructed
    # _pending entry with no tenant_id key, e.g. in tests).
    with storage.tenant_context(pending.get("tenant_id") or storage.DEFAULT_TENANT_ID):
        record = tm._to_record(final_role, token_json, " ".join(pending["scopes"]),
                                character_id=character_id, character_name=character_name)
        tm._tokens[final_role] = record
        tm._save_record(final_role)

    return RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?auth=success&role={urllib.parse.quote(final_role)}"
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
