"""location_registry access (docs/location_registry_schema.sql). Every query
goes through storage.connect(), so it is tenant-scoped by RLS."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from .. import storage

COLUMNS = (
    "location_id", "kind", "parent_location_id", "name", "solar_system_id", "status", "attempts",
    "last_attempt_at", "next_retry_at", "last_error", "resolved_at", "resolved_via", "tried_by",
)


def load(location_ids: Iterable[int]) -> dict[int, dict]:
    ids = sorted({int(i) for i in location_ids})
    if not ids:
        return {}
    with storage.connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM location_registry WHERE location_id = ANY(?)", (ids,),
        ).fetchall()
    return {int(r[0]): dict(zip(COLUMNS, r)) for r in rows}


def register(entries: list[dict]) -> None:
    """Inserts unseen ids with their initial state. An id that already exists
    only has its kind and parent refreshed - status, name and the retry
    bookkeeping belong to the resolver and are never overwritten here."""
    if not entries:
        return
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO location_registry (location_id, kind, parent_location_id, name, solar_system_id, "
            "status, resolved_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, location_id) DO UPDATE SET kind = excluded.kind, "
            "parent_location_id = excluded.parent_location_id",
            [(e["location_id"], e["kind"], e.get("parent_location_id"), e.get("name"),
              e.get("solar_system_id"), e.get("status", "pending"), e.get("resolved_at"))
             for e in entries],
        )


def due(now: datetime, tried_by: str, refresh_before: datetime, limit: int, force: bool = False,
        only_ids: Optional[Iterable[int]] = None) -> list[dict]:
    """Structure rows that should be looked up now: never tried, a failure
    whose retry date has passed (or whose capable characters changed since),
    or a name older than the refresh age. Ids with a manual name are never
    due; pending ids come first, then the longest-waiting."""
    base = (
        f"SELECT {', '.join(COLUMNS)} FROM location_registry "
        "WHERE kind = 'structure' "
        "AND location_id NOT IN (SELECT location_id FROM manual_location_names) "
    )
    id_params: tuple = ()
    if only_ids is not None:
        base += "AND location_id = ANY(?) "
        id_params = (sorted({int(i) for i in only_ids}),)
    order = "ORDER BY (status = 'pending') DESC, last_attempt_at ASC NULLS FIRST, location_id LIMIT ?"
    if force:
        sql, params = base + order, (*id_params, limit)
    else:
        sql = base + (
            "AND (status = 'pending' "
            "     OR (status IN ('forbidden', 'not_found', 'transient') "
            "         AND (next_retry_at IS NULL OR next_retry_at <= ? OR tried_by IS DISTINCT FROM ?)) "
            "     OR (status = 'resolved' AND resolved_at < ? AND (next_retry_at IS NULL OR next_retry_at <= ?))) "
        ) + order
        params = (*id_params, now, tried_by, refresh_before, now, limit)
    with storage.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(zip(COLUMNS, r)) for r in rows]


def record_success(location_id: int, name: str, solar_system_id: Optional[int], via: Optional[str],
                   now: datetime) -> None:
    with storage.connect() as conn:
        conn.execute(
            "UPDATE location_registry SET name = ?, "
            "solar_system_id = COALESCE(?, solar_system_id), status = 'resolved', attempts = 0, "
            "last_attempt_at = ?, next_retry_at = NULL, last_error = NULL, resolved_at = ?, "
            "resolved_via = ?, tried_by = NULL WHERE location_id = ?",
            (name, solar_system_id, now, now, via, location_id),
        )


def record_failure(location_id: int, status: str, error: str, next_retry_at: datetime,
                   tried_by: str, now: datetime) -> None:
    """A failed lookup. A name that was already resolved is kept (and so is its
    'resolved' status): losing access or a transient error must not hide it."""
    with storage.connect() as conn:
        conn.execute(
            "UPDATE location_registry SET status = CASE WHEN name IS NOT NULL THEN status ELSE ? END, "
            "attempts = attempts + 1, last_attempt_at = ?, next_retry_at = ?, last_error = ?, "
            "tried_by = ? WHERE location_id = ?",
            (status, now, next_retry_at, error, tried_by, location_id),
        )


def status_counts() -> dict[str, int]:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM location_registry WHERE kind = 'structure' GROUP BY status"
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}
