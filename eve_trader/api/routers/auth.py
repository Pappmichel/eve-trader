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

import ipaddress
import logging
import secrets
import threading
import time
import urllib.parse
from typing import Optional

import requests
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from ... import access_policy, storage
from ...access_gate import set_session_cookie
from ...esi_client import ESIClient, ESIError
from ...auth import TokenManager, _make_pkce_pair
from ...config import OAUTH_CONFIG
from ...esi_data.registry import DEFAULT_CAPABILITIES_FOR_NEW_CHARACTERS
from ...esi_data.selector import delete_strict_subset_tokens, reauth_write_role

router = APIRouter()

log = logging.getLogger(__name__)

# state -> {verifier, role_prefix, scopes, created_at, tenant_id, browser_nonce, client_ip}
# In-memory is enough for a single process. Mutations go through _pending_lock
# so concurrent /start + prune cannot raise "dictionary changed size during
# iteration" or grow without bound (F-04 / F-NEW-01 / P5-04). Fair eviction
# is per-process: multiple uvicorn workers each have their own 256-slot
# store. A shared Redis backend is out of scope for this design.
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()
_PENDING_TTL = 600
_PENDING_MAX = 256
_PENDING_MAX_PER_IP = 8

# Login-CSRF / session-fixation fix (found in a security audit 2026-08-23,
# confirmed real gap): `state` above is only ever used as a server-side dict
# key, never bound to the browser that actually initiated the login - PKCE's
# `verifier` stops someone from stealing a *code* and completing the
# exchange themselves, but it does nothing to stop the reverse: an attacker
# completes their OWN login (their own real EVE character, so `tm._verify`
# below has nothing to object to), then hands the resulting code+state pair
# to a victim's browser (e.g. via a crafted link) to load /callback with.
# The victim would silently end up logged into the *attacker's* tenant - and
# if they then add their own trading/production character while unknowingly
# inside it, that character's live ESI token lands in the attacker's tenant.
# This cookie is the standard fix: a random nonce set on the *initiating*
# browser at /start, echoed back and checked at /callback - an attacker's
# own browser can complete their own login, but can't forge this cookie
# inside a victim's browser, so a handed-off code+state pair now fails the
# nonce check instead of silently authenticating the victim as the attacker.
_OAUTH_NONCE_COOKIE = "eve_trader_oauth_nonce"


def _prune_pending_locked(now: float) -> None:
    """Caller holds _pending_lock. Iterate a snapshot of keys so a concurrent
    reader cannot see a half-mutated dict, and prune is O(n) on the current
    size (capped by _PENDING_MAX), not an unbounded historical list."""
    expired = [s for s, v in list(_pending.items()) if now - v["created_at"] > _PENDING_TTL]
    for s in expired:
        _pending.pop(s, None)


def _evict_from_heaviest_ip_locked() -> Optional[str]:
    """Caller holds _pending_lock. Drop the oldest entry belonging to the IP
    that currently occupies the most slots (tie-break: oldest created_at
    among those IPs). Used only to admit a *new* source when global capacity
    is full — an IP that already holds slots cannot trigger eviction of
    others to grow its own share (P5-04). Returns the evicted state key."""
    if not _pending:
        return None
    counts: dict[str, int] = {}
    for v in _pending.values():
        ip = v.get("client_ip") or "unknown"
        counts[ip] = counts.get(ip, 0) + 1
    max_count = max(counts.values())
    heaviest = {ip for ip, n in counts.items() if n == max_count}
    oldest_state = None
    oldest_at = None
    for s, v in _pending.items():
        ip = v.get("client_ip") or "unknown"
        if ip not in heaviest:
            continue
        created = v["created_at"]
        if oldest_at is None or created < oldest_at:
            oldest_at = created
            oldest_state = s
    if oldest_state is not None:
        _pending.pop(oldest_state, None)
    return oldest_state


def _admit_pending_entry_locked(state: str, entry: dict) -> None:
    """Caller holds _pending_lock. Prune, enforce per-IP and global bounds
    with fair eviction for a new source, then insert. Raises HTTPException
    429 when this source is at its per-IP cap or when this source already
    occupies slots and the global cap is full."""
    _prune_pending_locked(time.time())
    client_ip = entry.get("client_ip") or "unknown"
    ip_count = sum(1 for v in _pending.values() if (v.get("client_ip") or "unknown") == client_ip)
    if ip_count >= _PENDING_MAX_PER_IP:
        raise HTTPException(429, "Too many pending logins from this address.")
    if len(_pending) >= _PENDING_MAX:
        if ip_count > 0:
            raise HTTPException(429, "Too many pending logins. Try again shortly.")
        _evict_from_heaviest_ip_locked()
    _pending[state] = entry


