"""The access gate: a scope-less EVE SSO login that must resolve to an
allowed character/corp/alliance (config.AccessConfig) before any other API
route is reachable - see api/routers/gate.py for the actual login/callback
routes and api/app.py's AccessGateMiddleware for where this is enforced.

Deliberately separate from auth.py's TokenManager, which handles the
*data-access* buyer/seller/producer logins (real ESI scopes, tokens persisted
to disk, refreshed on expiry). This is purely an identity check: the session
this module issues is a signed, self-contained cookie (character_id/name +
issue time), never written to disk, and never used to call any ESI endpoint
on the visitor's behalf.

Off by default (AccessConfig.access_gate_enabled=False) - see that field's
own docstring for why. This module's functions are safe to import and call
even while disabled; only the middleware decides whether they're consulted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import ACCESS_CONFIG, OAUTH_CONFIG, AccessConfig, OAuthConfig

if TYPE_CHECKING:
    from fastapi import Response

SESSION_COOKIE_NAME = "eve_trader_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days


def _serializer(cfg: OAuthConfig) -> URLSafeTimedSerializer:
    if not cfg.session_secret_key:
        raise RuntimeError(
            "SESSION_SECRET_KEY is not set (.env) - required once AccessConfig."
            "access_gate_enabled is true. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return URLSafeTimedSerializer(cfg.session_secret_key, salt="eve-trader-access-gate")


def create_session_token(character_id: int, character_name: str, cfg: OAuthConfig = OAUTH_CONFIG) -> str:
    return _serializer(cfg).dumps({"character_id": character_id, "character_name": character_name})


def read_session_token(token: str, cfg: OAuthConfig = OAUTH_CONFIG) -> Optional[dict]:
    """Returns {"character_id", "character_name"} if `token` is a valid,
    unexpired session, else None (covers a tampered cookie, one signed with a
    since-rotated SESSION_SECRET_KEY, and a plain expired one - all three are
    just "not logged in", not distinguished further)."""
    try:
        return _serializer(cfg).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def set_session_cookie(response: "Response", character_id: int, character_name: str,
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
    token = create_session_token(character_id, character_name, cfg)
    secure = cfg.frontend_origin.startswith("https://")
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True,
        secure=secure, samesite="lax", path="/",
    )


def clear_session_cookie(response: "Response") -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def is_allowed(character_id: int, corporation_id: Optional[int], alliance_id: Optional[int],
                cfg: AccessConfig = ACCESS_CONFIG) -> bool:
    """A match on *any* of the three allowlists grants access (see
    AccessConfig's own docstring for why all three are checked together)."""
    if character_id in cfg.allowed_character_ids:
        return True
    if corporation_id is not None and corporation_id in cfg.allowed_corporation_ids:
        return True
    if alliance_id is not None and alliance_id in cfg.allowed_alliance_ids:
        return True
    return False


def resolve_corp_alliance(character_id: int) -> tuple[Optional[int], Optional[int]]:
    """(corporation_id, alliance_id) for `character_id`, via ESI's public
    (no-auth) character/corporation endpoints - alliance_id is None both for
    a corp with no alliance and for a lookup failure (best-effort: a
    character/corp allowlist entry should still work even if this particular
    call has a transient hiccup, only the alliance-level check degrades)."""
    from .esi_client import ESIClient  # local import: avoids a hard dependency for callers that don't need it

    client = ESIClient()
    corporation_id = client.character_public_info(character_id).get("corporation_id")
    alliance_id = None
    if corporation_id is not None:
        try:
            alliance_id = client.corporation_public_info(corporation_id).get("alliance_id")
        except Exception:  # noqa: BLE001 - best-effort, see docstring
            pass
    return corporation_id, alliance_id
