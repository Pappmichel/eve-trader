"""FastAPI app factory - CORS for the Vite dev server during development, plus
a static-files mount for the built frontend (frontend/dist/) so the "real run"
mode is a single process/port."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import scheduler
from .routers import auth, portfolio, production, trading

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


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

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
    app.include_router(production.router, prefix="/api/production", tags=["production"])
    app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
