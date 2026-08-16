"""FastAPI app factory - CORS for the Vite dev server during development, plus
a static-files mount for the built frontend (frontend/dist/) so the "real run"
mode is a single process/port."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Scope

from .. import scheduler
from ..access_gate import SESSION_COOKIE_NAME, read_session_token
from ..config import ACCESS_CONFIG
from .routers import auth, gate, portfolio, production, trading

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


class AccessGateMiddleware(BaseHTTPMiddleware):
    """Gates every /api/* route behind a valid access-gate session cookie
    once AccessConfig.access_gate_enabled is true - a complete no-op
    otherwise (the check re-reads ACCESS_CONFIG on every request rather than
    once at startup, so flipping it in config.yaml + restarting is the only
    wiring needed, nothing here needs to change). See access_gate.py's own
    module docstring for why this exists, and auth.py's callback()/gate.py
    for the login flow that issues the cookie this checks.

    Registered *after* CORSMiddleware below (Starlette's first-added
    middleware ends up outermost) so CORS preflight (OPTIONS) requests and
    the CORS headers on this middleware's own 401 responses are still
    handled correctly - a 401 with no CORS headers would otherwise just look
    like a network error to a cross-origin dev frontend (localhost:5173),
    not a clear 401."""

    async def dispatch(self, request: Request, call_next):
        if not ACCESS_CONFIG.access_gate_enabled:
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") or path in _GATE_EXEMPT_PATHS:
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not token or read_session_token(token) is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    scheduler.start()  # no-op unless TradingConfig.scheduler_enabled - see scheduler.py
    yield
    scheduler.stop()


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

    if FRONTEND_DIST.exists():
        app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
