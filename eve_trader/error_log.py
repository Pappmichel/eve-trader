"""Self-hosted error tracking (GitHub issue #88) - see docs/observability_
schema.sql for the storage-layer rationale (deliberately unscoped, like
tool_grants/tenants). Cross-cutting like portfolio.py/admin.py: `do_report_
error` is reachable by any tenant's frontend (no tool grant required, same
as e.g. /api/gate/status - see api/app.py's _TOOL_PATH_PREFIXES), while
`do_list_errors` is Admin-tool-only (wired through api/routers/admin.py,
not this module's own router).
"""
from __future__ import annotations

from . import storage

# Bounds how much a single report can write - a render-crash stack trace or
# a long error message is exactly the kind of thing that's unbounded in
# practice (deeply nested component trees, minified bundle URLs), and this
# is a best-effort diagnostic aid, not a place that needs the full text to
# be useful. Generous enough to keep a real stack trace's most useful
# (top) frames.
_MAX_MESSAGE_LENGTH = 2000
_MAX_DETAIL_LENGTH = 8000
_MAX_PATH_LENGTH = 500


def _truncate(value: str, max_length: int) -> str:
    return value if len(value) <= max_length else value[:max_length]


def do_report_error(source: str, message: str, detail: str | None = None, path: str | None = None) -> dict:
    storage.log_error(
        source=_truncate(source, 50),
        message=_truncate(message, _MAX_MESSAGE_LENGTH),
        detail=_truncate(detail, _MAX_DETAIL_LENGTH) if detail else None,
        path=_truncate(path, _MAX_PATH_LENGTH) if path else None,
    )
    return {"recorded": True}


def do_list_errors(limit: int = 200) -> list[dict]:
    return storage.list_errors(limit=limit)
