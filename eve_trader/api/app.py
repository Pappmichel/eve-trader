"""FastAPI app factory - CORS for the Vite dev server during development, plus
a static-files mount for the built frontend (frontend/dist/) so the "real run"
mode is a single process/port."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Scope

from .. import access_policy, scheduler, storage, tenant_scope
from ..access_gate import SESSION_COOKIE_NAME, AuthorizedSession, authorize_session_cookie

log = logging.getLogger(__name__)
from ..config import ACCESS_CONFIG, TRADING_CONFIG, apply_config_overrides
from ..doctrine.config import DOCTRINE_CONFIG
from ..production.config import PRODUCTION_CONFIG
from .routers import (
    admin, auth, characters, doctrine, errors, gate, portfolio, production, refining, sorting, station_trading, trading,
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """Falls back to index.html on a 404 instead of returning a bare 404.

    Needed because this mount only knows about files that actually exist in
    frontend/dist (index.html, assets/*.js, ...) - a client-side route like
    /production/asset-plan isn't one of those, it only "exists" once
    React Router takes over inside an already-loaded page. A hard reload
    (F5) or a bookmarked/typed URL sends a fresh GET straight to the server
    for that path, which would otherwise 404 - this mirrors what the Vite
    dev server already does automatically (which is why this only ever
    showed up in the built/deployed single-process mode, never locally).

    Tier 2 finding (business-logic audit 2026-08-28/30): this mount is at
    "/", so an unmatched `/api/...` path (a typo'd route, a retired one, or
    just internet scanner noise) also fell through to this same fallback -
    a bare 404 from a genuinely missing API route was silently rewritten
    into a 200 serving index.html, which a client/monitoring script would
    read as "success". `/api/` is never a real SPA route (every real one is
    already claimed by an `app.include_router(..., prefix="/api/...")`
    above), so path starting with "api/" (no leading slash - see this
    class's own `path` param, relative to the "/" mount point) must stay a
    real 404, not fall back. The bare "api" path (no trailing segment -
    Starlette normalizes "/api"/"/api/" to this) is excluded the same way
    (independent challenge pass, 2026-09-26 - `startswith("api/")` alone
    let exactly these two through)."""

    async def get_response(self, path: str, scope: Scope):
        # StaticFiles doesn't return a 404 Response here on a missing file -
        # it *raises* HTTPException(404) (confirmed live: a plain
        # `if response.status_code == 404` check on the return value never
        # fired, since execution never reaches it) - has to be caught, not
        # branched on.
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and path != "api" and not path.startswith("api/"):
                return await super().get_response("index.html", scope)
            raise

# Reachable without a gate session even while AccessConfig.access_gate_enabled
# is true - the login flow itself, plus the one status/logout pair the
# frontend needs to be able to call *before* knowing whether it's logged in.
_GATE_EXEMPT_PATHS = {
    "/api/auth/gate/start",
    "/api/auth/callback",
    "/api/gate/status",
    "/api/gate/logout",
}

# /api routes that require a valid session when the gate is on, but no
# tool grant. Middleware 401s without a cookie; `_required_tool_for_path`
# returns None so any authenticated character may call them. Kept next to
# the other classification tables so a new /api route cannot be added
# without an explicit bucket (see tests/test_gate_route_coverage.py).
_SESSION_ONLY_API_PREFIXES = (
    "/api/errors",
)

# Path prefix -> the tool_key a request under it requires (see
# access_gate.authorize_session_cookie). Only enforced while the gate is
# enabled.
_TOOL_PATH_PREFIXES = {
    "/api/trading/": "trading",
    "/api/production/": "production",
    "/api/doctrine/": "doctrine",
    "/api/refining/": "refining",
    "/api/station-trading/": "station_trading",
    "/api/sorting/": "sorting",
    "/api/portfolio/": "portfolio",
    "/api/admin/": "admin",
    "/api/characters/": "characters",
}


def _is_docs_path(path: str) -> bool:
    return path == "/openapi.json" or path.startswith("/docs") or path.startswith("/redoc")


def _is_session_only_api_path(path: str) -> bool:
    for prefix in _SESSION_ONLY_API_PREFIXES:
        stripped = prefix.rstrip("/")
        if path == stripped or path.startswith(stripped + "/"):
            return True
    return False


def _is_auth_role_gated_path(_path: str) -> bool:
    """Prefix `/api/auth/{role}/start` and `/access-preview` were removed in
    Phase 9. Kept so the classification matrix still has an explicit bucket
    for that shape (always false for live routes)."""
    return False


async def _affiliation_block(session: AuthorizedSession) -> Optional[JSONResponse]:
    """After a cookie is authorized: honor a suspension flag, and lazily
    re-check affiliation when the last check is older than 6 hours.

    The ESI call runs in a worker thread. `dispatch` is async; a blocking
    fetch here would stall every request on the event loop. Admins and an
    empty allowlist are exempt (decisions 3c, 3d). A stale suspension flag
    on an exempt session is cleared, not enforced.
    """
    exempt = "admin" in session.tool_keys or not session.allowlist_active
    if exempt:
        if session.access_suspended:
            await run_in_threadpool(
                access_policy.refresh_registered, session.character_id, session.tool_keys, False,
            )
        return None
    if session.access_suspended:
        return JSONResponse({"detail": "access_suspended"}, status_code=403)
    if not access_policy.recheck_due(
        session.affiliation_checked_at, session.tool_keys, session.allowlist_active,
    ):
        return None
    try:
        verdict = await run_in_threadpool(
            access_policy.refresh_registered, session.character_id, session.tool_keys, False,
        )
    except Exception:
        log.exception("affiliation re-check failed")
        return JSONResponse({"detail": "access_unverifiable"}, status_code=403)
    if verdict == access_policy.Verdict.SUSPENDED:
        return JSONResponse({"detail": "access_suspended"}, status_code=403)
    if verdict == access_policy.Verdict.UNKNOWN:
        return JSONResponse({"detail": "access_unverifiable"}, status_code=403)
    return None


def _required_tool_for_path(path: str, method: str = "GET") -> Optional[str]:
    # F-06's one-off exception here (POST /api/portfolio/backups required
    # "admin" even though the path lived under /api/portfolio/) is gone -
    # both backup routes moved to /api/admin/backups (confirmed real
    # misplacement 2026-09-21, see admin.do_create_backup's own docstring),
    # so the plain prefix table below already covers them correctly.
    for prefix, tool_key in _TOOL_PATH_PREFIXES.items():
        if path.startswith(prefix):
            return tool_key
    return None


class AccessGateMiddleware(BaseHTTPMiddleware):
    """Gates every /api/* route behind a valid access-gate session cookie
    once AccessConfig.access_gate_enabled is true (on by default; the check
    re-reads ACCESS_CONFIG on every request rather than once at startup, so
    flipping it in config.yaml + restarting is the only wiring needed). See
    access_gate.py's own module docstring for why this exists, and
    auth.py's callback()/gate.py for the login flow that issues the cookie
    this checks.

    Session cookies are re-validated against tenant_registry_entries and
    tool_grants on every request (access_gate.authorize_session_cookie) —
    a signed cookie is not enough if the character was removed, reassigned,
    or had sessions_valid_after advanced.

    Also - regardless of whether the gate is enabled - sets storage.py's
    ambient tenant_id contextvar for the duration of the request (reset in a
    finally, so it can never leak into a later, unrelated request on the
    same worker): storage.DEFAULT_TENANT_ID when the gate is off (a trusted
    single operator who turned it off in config.yaml) or on but the path is
    exempt/no tenant resolution applies yet; the session cookie's own
    resolved tenant_id when the gate is on and the cookie is valid. Every
    real storage.py query needs a tenant now (see storage.connect()'s
    fail-closed check) - without this, every request would 500 with "no
    tenant set" regardless of the gate's own enabled/disabled state.

    Registered *after* CORSMiddleware below (Starlette's first-added
    middleware ends up outermost) so CORS preflight (OPTIONS) requests and
    the CORS headers on this middleware's own 401 responses are still
    handled correctly - a 401 with no CORS headers would otherwise just look
    like a network error to a cross-origin dev frontend (localhost:5173),
    not a clear 401."""

    async def dispatch(self, request: Request, call_next):
        if not ACCESS_CONFIG.access_gate_enabled:
            # Gate off - every request is DEFAULT_TENANT_ID, structurally
            # (there's no session to resolve a *different* tenant from), so
            # the config-bleeds-across-tenants gap tenant_scope.enter_tenant
            # exists to fix cannot occur here - only one tenant is ever in
            # play. A bare storage.set_current_tenant is enough (and doesn't
            # cost every request a real Postgres round-trip just to
            # re-resolve config for the one tenant that already owns the
            # live shared instance - see _lifespan's own one-time load for
            # how a Settings-page save still survives a restart).
            return await self._call_with_default_tenant(call_next, request)

        path = request.url.path
        if _is_docs_path(path):
            session = authorize_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
            if session is None:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            blocked = await _affiliation_block(session)
            if blocked is not None:
                return blocked
            return await call_next(request)

        if not path.startswith("/api/") or path in _GATE_EXEMPT_PATHS:
            # Exempt paths (the login flow itself, gate status/logout) never
            # had a tenant to resolve yet - /callback resolves its own via
            # storage.connect_unscoped() internally, doesn't need one set here.
            return await call_next(request)

        session = authorize_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
        if session is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        blocked = await _affiliation_block(session)
        if blocked is not None:
            return blocked

        required_tool = _required_tool_for_path(path, request.method)
        if required_tool is not None and required_tool not in session.tool_keys:
            return JSONResponse({"detail": "Forbidden - missing tool grant"}, status_code=403)

        # Gate on - the request could genuinely be any of several different
        # real tenants, so their own TRADING_CONFIG/PRODUCTION_CONFIG must be
        # resolved fresh here, not left pointing at whichever tenant's
        # settings happened to be live last - see tenant_scope's own docstring.
        with tenant_scope.enter_tenant(session.tenant_id):
            return await call_next(request)

    @staticmethod
    async def _call_with_default_tenant(call_next, request: Request):
        context_token = storage.set_current_tenant(storage.DEFAULT_TENANT_ID)
        try:
            return await call_next(request)
        finally:
            storage.reset_current_tenant(context_token)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _load_default_tenant_config()
    scheduler.start()  # no-op unless DEFAULT_TENANT_ID's own scheduler_enabled - see scheduler.py
    yield
    scheduler.stop()


def _load_default_tenant_config() -> None:
    """One-time, at-boot load of DEFAULT_TENANT_ID's own saved Settings-page
    overrides into TRADING_CONFIG/PRODUCTION_CONFIG/DOCTRINE_CONFIG's
    *shared default* instance (mutated directly via apply_config_overrides,
    not via a ContextVar.set() - the gate-disabled request path above never
    sets a per-request value at all, it always falls through to this same
    default object, so mutating it once here is enough for every future
    gate-disabled request to see it, with no per-request Postgres cost).

    Without this, a Settings-page save already takes effect immediately for
    the rest of the current process (apply_config_overrides mutates the
    live instance in place) but is silently lost across a restart - only
    config.yaml is read at TRADING_CONFIG/PRODUCTION_CONFIG/DOCTRINE_CONFIG's
    own import time, never tenant_settings."""
    with storage.tenant_context(storage.DEFAULT_TENANT_ID):
        trading_overrides = storage.load_tenant_settings("trading")
        production_overrides = storage.load_tenant_settings("production")
        doctrine_overrides = storage.load_tenant_settings("doctrine")
    if trading_overrides:
        apply_config_overrides(TRADING_CONFIG, trading_overrides)
    if production_overrides:
        apply_config_overrides(PRODUCTION_CONFIG, production_overrides)
    if doctrine_overrides:
        apply_config_overrides(DOCTRINE_CONFIG, doctrine_overrides)


def create_app() -> FastAPI:
    app = FastAPI(title="EVE Trader API", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AccessGateMiddleware)

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(gate.router, prefix="/api/gate", tags=["gate"])
    app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
    app.include_router(production.router, prefix="/api/production", tags=["production"])
    app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
    app.include_router(doctrine.router, prefix="/api/doctrine", tags=["doctrine"])
    app.include_router(refining.router, prefix="/api/refining", tags=["refining"])
    app.include_router(station_trading.router, prefix="/api/station-trading", tags=["station_trading"])
    app.include_router(sorting.router, prefix="/api/sorting", tags=["sorting"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(characters.router, prefix="/api/characters", tags=["characters"])
    app.include_router(errors.router, prefix="/api/errors", tags=["errors"])

    if FRONTEND_DIST.exists():
        app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