def _prune_pending() -> None:
    with _pending_lock:
        _prune_pending_locked(time.time())


def _client_ip(request: Request) -> str:
    """Rate-limit key for the caller. IPv6 callers are grouped by their /64
    network (one subscriber usually owns a whole /64, so per-address keys
    would give them unlimited slots); IPv4-mapped IPv6 counts as IPv4."""
    if request.client is None or not request.client.host:
        return "unknown"
    try:
        addr = ipaddress.ip_address(request.client.host)
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def begin_oauth(
    request: Request,
    response: Response,
    *,
    role_prefix: str,
    scopes: list[str],
    extra: dict | None = None,
) -> dict:
    """Admit a pending SSO round and return the EVE authorize URL.

    Used by identity-only `/gate/start`, Characters re-auth
    (`role_prefix="reauth"` plus `extra["reauth_character_id"]`) and
    Characters add (`role_prefix="reauth"`, no `reauth_character_id`,
    `scopes=[]` - whoever logs in is the character being added).
    Prefix `/start` was removed in Phase 9.
    """
    if not OAUTH_CONFIG.client_id:
        raise HTTPException(500, "EVE_SSO_CLIENT_ID is not set (.env).")
    verifier, challenge = _make_pkce_pair()
    state = secrets.token_urlsafe(32)
    browser_nonce = secrets.token_urlsafe(32)
    client_ip = _client_ip(request)
    entry = {
        "verifier": verifier, "role_prefix": role_prefix, "scopes": list(scopes),
        "created_at": time.time(),
        # Stashed for /callback (an AccessGateMiddleware-exempt path with no
        # automatic ambient tenant of its own) to pick back up. Characters
        # re-auth is not gate-exempt, so the middleware has already set a
        # real tenant; may be None for role_prefix="gate" (that one *is*
        # exempt, by design - harmless, since the gate branch of /callback
        # resolves its own tenant fresh via the registry).
        "tenant_id": storage.get_current_tenant(),
        "browser_nonce": browser_nonce,
        "client_ip": client_ip,
    }
    if extra:
        entry.update(extra)
    with _pending_lock:
        _admit_pending_entry_locked(state, entry)
    # See _OAUTH_NONCE_COOKIE's own comment above for why this exists - same
    # secure-flag reasoning as access_gate.set_session_cookie (a bare-IP,
    # no-domain-yet deployment is still plain HTTP, so Secure=True there
    # would make the browser silently drop the cookie).
    response.set_cookie(
        _OAUTH_NONCE_COOKIE, browser_nonce, max_age=_PENDING_TTL, httponly=True,
        secure=OAUTH_CONFIG.frontend_origin.startswith("https://"), samesite="lax", path="/api/auth",
    )
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


@router.get("/gate/start")
def start_gate_login(request: Request, response: Response):
    """Identity-only access-gate login. ESI data-access re-auth is
    `/api/characters/reauth/start` (gated on tool_key characters)."""
    return begin_oauth(request, response, role_prefix="gate", scopes=[])


def _gate_redirect(query: str, *, character_id: int | None = None,
                   character_name: str | None = None, tenant_id: str | None = None):
    resp = RedirectResponse(f"{OAUTH_CONFIG.frontend_origin}/?{query}")
    if tenant_id is not None and character_id is not None and character_name is not None:
        set_session_cookie(resp, character_id, character_name, tenant_id)
    resp.delete_cookie(_OAUTH_NONCE_COOKIE, path="/api/auth")
    return resp


def _affiliation_display_names(corporation_id: int, alliance_id: int | None) -> tuple[str | None, str | None]:
    ids = [corporation_id]
    if alliance_id is not None:
        ids.append(alliance_id)
    try:
        names = ESIClient().resolve_names(ids)
    except ESIError:
        log.warning("could not resolve affiliation names for corp %s", corporation_id, exc_info=True)
        return None, None
    return names.get(corporation_id), (names.get(alliance_id) if alliance_id is not None else None)


