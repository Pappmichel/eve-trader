"""Self-hosted error tracking (GitHub issue #88) - see docs/observability_
schema.sql for the storage-layer rationale (deliberately unscoped, like
tool_grants/tenants). Cross-cutting like portfolio.py/admin.py: `do_report_
error` is reachable by any logged-in tenant's frontend (a valid session is
required, no tool grant - see api/app.py's _SESSION_ONLY_API_PREFIXES), while
`do_list_errors` is Admin-tool-only (wired through api/routers/admin.py,
not this module's own router).
"""
from __future__ import annotations

import logging
import re

from . import storage

logger = logging.getLogger(__name__)

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
    # api/routers/errors.py deliberately doesn't _wrap this endpoint - error
    # reporting must not itself raise a user-visible error - so that
    # guarantee has to hold here too: a Postgres failure while trying to
    # record an error must degrade to {"recorded": False}, not propagate as
    # a raw 500 out of what's meant to be a best-effort diagnostic aid.
    try:
        storage.log_error(
            source=_truncate(source, 50),
            message=_truncate(message, _MAX_MESSAGE_LENGTH),
            detail=_truncate(detail, _MAX_DETAIL_LENGTH) if detail else None,
            path=_truncate(path, _MAX_PATH_LENGTH) if path else None,
        )
    except Exception:
        logger.exception("Failed to record frontend/backend error report")
        return {"recorded": False}
    return {"recorded": True}


def do_list_errors(limit: int = 200) -> list[dict]:
    return storage.list_errors(limit=limit)


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DIGITS_RE = re.compile(r"\d+")


def normalize_message(message: str) -> str:
    """Message with UUIDs and digit runs replaced, so "error 1 <uuid>" and
    "error 2 <uuid>" count as one problem."""
    return _DIGITS_RE.sub("#", _UUID_RE.sub("<id>", message))


def group_errors(rows: list[dict]) -> list[dict]:
    """Groups rows (newest first, as list_errors returns them) by source and
    normalized message. Each group carries the count, first/last time, the
    number of distinct tenants and the newest row's detail/path. Newest
    group first."""
    groups: dict[tuple[str, str], dict] = {}
    tenants: dict[tuple[str, str], set] = {}
    for row in rows:
        key = (row["source"], normalize_message(row["message"]))
        g = groups.get(key)
        if g is None:
            groups[key] = g = {
                "source": row["source"], "message": row["message"], "count": 0,
                "first_seen": row["created_at"], "last_seen": row["created_at"],
                "detail": row["detail"], "path": row["path"],
            }
            tenants[key] = set()
        g["count"] += 1
        if row["created_at"] is not None:
            if g["first_seen"] is None or row["created_at"] < g["first_seen"]:
                g["first_seen"] = row["created_at"]
        tenants[key].add(row["tenant_id"])
    for key, g in groups.items():
        g["tenant_count"] = len(tenants[key])
    return list(groups.values())


def do_list_error_groups(limit: int = 1000) -> list[dict]:
    return group_errors(storage.list_errors(limit=limit))
