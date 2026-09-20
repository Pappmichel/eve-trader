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

On by default (AccessConfig.access_gate_enabled=True). A local trusted
operator can still turn it off in config.yaml (filesystem/SSH, never via an
HTTP parameter). When disabled, AccessGateMiddleware sets
storage.DEFAULT_TENANT_ID instead of consulting this module's session
cookie. This module's functions are safe to import and call regardless.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import storage
from .config import OAUTH_CONFIG, OAuthConfig

if TYPE_CHECKING:
    from fastapi import Response

log = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "eve_trader_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days

# Every tool this app has. "admin" is a normal tool grant, issued by the
# Admin UI or `eve-trader admin bootstrap` — not implied by DEFAULT_TENANT_ID.
# "characters" is the ninth grant (docs/ESI_ACCESS_PLAN.md decision 11):
# Admin's checkboxes auto-tick it in the UI only; do_set_tool_grants stays
# replace-not-merge and does not special-case it.
ALL_TOOL_KEYS = ("trading", "production", "doctrine", "refining", "station_trading", "sorting", "portfolio", "admin", "characters")


@dataclass(frozen=True)
class AuthorizedSession:
    """A cookie that is signed, unexpired, still bound to its tenant, and
    not revoked via sessions_valid_after."""
    character_id: int
    character_name: str
    tenant_id: str
    tool_keys: list[str]


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
    - all three are just "not logged in", not distinguished further).

    Does not consult the registry — use authorize_session_cookie on the
    request path so tenant membership and sessions_valid_after are checked.
    """
    try:
        return _serializer(cfg).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def _cookie_issued_at(token: str, cfg: OAuthConfig = OAUTH_CONFIG) -> Optional[tuple[dict, datetime]]:
    """itsdangerous 2.2 URLSafeTimedSerializer.loads(..., return_timestamp=True)
    returns (payload, aware-UTC datetime) without changing the cookie format."""
    try:
        payload, issued_at = _serializer(cfg).loads(
            token, max_age=SESSION_MAX_AGE_SECONDS, return_timestamp=True,
        )
    except (BadSignature, SignatureExpired):
        return None
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    return payload, issued_at


def authorize_session_cookie(token: Optional[str], cfg: OAuthConfig = OAUTH_CONFIG) -> Optional[AuthorizedSession]:
    """F-01 + F-03: cookie is valid only if the character still belongs to
    the cookie's tenant, the signature/expiry check passes, and the
    itsdangerous issue timestamp is not before sessions_valid_after.

    Returns None (unauthenticated) on any failure, including a DB error —
    fail closed. An empty tool_keys list is a successful auth with no
    grants (callers 403 on the required tool), distinct from None.
    """
    if not token:
        return None
    parsed = _cookie_issued_at(token, cfg)
    if parsed is None:
        return None
    payload, issued_at = parsed
    tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
    character_id = payload.get("character_id") if isinstance(payload, dict) else None
    character_name = payload.get("character_name", "") if isinstance(payload, dict) else ""
    if tenant_id is None or character_id is None:
        return None
    try:
        authz = storage.session_authorization(int(character_id), str(tenant_id))
    except Exception:
        log.exception("session authorization lookup failed; refusing the request")
        return None
    if authz is None:
        return None
    tool_keys, sessions_valid_after = authz
    if sessions_valid_after is not None:
        sva = sessions_valid_after
        if getattr(sva, "tzinfo", None) is None:
            sva = sva.replace(tzinfo=timezone.utc)
        # itsdangerous 2.2 records whole seconds. Comparing that to a
        # microsecond TIMESTAMPTZ would reject a legitimate new cookie
        # issued in the same second as revocation (logout then re-login).
        # Cookies whose whole-second timestamp is strictly before
        # sessions_valid_after are revoked; same-second is the signer’s
        # resolution limit.
        if issued_at < sva.replace(microsecond=0):
            return None
    return AuthorizedSession(
        character_id=int(character_id),
        character_name=str(character_name),
        tenant_id=str(tenant_id),
        tool_keys=list(tool_keys),
    )


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


def tools_for(tenant_id: str, character_id: int) -> Optional[list[str]]:
    """Every tool_key `character_id` currently has for `tenant_id`, from the
    same single DB read the middleware uses. Returns None when the character
    is not registered to this tenant (distinct from [] = registered, no
    grants). DB errors propagate — authorize_session_cookie catches them
    and fails closed; callers that want the same must catch too."""
    authz = storage.session_authorization(character_id, tenant_id)
    if authz is None:
        return None
    return authz[0]
