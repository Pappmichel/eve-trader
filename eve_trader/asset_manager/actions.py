"""Asset Manager do_* actions (docs/ASSET_MANAGER_PLAN.md) - UI-agnostic,
called by api/routers/asset_manager.py and the CLI.

Loaded rows are cached per tenant for a short TTL (one lock per cache key,
CLAUDE.md "Caching pattern": several independent keys) and dropped after a
sync, so paging and filtering do not reload and re-price every asset.
"""
from __future__ import annotations

import csv
import io
import threading
import time
from typing import Optional

from .. import storage
from ..actions import ActionError
from . import collect, query
from .scope import ASSET_KINDS, TOOL_KEY, allowed_tools, granted_tools, may_use, owner_scope

_CACHE_TTL = 120  # seconds
_cache: dict[tuple, tuple[float, list[dict]]] = {}
_cache_locks: dict[tuple, threading.Lock] = {}
_cache_guard = threading.Lock()


def invalidate_cache(tenant_id: Optional[str] = None) -> None:
    """Drop cached rows for one tenant (None = every tenant)."""
    with _cache_guard:
        for key in [k for k in _cache if tenant_id is None or k[0] == tenant_id]:
            _cache.pop(key, None)


def _context() -> tuple[Optional[set[str]], Optional[set[str]], dict]:
    grants = granted_tools()
    allowed = allowed_tools(grants)
    return grants, allowed, owner_scope(allowed)


def _rows(grants: Optional[set[str]], allowed: Optional[set[str]], scope: dict) -> list[dict]:
    include_manual = may_use("production", grants)
    scope_key = tuple(sorted((k, tuple(c), tuple(o)) for k, (c, o) in scope.items()))
    key = (storage.get_current_tenant(), None if allowed is None else frozenset(allowed),
           include_manual, scope_key)
    with _cache_guard:
        lock = _cache_locks.setdefault(key, threading.Lock())
    with lock:
        hit = _cache.get(key)
        if hit is not None and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        rows = collect.load_base_rows(dict(scope), include_manual=include_manual)
        with _cache_guard:
            _cache[key] = (time.time(), rows)
        return rows


def _query_from(params: dict) -> query.ItemQuery:
    def ints(name):
        return tuple(int(v) for v in params.get(name) or ())
    try:
        q = query.ItemQuery(
            q=params.get("q") or "",
            owners=tuple(params.get("owners") or ()),
            region_id=params.get("region_id"),
            system_id=params.get("system_id"),
            location_id=params.get("location_id"),
            hangar=params.get("hangar"),
            container_item_id=params.get("container_item_id"),
            category_ids=ints("category_ids"),
            group_ids=ints("group_ids"),
            type_ids=ints("type_ids"),
            sources=tuple(params.get("sources") or ()),
            hide_badges=tuple(params.get("hide_badges") or ()),
            stock_only=bool(params.get("stock_only")),
            group_by=params.get("group_by") or "type_location",
            sort=params.get("sort") or "value",
            desc=params.get("desc", True) is not False,
            page=int(params.get("page") or 1),
            page_size=int(params.get("page_size") or 100),
        )
        query.validate(q)
    except (query.QueryError, TypeError, ValueError) as e:
        raise ActionError(str(e)) from e
    return q


def do_list_items(**params) -> dict:
    """One page of rows plus totals over the whole filter and the filter
    options (facets). Parameters: see query.ItemQuery."""
    q = _query_from(params)
    grants, allowed, scope = _context()
    rows = _rows(grants, allowed, scope)
    result = query.run(rows, q)
    result["manual_stock_visible"] = may_use("production", grants)
    result["shared"] = any(chars or corps for chars, corps in scope.values())
    return result