def _complete_gate_login(character_id: int, character_name: str):
    tenant_id = storage.resolve_tenant_id(character_id)
    if tenant_id is not None:
        # Name refresh does not touch affiliation columns (ON CONFLICT only
        # sets the name). Done even when the verdict later withholds the cookie
        # so the Admin user list stays current.
        storage.add_tenant_registry_entry(tenant_id, character_id, character_name=character_name)
        tool_keys = storage.list_tool_grants_for_character(character_id)
        try:
            verdict = access_policy.refresh_registered(character_id, tool_keys, force=True)
        except Exception:
            log.exception("gate login affiliation check failed")
            return _gate_redirect("gate=error&message=affiliation_unavailable")
        if verdict == access_policy.Verdict.SUSPENDED:
            return _gate_redirect("gate=suspended")
        if verdict == access_policy.Verdict.UNKNOWN:
            return _gate_redirect("gate=error&message=affiliation_unavailable")
        return _gate_redirect(
            f"gate=success&character={urllib.parse.quote(character_name)}",
            character_id=character_id, character_name=character_name, tenant_id=tenant_id,
        )

    existing = storage.get_access_request(character_id)
    if existing is not None and existing["status"] == "rejected":
        return _gate_redirect("gate=rejected")
    affiliation = access_policy.fetch_affiliation(character_id)
    if affiliation is None:
        return _gate_redirect("gate=error&message=affiliation_unavailable")
    corporation_id, alliance_id = affiliation
    if not access_policy.is_allowed(corporation_id, alliance_id):
        return _gate_redirect("gate=denied")
    corporation_name, alliance_name = _affiliation_display_names(corporation_id, alliance_id)
    storage.upsert_pending_access_request(
        character_id, character_name, corporation_id, corporation_name, alliance_id, alliance_name,
    )
    return _gate_redirect("gate=pending")


# Characters SSO rounds (re-authorize and add) return to the Characters
# page, so re-authorizing several characters does not mean navigating back
# from the landing page each time. Gate logins keep returning to "/".
_CHARACTERS_PAGE_PATH = "/character-management/characters"


def _return_url(role_prefix: str | None, query: str) -> str:
    path = _CHARACTERS_PAGE_PATH if role_prefix == "reauth" else "/"
    return f"{OAUTH_CONFIG.frontend_origin}{path}?{query}"


def _log_detail(value) -> str:
    """Untrusted or internal detail for the log only, never for a redirect."""
    return repr(str(value)[:200])


def _error_redirect(code: str, role_prefix: str | None = None):
    """Redirect back to the frontend with one fixed error code. Provider text
    and exception messages never reach the URL."""
    if role_prefix == "reauth":
        url = _return_url(role_prefix, f"auth=error&message={code}")
    else:
        url = f"{OAUTH_CONFIG.frontend_origin}/?auth=error&message={code}"
    resp = RedirectResponse(url)
    resp.delete_cookie(_OAUTH_NONCE_COOKIE, path="/api/auth")
    return resp


