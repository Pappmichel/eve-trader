"""FastAPI app factory - CORS for the Vite dev server during development, plus
a static-files mount for the built frontend (frontend/dist/) so the "real run"
mode is a single process/port."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Scope

from .. import scheduler, storage, tenant_scope
from ..access_gate import SESSION_COOKIE_NAME, read_session_token, tools_for
from ..config import ACCESS_CONFIG, TRADING_CONFIG, apply_config_overrides
from ..doctrine.config import DOCTRINE_CONFIG
from ..production.config import PRODUCTION_CONFIG
from .routers import admin, auth, doctrine, errors, gate, portfolio, production, refining, trading

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
    showed up in the built/deployed single-process mode, never locally)."""

    async def get_response(self, path: str, scope: Scope):
        # StaticFiles doesn't return a 404 Response here on a missing file -
        # it *raises* HTTPException(404) (confirmed live: a plain
        # `if response.status_code == 404` check on the return value never
        # fired, since execution never reaches it) - has to be caught, not
        # branched on.
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
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

# Path prefix -> the tool_key a request under it requires (see access_gate.
# tools_for). Only enforced while the gate is enabled - see dispatch() below;
# a path with no matching prefix (e.g. /api/gate/*) is never tool-gated, only
# session-gated. /api/auth/{role_prefix}/(start|consent) is handled
# separately below (GitHub issue #57) - it doesn't share one fixed prefix
# per tool the way these do, since the tool depends on the path's own
# role_prefix segment.
_TOOL_PATH_PREFIXES = {
    "/api/trading/": "trading",
    "/api/production/": "production",
    "/api/doctrine/": "doctrine",
    "/api/refining/": "refining",
    "/api/portfolio/": "portfolio",
    "/api/admin/": "admin",
}

_AUTH_START_PREFIX = "/api/auth/"
# Every /api/auth/{role_prefix}/<suffix> route that reveals or acts on a
# specific tool's login role needs the same tool-grant check as /start -
# /consent (GET status, POST acknowledge - the role-login data-access
# confirmation, see auth.py's own get_consent_status/acknowledge_consent)
# was added after #57's own fix and would otherwise silently fall through
# this function to None (ungated) the exact same way /start used to.
_AUTH_GATED_SUFFIXES = ("/start", "/consent")


def _required_tool_for_path(path: str) -> Optional[str]:
    for prefix, tool_key in _TOOL_PATH_PREFIXES.items():
        if path.startswith(prefix):
            return tool_key
    # GitHub issue #57 (found in a full-codebase audit 2026-08-21, confirmed
    # real gap): /api/auth/{role_prefix}/start used to be reachable by any
    # character with a valid gate session regardless of tool grants - e.g. a
    # character granted only "trading" could still call
    # /api/auth/producer/start and register a live ESI token for
    # Production. ROLE_PREFIX_TOOL (auth.py) is the single source of truth
    # for which tool each role_prefix belongs to - "gate" maps to None
    # (identity-only, and already fully exempt via _GATE_EXEMPT_PATHS
    # before this function is ever reached for it) and an unrecognized
    # role_prefix also maps to None here (the route handler itself rejects
    # it with a 400 - see auth.start_login), never silently granted access.
    if path.startswith(_AUTH_START_PREFIX):
        for suffix in _AUTH_GATED_SUFFIXES:
            if path.endswith(suffix):
                role_prefix = path[len(_AUTH_START_PREFIX):-len(suffix)]
                return auth.ROLE_PREFIX_TOOL.get(role_prefix)
    return None


class AccessGateMiddleware(BaseHTTPMiddleware):
    """Gates every /api/* route behind a valid access-gate session cookie
    once AccessConfig.access_gate_enabled is true (the check re-reads
    ACCESS_CONFIG on every request rather than once at startup, so flipping
    it in config.yaml + restarting is the only wiring needed, nothing here
    needs to change). See access_gate.py's own module docstring for why this
    exists, and auth.py's callback()/gate.py for the login flow that issues
    the cookie this checks.

    Also - regardless of whether the gate is enabled - sets storage.py's
    ambient tenant_id contextvar for the duration of the request (reset in a
    finally, so it can never leak into a later, unrelated request on the
    same worker): storage.DEFAULT_TENANT_ID when the gate is off (this app's
    default - a trusted single operator, no login wall, see
    docs/phase3_schema.sql's seed row) or on but the path is exempt/no
    tenant resolution applies yet; the session cookie's own resolved
    tenant_id when the gate is on and the cookie is valid. Every real
    storage.py query needs a tenant now (see storage.connect()'s fail-closed
    check) - without this, every request would 500 with "no tenant set"
    regardless of the gate's own enabled/disabled state.

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
        if not path.startswith("/api/") or path in _GATE_EXEMPT_PATHS:
            # Exempt paths (the login flow itself, gate status/logout) never
            # had a tenant to resolve yet - /callback resolves its own via
            # storage.connect_unscoped() internally, doesn't need one set here.
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE_NAME)
        data = read_session_token(token) if token else None
        # A still-valid (unexpired, correctly-signed) cookie from before
        # tenant_id was added to the session payload (multi-tenant migration
        # Phase 3a) would decode successfully - itsdangerous only checks the
        # signature/expiry, not the payload shape - but have no "tenant_id"
        # key. Treat that the same as "not authenticated" (a stale cookie
        # forcing a fresh login) rather than letting `data["tenant_id"]`
        # raise KeyError into an unhandled 500 below.
        tenant_id = data.get("tenant_id") if data else None
        if tenant_id is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        # Per-tool enforcement - a valid session only proves *who*, not
        # *which tools*; this is the actual authorization check (see
        # access_gate.tools_for's own docstring for why it's the one
        # chokepoint both this and /api/gate/status's `tools` field use).
        # Deliberately only reachable here, not left to the frontend's own
        # tool-filtered Landing page - hiding a card doesn't stop a direct
        # API call, only this does.
        required_tool = _required_tool_for_path(path)
        if required_tool is not None and required_tool not in tools_for(tenant_id, data["character_id"]):
            return JSONResponse({"detail": "Forbidden - missing tool grant"}, status_code=403)

        # Gate on - the request could genuinely be any of several different
        # real tenants, so their own TRADING_CONFIG/PRODUCTION_CONFIG must be
        # resolved fresh here, not left pointing at whichever tenant's
        # settings happened to be live last - see tenant_scope's own docstring.
        with tenant_scope.enter_tenant(tenant_id):
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
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(errors.router, prefix="/api/errors", tags=["errors"])

    if FRONTEND_DIST.exists():
        app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
