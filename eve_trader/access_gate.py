"""The access gate: a scope-less EVE SSO login that must resolve to a
provisioned tenant (storage.resolve_tenant_id, backed by the
tenant_registry_entries table - see docs/phase3_schema.sql) before any
other API route is reachable - see api/routers/gate.py for the actual
login/callback routes and api/app.py's AccessGateMiddleware for where this
is enforced.

Deliberately separate from auth.py's TokenManager, which handles the
*data-access* buyer/seller/producer logins (real ESI scopes, tokens persisted
to disk, refreshed on expiry). This is purely an identity check: the session
this module issues is a signed, self-contained cookie (character_id/name/
tenant_id + issue time), never written to disk, and never used to call any
ESI endpoint on the visitor's behalf.

Off by default (AccessConfig.access_gate_enabled=False) - see that field's
own docstring for why. When disabled, AccessGateMiddleware sets
storage.DEFAULT_TENANT_ID instead of ever consulting this module's session
cookie at all - this module's functions are safe to import and call
regardless, only the middleware decides whether they're consulted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import storage
from .config import OAUTH_CONFIG, OAuthConfig

if TYPE_CHECKING:
    from fastapi import Response

SESSION_COOKIE_NAME = "eve_trader_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days

# Every tool this app has - the Admin tool is one more entry here, not a
# special case elsewhere (see tools_for's own docstring for why
# DEFAULT_TENANT_ID's users get all of these, including "admin", without an
# explicit tool_grants row).
ALL_TOOL_KEYS = ("trading", "production", "doctrine", "portfolio", "admin")


def _serializer(cfg: OAuthConfig) -> URLSafeTimedSerializer:
    if not cfg.session_secret_key:
        raise RuntimeError(
            "SESSION_SECRET_KEY is not set (.env) - required once AccessConfig."
            "access_gate_enabled is true. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return URLSafeTimedSerializer(cfg.session_secret_key, salt="eve-trader-access-gate")


def create_session_token(character_id: int, character_name: str, tenant_id: str,
                          cfg: OAuthConfig = OAUTH_CONFIG) -> str:
    return _serializer(cfg).dumps(
        {"character_id": character_id, "character_name": character_name, "tenant_id": tenant_id}
    )


def read_session_token(token: str, cfg: OAuthConfig = OAUTH_CONFIG) -> Optional[dict]:
    """Returns {"character_id", "character_name", "tenant_id"} if `token` is
    a valid, unexpired session, else None (covers a tampered cookie, one
    signed with a since-rotated SESSION_SECRET_KEY, and a plain expired one
    - all three are just "not logged in", not distinguished further)."""
    try:
        return _serializer(cfg).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def set_session_cookie(response: "Response", character_id: int, character_name: str, tenant_id: str,
                        cfg: OAuthConfig = OAUTH_CONFIG) -> None:
    """Secure=True (cookie only sent over HTTPS) once frontend_origin is
    actually an https:// URL - NOT inferred from callback_host being
    non-localhost (an earlier version did that, confirmed wrong 2026-08-16:
    a real deployment on a bare IP with no domain yet - no Let's Encrypt cert
    possible without one - is non-localhost but still plain HTTP; Secure=True
    there would make the browser silently refuse to ever send the cookie
    back, breaking login with no visible error). frontend_origin already has
    to be set correctly per environment anyway (see its own docstring), so
    its scheme is the one honest signal for whether HTTPS is actually in use
    right now, independent of what the domain/IP happens to be."""
    token = create_session_token(character_id, character_name, tenant_id, cfg)
    secure = cfg.frontend_origin.startswith("https://")
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True,
        secure=secure, samesite="lax", path="/",
    )


def clear_session_cookie(response: "Response") -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def tools_for(tenant_id: str, character_id: int) -> list[str]:
    """Every tool_key `character_id` (registered to `tenant_id`) currently
    has access to. storage.DEFAULT_TENANT_ID's own users always get every
    tool (including "admin") without an explicit tool_grants row - the
    Admin tool is a deliberate cross-tenant superadmin surface, only ever
    reachable from the one tenant that represents this app's own operator
    (see docs/admin_schema.sql's own comment on the tool_grants table).

    One chokepoint, used by both AccessGateMiddleware's per-request
    enforcement (api/app.py) and /api/gate/status's `tools` field
    (api/routers/gate.py) - so the two can never disagree about what a
    given character can see vs. what they're actually allowed to call."""
    if tenant_id == storage.DEFAULT_TENANT_ID:
        return list(ALL_TOOL_KEYS)
    return storage.list_tool_grants_for_character(character_id)
