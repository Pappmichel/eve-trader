"""Frontend error reporting (GitHub issue #88) - deliberately its own
router/prefix, not folded into admin.py's router: this POST endpoint must
stay reachable by every tenant's frontend (an ErrorBoundary crash or an
unhandled exception can happen on any page, for any tenant), unlike every
route under /api/admin/* which is gated to the "admin" tool grant only -
see api/app.py's _TOOL_PATH_PREFIXES, which has no entry for "/api/errors/"
at all, so no tool grant is required here."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ... import error_log

router = APIRouter()

# Security audit 2026-08-23, ahead of opening this deployment to more than
# one invited operator: this endpoint has no tool-grant/gate check at all
# (by design, see the module docstring above), so once the app sits on a
# public IP it's reachable by anyone on the internet, not just registered
# tenants. Two independent bounds, both stdlib-only (no external rate-limit
# library, matching this codebase's own time.time()-based caching
# convention - see CLAUDE.md's "Caching pattern" section): field-length
# caps below stop one oversized payload, and this per-source-IP sliding
# window stops unbounded repeated calls. `error_log`'s own MAX_ERROR_LOG_ROWS
# pruning already bounds total storage - this bounds request/write churn,
# a distinct concern.
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 20
_rate_limit_lock = threading.Lock()
_rate_limit_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(client_ip: str) -> bool:
    now = time.time()
    with _rate_limit_lock:
        hits = _rate_limit_hits[client_ip]
        while hits and now - hits[0] > _RATE_LIMIT_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= _RATE_LIMIT_MAX_REQUESTS:
            return True
        hits.append(now)
        return False


class ReportErrorRequest(BaseModel):
    source: str = Field(max_length=100)
    message: str = Field(max_length=2000)
    detail: str | None = Field(default=None, max_length=4000)
    path: str | None = Field(default=None, max_length=500)


@router.post("")
def report_error(req: ReportErrorRequest, request: Request):
    # A real, occasional error report must never itself fail (see below) -
    # this 429 only trips for actual abuse (way more than any single
    # legitimate frontend session would ever report in a minute).
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        raise HTTPException(429, "Too many error reports - try again shortly.")
    # Never _wrap'd into a 400 on failure - reporting an error must not
    # itself raise a user-visible error. FastAPI's own request-validation
    # (missing/wrong-typed fields) still 422s, which is fine: that's a
    # frontend bug in the reporting call itself, not something to swallow.
    return error_log.do_report_error(
        source=req.source, message=req.message, detail=req.detail, path=req.path,
    )
