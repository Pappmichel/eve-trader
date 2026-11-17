"""Public liveness/readiness probe for the deploy script and reverse proxy.

Deliberately minimal: status only. Version, backup age and sync age live
behind the admin grant (`GET /api/admin/health`), not on an open endpoint.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ... import storage

log = logging.getLogger(__name__)

router = APIRouter()

# Seconds to wait for a pooled connection; also bounds the whole probe, so an
# exhausted pool answers 503 instead of hanging the caller.
DB_CHECK_TIMEOUT_SECONDS = 2.0


def _check_database() -> None:
    with storage.connect_unscoped(timeout=DB_CHECK_TIMEOUT_SECONDS) as conn:
        conn.execute("SELECT 1").fetchone()


@router.get("/health")
async def health():
    try:
        await asyncio.wait_for(
            run_in_threadpool(_check_database), timeout=DB_CHECK_TIMEOUT_SECONDS + 1.0,
        )
    except Exception as e:  # PoolTimeout, OperationalError, timeout, ...
        log.warning("health check degraded: %s", type(e).__name__)
        return JSONResponse({"status": "degraded"}, status_code=503)
    return {"status": "ok"}