def all_filtered_rows(**params) -> tuple[query.ItemQuery, list[dict]]:
    """Every grouped row of the filter, unpaged (exports)."""
    q = _query_from({**params, "page": 1, "page_size": query.MAX_PAGE_SIZE})
    grants, allowed, scope = _context()
    rows = _rows(grants, allowed, scope)
    filtered = [r for r in rows if query.matches(r, q)]
    return q, query.sort_rows(query.group(filtered, q.group_by), q.sort, q.desc)


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    """Container and ship names are free text set by any corp member; a
    leading formula character is neutralised for spreadsheets (R-15)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " / ".join(str(v) for v in value)
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


_CSV_COLUMNS = (
    ("type_name", "Type"), ("quantity", "Quantity"), ("location_name", "Location"),
    ("solar_system_name", "System"), ("region_name", "Region"), ("owners", "Owners"),
    ("owner_name", "Owner"), ("hangar", "Hangar"), ("container_path", "Container"),
    ("item_name", "Name"), ("sources", "Sources"), ("source", "Source"), ("badges", "Badges"),
    ("unit_price", "Unit price"), ("value", "Value"), ("volume", "Volume m3"),
    ("category_name", "Category"), ("group_name", "Group"),
)


def do_export_csv(**params) -> str:
    q, rows = all_filtered_rows(**params)
    present = [(k, label) for k, label in _CSV_COLUMNS if any(k in r for r in rows[:1])] or list(_CSV_COLUMNS)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _k, label in present])
    for r in rows:
        writer.writerow([_csv_cell(r.get(k)) for k, _label in present])
    return buf.getvalue()


def do_export_eve_list(**params) -> str:
    """`Type name<TAB>quantity` per type for the current filter - the paste
    format EVE multibuy and appraisal sites read. Quantities are summed per
    type whatever the grouping; fractional job outputs are rounded down."""
    _q, rows = all_filtered_rows(**{**params, "group_by": "type"})
    lines = [f"{r['type_name']}\t{int(r['quantity'])}" for r in rows if r["quantity"] >= 1]
    return "\n".join(lines)


def do_freshness() -> dict:
    """Per owner and kind: last success, last attempt, last error - only
    owners the page may read, only the kinds it reads (R-16)."""
    _grants, _allowed, scope = _context()
    owners: dict[tuple[str, int], set[str]] = {}
    for kind, (chars, corps) in scope.items():
        for c in chars:
            owners.setdefault(("character", c), set()).add(kind)
        for c in corps:
            owners.setdefault(("corporation", c), set()).add(kind)
    fresh = {
        (owner_type, owner_id, kind): (success, attempt, error)
        for owner_type, owner_id, kind, success, attempt, error, _as_of in storage.list_esi_freshness()
    }
    names = collect._owner_names(set(owners), {})
    rows = []
    oldest: dict[str, Optional[str]] = {}
    for (owner_type, owner_id), kinds in sorted(owners.items(), key=lambda kv: names[kv[0]].lower()):
        entry = {"owner_type": owner_type, "owner_id": owner_id, "owner_name": names[(owner_type, owner_id)],
                 "kinds": {}}
        for kind in ASSET_KINDS:
            if kind not in kinds:
                continue
            success, attempt, error = fresh.get((owner_type, owner_id, kind), (None, None, None))
            entry["kinds"][kind] = {"last_success_at": success, "last_attempt_at": attempt, "last_error": error}
            if kind not in oldest or (oldest[kind] is not None and (success is None or success < oldest[kind])):
                oldest[kind] = success
        rows.append(entry)
    return {"owners": rows, "oldest_success": oldest}


def do_sync(progress_callback=None) -> dict:
    """Refreshes every kind the page reads for every owner in the union of
    shares (D11), then drops the cached rows."""
    from ..esi_data.orchestrator import do_sync_kinds

    tenant_id = storage.get_current_tenant()
    allowed = allowed_tools(granted_tools())
    if progress_callback:
        progress_callback({"message": "Syncing assets"})
    result = do_sync_kinds(ASSET_KINDS, tool_keys=allowed)
    invalidate_cache(tenant_id)
    return result


def do_start_sync() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.start_job(TOOL_KEY, "sync", "Sync assets", lambda cb: do_sync(progress_callback=cb))


def do_sync_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(TOOL_KEY, "sync")
