"""Frontend error reporting (GitHub issue #88) - deliberately its own
router/prefix, not folded into admin.py's router: this POST endpoint must
stay reachable by every tenant's frontend (an ErrorBoundary crash or an
unhandled exception can happen on any page, for any tenant), unlike every
route under /api/admin/* which is gated to the "admin" tool grant only -
see api/app.py's _TOOL_PATH_PREFIXES, which has no entry for "/api/errors/"
at all, so no tool grant is required here."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ... import error_log

router = APIRouter()


class ReportErrorRequest(BaseModel):
    source: str
    message: str
    detail: str | None = None
    path: str | None = None


@router.post("")
def report_error(req: ReportErrorRequest):
    # Never _wrap'd into a 400 on failure - reporting an error must not
    # itself raise a user-visible error. FastAPI's own request-validation
    # (missing/wrong-typed fields) still 422s, which is fine: that's a
    # frontend bug in the reporting call itself, not something to swallow.
    return error_log.do_report_error(
        source=req.source, message=req.message, detail=req.detail, path=req.path,
    )