@router.get("/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None,
             error_description: str | None = None,
             oauth_nonce: str | None = Cookie(default=None, alias=_OAUTH_NONCE_COOKIE)):
    """This route's full URL must be set as OAUTH_CONFIG.redirect_uri
    (EVE_SSO_CALLBACK_HOST/PORT env vars) and match the EVE dev-portal app's
    registered callback URL exactly."""
    if error or error_description:
        log.warning("SSO callback reported an error: error=%s description=%s",
                    _log_detail(error), _log_detail(error_description))
        return _error_redirect("sso_error")
    if not code or not state:
        return _error_redirect("missing_code_state")

    # The entry is consumed only when the browser nonce matches, so a foreign
    # request carrying a valid `state` cannot burn the user's pending login.
    pending = None
    nonce_ok = False
    with _pending_lock:
        candidate = _pending.get(state)
        if candidate is not None and time.time() - candidate["created_at"] > _PENDING_TTL:
            _pending.pop(state, None)
            candidate = None
        if candidate is not None and oauth_nonce and secrets.compare_digest(
            oauth_nonce, candidate.get("browser_nonce") or ""
        ):
            nonce_ok = True
            _pending.pop(state, None)
        pending = candidate
    if pending is None:
        return _error_redirect("state_expired_or_unknown")

    # See _OAUTH_NONCE_COOKIE's own comment above - `oauth_nonce` must match
    # what /start stashed for this exact `state`, proving this /callback
    # request is arriving in the same browser that initiated the login,
    # not one an attacker handed a completed code+state pair to.
    if not nonce_ok:
        return _error_redirect("nonce_mismatch", pending.get("role_prefix"))

    role_prefix = pending.get("role_prefix")
    if role_prefix not in ("gate", "reauth"):
        log.warning("SSO callback with unknown flow %s", _log_detail(role_prefix))
        return _error_redirect("unknown_flow")

    tm = TokenManager(OAUTH_CONFIG)
    try:
        token_json = tm._exchange_code(code, pending["verifier"])
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("SSO token exchange failed: %s", _log_detail(e))
        return _error_redirect("token_exchange_failed", role_prefix)
    try:
        character_id, character_name = tm._verify(token_json["access_token"])
    except (requests.RequestException, KeyError, ValueError) as e:
        # A network-level failure or a malformed response must still end in a
        # redirect back to the frontend, never a raw 500 mid-SSO.
        log.warning("SSO identity lookup failed: %s", _log_detail(e))
        return _error_redirect("identity_failed", role_prefix)

    if role_prefix == "gate":
        # Identity-only login (see access_gate.py) - never persisted to
        # TokenManager. The session cookie is the credential. An
        # unregistered character from an allowlisted corp/alliance files
        # an access request; everyone else is denied, rejected, or
        # suspended. Registered characters are re-checked (force=True, so
        # the 6h window does not apply at login).
        return _complete_gate_login(character_id, character_name)

    # Both Characters SSO rounds land here: re-authorize (an existing
    # character, `reauth_character_id` set - the returned character must
    # match it) and add (no `reauth_character_id` - whoever logs in is the
    # character being added, identity-only, no scopes). Both write through
    # reauth_write_role, which reuses an existing key for that character
    # and otherwise mints `esi:<id>` (decision 2: no re-keying).
    expected_reauth = pending.get("reauth_character_id")
    is_add = expected_reauth is None
    if not is_add and int(character_id) != int(expected_reauth):
        return _error_redirect("character_mismatch", role_prefix)
    tenant_id = pending.get("tenant_id")
    if not tenant_id:
        # /callback is gate-exempt and has no ambient tenant; /start captured
        # one. Without it nothing may be written, least of all into a
        # fallback tenant.
        log.warning("SSO callback without a captured tenant")
        return _error_redirect("session_lost", role_prefix)
    with storage.tenant_context(tenant_id):
        # The add round carries no scopes, so writing it over a token
        # this character already holds would silently strip every
        # scope they had - reauth_write_role reuses that same key.
        # Adding an already-registered character is therefore a no-op,
        # not a write: the Characters page already lists them, and
        # Re-authorize is the way to change their scopes.
        if is_add and any(
            r.character_id == int(character_id) for r in tm.list_records()
        ):
            resp = RedirectResponse(_return_url(
                role_prefix,
                f"auth=success&added=existing&character={urllib.parse.quote(character_name)}",
            ))
            resp.delete_cookie(_OAUTH_NONCE_COOKIE, path="/api/auth")
            return resp
        final_role = reauth_write_role(character_id)
        record = tm._to_record(
            final_role, token_json, " ".join(pending["scopes"]),
            character_id=character_id, character_name=character_name,
        )
        tm._tokens[final_role] = record
        tm._save_record(final_role)
        delete_strict_subset_tokens(character_id, tokens=tm)
        if is_add:
            for capability_key in DEFAULT_CAPABILITIES_FOR_NEW_CHARACTERS:
                storage.upsert_esi_character_capability(int(character_id), capability_key)
    resp = RedirectResponse(_return_url(
        role_prefix,
        f"auth=success&role={urllib.parse.quote(final_role)}"
        f"&character={urllib.parse.quote(character_name)}",
    ))
    resp.delete_cookie(_OAUTH_NONCE_COOKIE, path="/api/auth")
    return resp


# No more /status route: buyer/seller stopped being a single fixed role each
# (GitHub issue #46), so "logged in y/n" is no longer a meaningful answer -
# the Trading router's own /buyer-characters and /seller-characters (mirrors
# Production's /producer-characters) list every registered character per
# role instead. ESI login is the Characters page, not a prefix /start.
