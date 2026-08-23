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
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ... import storage
from ...access_gate import set_session_cookie
from ...auth import TokenManager, _make_pkce_pair
from ...config import OAUTH_CONFIG
from ...doctrine import esi_sync as doctrine_esi_sync
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
    if role_prefix == "doctrine":
        return doctrine_esi_sync.DOCTRINE_SCOPES
    if role_prefix == "doctrine-assets":
        return doctrine_esi_sync.DOCTRINE_ASSET_SCOPES
    if role_prefix == "gate":
        return []  # identity only - see access_gate.py
    return list(OAUTH_CONFIG.scopes)


# GitHub issue #57 (found in a full-codebase audit 2026-08-21): the tool_key
# a /api/auth/{role_prefix}/start login requires - both the allowlist of
# valid role_prefix values (start_login below rejects anything else, rather
# than letting an arbitrary string become a permanent TokenManager role key)
# and what api/app.py's AccessGateMiddleware checks a session's tool grants
# against for this path, since /api/auth/ isn't covered by
# _TOOL_PATH_PREFIXES's plain prefix match. "gate" maps to None - it's
# identity-only and already fully exempt from the gate check via
# api/app.py's _GATE_EXEMPT_PATHS, never reaching this mapping at all in
# practice, but listed here so it's still a recognized/allowed role_prefix.
ROLE_PREFIX_TOOL: dict[str, Optional[str]] = {
    "buyer": "trading",
    "seller": "trading",
    "producer": "production",
    "doctrine": "doctrine",
    "doctrine-assets": "doctrine",
    "gate": None,
}


@router.get("/{role_prefix}/consent")
def get_consent_status(role_prefix: str):
    """Whether the current tenant has already acknowledged what data this
    role_prefix's login reads - the frontend's confirm-before-redirect modal
    (useRoleCharacters.ts) checks this before showing itself, so a tenant
    only sees the confirmation once per role. "gate" is deliberately
    excluded - before a first gate login there's no tenant to check this
    against at all (see role_consent_schema.sql's own comment); the
    Landing page uses localStorage for that one role instead."""
    if role_prefix not in ROLE_PREFIX_TOOL:
        raise HTTPException(400, f"Unknown role_prefix '{role_prefix}'.")
    if role_prefix == "gate":
        raise HTTPException(400, "gate consent is tracked client-side, not via this endpoint.")
    return {"acknowledged": storage.has_role_consent(role_prefix)}


@router.post("/{role_prefix}/consent")
def acknowledge_consent(role_prefix: str):
    """Records that the current tenant has seen and confirmed the
    data-access description for role_prefix - called right before the
    frontend proceeds to /start for the first time. See get_consent_status
    above for why "gate" isn't accepted here."""
    if role_prefix not in ROLE_PREFIX_TOOL:
        raise HTTPException(400, f"Unknown role_prefix '{role_prefix}'.")
    if role_prefix == "gate":
        raise HTTPException(400, "gate consent is tracked client-side, not via this endpoint.")
    storage.record_role_consent(role_prefix)
    return {"acknowledged": True}


@router.get("/{role_prefix}/start")
def start_login(role_prefix: str):
    """role_prefix: "buyer" | "seller" | "producer" | ... - every one of
    these is multi-character (GitHub issue #46: buyer/seller used to be a
    single fixed role each, now they follow the same "producer" scheme) -
    the final role is resolved after login as f"{role_prefix}:<char_id>"."""
    if role_prefix not in ROLE_PREFIX_TOOL:
        raise HTTPException(400, f"Unknown role_prefix '{role_prefix}'.")
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
        # token. Character-only (corp/alliance registry entries retired -
        # see docs/admin_schema.sql), so no corp/alliance ESI lookup needed
        # here anymore.
        tenant_id = storage.resolve_tenant_id(character_id)
        if tenant_id is None:
            return RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?gate=denied")
        # Refresh the cached character_name (tenant_registry_entries' own
        # column, see docs/admin_schema.sql) with the name EVE SSO just
        # verified - keeps the Admin UI's user list current if a character
        # is renamed, at zero extra cost (character_name is already known
        # here, no additional ESI call).
        storage.add_tenant_registry_entry(tenant_id, character_id, character_name=character_name)
        # Informational only, not yet gating anything - the frontend's own
        # gate confirmation uses localStorage (see role_consent_schema.sql's
        # comment on why: there's no tenant to attach a server-side record
        # to *before* this login resolves one). Recorded here now that a
        # real tenant_id exists, in case a future need for it shows up.
        with storage.tenant_context(tenant_id):
            storage.record_role_consent("gate")
        resp = RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?gate=success&character={urllib.parse.quote(character_name)}")
        set_session_cookie(resp, character_id, character_name, tenant_id)
        return resp

    # GitHub issue #46: buyer/seller used to be stored under a single fixed
    # role key (a second login for the same role silently overwrote the
    # first) - now every role_prefix (including buyer/seller) resolves to
    # f"{role_prefix}:{character_id}", same multi-character scheme "producer"
    # already used, so multiple buyer/seller characters can be registered
    # independently.
    final_role = f"{role_prefix}:{character_id}"
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


# No more /status route: buyer/seller stopped being a single fixed role each
# (GitHub issue #46), so "logged in y/n" is no longer a meaningful answer -
# the Trading router's own /buyer-characters and /seller-characters (mirrors
# Production's /producer-characters) list every registered character per
# role instead. See TradingLayout.tsx's LoginButton for the frontend side.
