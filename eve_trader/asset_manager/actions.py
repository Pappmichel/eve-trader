"""Asset Manager do_* actions (docs/ASSET_MANAGER_PLAN.md) - UI-agnostic,
called by api/routers/asset_manager.py and the CLI.

Loaded rows are cached per tenant for a short TTL (one lock per cache key,
CLAUDE.md "Caching pattern": several independent keys) and dropped after a
sync, so paging and filtering do not reload and re-price every asset.
"""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from typing import Optional

from .. import storage
from ..actions import ActionError
from . import collect, query
from .scope import ASSET_KINDS, TOOL_KEY, allowed_tools, granted_tools, may_use, owner_scope

log = logging.getLogger(__name__)

_CACHE_TTL = 120  # seconds
_cache: dict[tuple, tuple[float, list[dict]]] = {}
_cache_locks: dict[tuple, threading.Lock] = {}
_cache_guard = threading.Lock()
# wanted_by per (tenant, grants): one read per doctrine fitting, so it rides
# the same TTL instead of running on every page request.
_wanted_cache: dict[tuple, tuple[float, dict[int, list[dict]]]] = {}


def invalidate_cache(tenant_id: Optional[str] = None) -> None:
    """Drop cached rows for one tenant (None = every tenant)."""
    with _cache_guard:
        for store in (_cache, _wanted_cache):
            for key in [k for k in store if tenant_id is None or k[0] == tenant_id]:
                store.pop(key, None)


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
        now = time.time()
        with _cache_guard:
            # One live entry per tenant: a changed share or grant makes a new
            # key, and expired or superseded rows must not pile up in memory.
            for old in [k for k, (at, _r) in _cache.items() if k[0] == key[0] or now - at >= _CACHE_TTL]:
                _cache.pop(old, None)
                if old != key:
                    _cache_locks.pop(old, None)
            _cache[key] = (now, rows)
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
    """One page of rows plus totals over the whole filter, the filter
    options (facets) and, per row, the tools that want that type.
    Parameters: see query.ItemQuery."""
    q = _query_from(params)
    grants, allowed, scope = _context()
    rows = _rows(grants, allowed, scope)
    result = query.run(rows, q)
    # Which tool wants each type (F-4), for the returned page only.
    demand = wanted_by(grants)
    for row in result["rows"]:
        row["wanted_by"] = demand.get(row["type_id"], [])
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
    # Numbers are written raw (a negative value must stay a number); only text
    # can carry a formula.
    if not isinstance(value, (int, float)) and text.startswith(_FORMULA_PREFIXES):
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


_CSV_ITEM_ONLY = {"owner_name", "source", "hangar", "container_path", "item_name"}
_CSV_GROUP_ONLY = {"owners", "sources"}


def do_export_csv(**params) -> str:
    q, rows = all_filtered_rows(**params)
    skip = _CSV_GROUP_ONLY if q.group_by == "none" else _CSV_ITEM_ONLY
    if q.group_by == "type":
        skip = skip | {"location_name", "solar_system_name", "region_name"}
    present = [(k, label) for k, label in _CSV_COLUMNS if k not in skip]
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
    grants, allowed, scope = _context()
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
    # No `_rows` here: a cold load prices every asset, and a freshness poll
    # must not wait for (or hold the lock of) the inventory load.
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
    from ..doctrine.config import DOCTRINE_CONFIG

    if progress_callback:
        progress_callback({"message": "Syncing assets"})
    # Same fetcher extra as the scheduler's esi_data_sync: without it the
    # contracts fetcher would write every item exchange anywhere into
    # Doctrine's contract snapshot and fetch all their items.
    structure_id = DOCTRINE_CONFIG.effective_structure_id
    extra = {"structure_id": structure_id} if structure_id is not None else None
    result = do_sync_kinds(ASSET_KINDS, tool_keys=allowed, extra=extra)
    invalidate_cache(tenant_id)
    if progress_callback:
        progress_callback({"message": "Pricing"})
    warm_cache()
    return result


def warm_cache() -> bool:
    """Loads and prices the current tenant's rows so the first page open does
    not pay for it: a cold load priced ~3,800 types and ~180 blueprint copies
    and took about 22 s on the 1-vCPU test server (the invention estimates are
    then cached for six hours, the rows for `_CACHE_TTL`). Never raises."""
    try:
        grants, allowed, scope = _context()
        _rows(grants, allowed, scope)
        return True
    except Exception:  # noqa: BLE001 - a warm-up must never break its caller
        log.exception("asset manager warm-up failed")
        return False


def warm_all_tenants() -> int:
    """Warm-up at process start: every active tenant that holds the tool and
    shares something with it. Returns how many tenants were warmed."""
    from .. import tenant_eligibility, tenant_scope

    warmed = 0
    try:
        last_active = storage.list_tenant_last_active()
        tenants = [str(t[0]) for t in storage.list_tenants()]
    except Exception:  # noqa: BLE001
        log.exception("asset manager warm-up could not list tenants")
        return 0
    for tenant_id in tenants:
        try:
            if not tenant_eligibility.is_active(tenant_id, last_active):
                continue
            with tenant_scope.enter_tenant(tenant_id):
                _grants, _allowed, scope = _context()
                if not may_use(TOOL_KEY, _grants) or not any(c or o for c, o in scope.values()):
                    continue
                warmed += 1 if warm_cache() else 0
        except Exception:  # noqa: BLE001
            log.exception("asset manager warm-up failed for tenant %s", tenant_id)
    return warmed


def do_start_sync() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.start_job(TOOL_KEY, "sync", "Sync assets", lambda cb: do_sync(progress_callback=cb))


def do_set_location_name(location_id: int, name: str) -> dict:
    """Names a location for this tenant (locations.actions) and drops the
    cached rows so the next page shows it."""
    from ..locations import actions as location_actions

    result = location_actions.do_set_manual_name(location_id, name)
    invalidate_cache(storage.get_current_tenant())
    return result


def do_remove_location_name(location_id: int) -> dict:
    from ..locations import actions as location_actions

    result = location_actions.do_remove_manual_name(location_id)
    invalidate_cache(storage.get_current_tenant())
    return result


def do_retry_location(location_id: int) -> dict:
    """Looks one location up again now (locations.actions) and drops the
    cached rows."""
    from ..locations import actions as location_actions

    result = location_actions.do_retry_lookup(location_id)
    invalidate_cache(storage.get_current_tenant())
    return result


def do_sync_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(TOOL_KEY, "sync")


# ------------------------------------------------------------ phase 3 / 3b
def _filtered(params: dict) -> tuple[Optional[set[str]], list[dict]]:
    q = _query_from({**params, "page": 1, "page_size": query.MAX_PAGE_SIZE})
    grants, allowed, scope = _context()
    rows = _rows(grants, allowed, scope)
    return grants, [r for r in rows if query.matches(r, q)]


def do_locations(**params) -> dict:
    """Region -> system -> location summary of the filtered rows."""
    from . import tree
    _grants, rows = _filtered(params)
    return {"regions": tree.location_summary(rows)}


def do_location_tree(location_id: int, **params) -> dict:
    """Owner -> hangar -> container -> item tree for one location (0 = no
    location) over the filtered rows."""
    from . import tree
    _grants, rows = _filtered({**params, "location_id": None, "region_id": None, "system_id": None})
    return {"location_id": location_id or None, "nodes": tree.location_tree(rows, location_id)}


def do_ship(owner_type: str, owner_id: int, item_id: int) -> dict:
    """One assembled ship: fitting/holds by section, EFT text and - with the
    `doctrine` grant - how well each active fitting of that hull matches
    (F-1). Reads Doctrine's fittings through storage and its pure scorer;
    no Doctrine state is written."""
    from . import tree
    grants, allowed, scope = _context()
    rows = _rows(grants, allowed, scope)
    found = tree.ship_contents(rows, owner_type, int(owner_id), int(item_id))
    if found is None:
        raise ActionError("That ship is not in the assets this page may read.")
    ship = found["ship"]
    if ship.get("category_id") != 6:
        raise ActionError("That item is not a ship.")
    sections = []
    from .labels import SHIP_SECTIONS
    for label in SHIP_SECTIONS:
        items = found["sections"].get(label) or []
        if not items:
            continue
        sections.append({
            "label": label,
            "items": [query.public_row(r) for r in sorted(items, key=lambda r: (r.get("flag") or "", r["type_name"]))],
        })
    contents_value = sum(r["value"] for r in found["inside"] if r.get("value") is not None)
    result = {
        "ship": query.public_row(ship),
        "sections": sections,
        "eft": tree.eft_text(ship, found["direct"]),
        "contents_value": contents_value,
        "total_value": contents_value + (ship.get("value") or 0.0),
        "doctrine_visible": may_use("doctrine", grants),
        "doctrine_matches": [],
    }
    if result["doctrine_visible"]:
        result["doctrine_matches"] = _doctrine_matches(ship["type_id"], tree.contents_multiset(found["inside"]))
    return result


def _doctrine_matches(hull_type_id: int, ist: dict[int, float]) -> list[dict]:
    from ..doctrine.models import FittingItem
    from ..doctrine.validation import build_contract_soll, clears_match_threshold, match_score

    doctrine_names: dict[str, str] = {}
    out = []
    for f in storage.list_active_fittings():
        fitting_id, doctrine_id, name, variant, hull = str(f[0]), str(f[1]), f[2], f[3], f[4]
        if int(hull) != int(hull_type_id):
            continue
        items = [
            FittingItem(fitting_id=fitting_id, line_no=line_no, slot_section=section,
                        type_id=int(type_id), quantity=float(qty), is_offline=bool(offline))
            for line_no, section, type_id, qty, offline in storage.load_fitting_items(fitting_id)
        ]
        exact, consume = build_contract_soll(items)
        score = match_score(exact, consume, ist)
        missing = {t: q - ist.get(t, 0.0) for t, q in exact.items() if ist.get(t, 0.0) < q}
        type_names = storage.sde_type_names(missing)
        if doctrine_id not in doctrine_names:
            doc = storage.get_doctrine(doctrine_id)
            doctrine_names[doctrine_id] = doc[1] if doc else doctrine_id
        out.append({
            "fitting_id": fitting_id, "fitting_name": name, "variant_label": variant,
            "doctrine_name": doctrine_names[doctrine_id], "score": round(score, 3),
            "matches": clears_match_threshold(score),
            "missing": [{"type_id": t, "type_name": type_names.get(t, f"Type {t}"), "quantity": q}
                        for t, q in sorted(missing.items())],
        })
    out.sort(key=lambda m: -m["score"])
    return out


# ------------------------------------------------------------ phase 5
_VIEW_KEYS = (
    "q", "owners", "region_id", "system_id", "location_id", "hangar", "container_item_id",
    "category_ids", "group_ids", "type_ids", "sources", "hide_badges", "stock_only",
    "group_by", "sort", "desc", "page_size",
)


def _clean_view_query(raw) -> dict:
    """Only known /items parameters survive, and they must validate - a view
    saved by an older version that no longer validates is reported, never a
    500 (F-3)."""
    if not isinstance(raw, dict):
        raise ActionError("A saved view must be an object of /items parameters.")
    cleaned = {k: raw[k] for k in _VIEW_KEYS if k in raw and raw[k] not in (None, "", [])}
    _query_from(cleaned)
    return cleaned


def do_list_views() -> dict:
    views = []
    for view_id, name, raw, updated_at in storage.list_asset_manager_views():
        try:
            params, error = _clean_view_query(raw), None
        except ActionError as e:
            params, error = {}, str(e)
        views.append({"id": view_id, "name": name, "query": params, "updated_at": updated_at, "error": error})
    return {"views": views}


def do_save_view(name: str, query_params: dict) -> dict:
    import json
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise ActionError("A view name needs 1 to 80 characters.")
    cleaned = _clean_view_query(query_params)
    view_id = storage.save_asset_manager_view(name, json.dumps(cleaned))
    return {"id": view_id, "name": name, "query": cleaned}


def do_delete_view(view_id: int) -> dict:
    if not storage.delete_asset_manager_view(view_id):
        raise ActionError("That saved view does not exist.")
    return {"deleted": view_id}


def wanted_by(grants: Optional[set[str]]) -> dict[int, list[dict]]:
    """type_id -> which tool wants it (F-4), only for tools the session
    holds. Plain storage reads of configured demand (stock targets, the last
    buy list, active doctrine stockpiles, the Trading shortlist, mineral
    targets) - no planner run; cached for the row-cache TTL."""
    key = (storage.get_current_tenant(), None if grants is None else frozenset(grants))
    now = time.time()
    with _cache_guard:
        hit = _wanted_cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL:
            return hit[1]
    out = _load_wanted_by(grants)
    with _cache_guard:
        for old in [k for k, (at, _v) in _wanted_cache.items() if now - at >= _CACHE_TTL]:
            _wanted_cache.pop(old, None)
        _wanted_cache[key] = (now, out)
    return out


def _load_wanted_by(grants: Optional[set[str]]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}

    def add(type_id, tool, label, qty):
        out.setdefault(int(type_id), []).append({"tool": tool, "label": label, "quantity": qty})

    if may_use("production", grants):
        for type_id, _name, backup, home, jita in storage.load_stock_targets():
            total = sum(v or 0 for v in (backup, home, jita))
            add(type_id, "production", "Stock target", total)
        for type_id, qty in storage.load_latest_buy_list().items():
            add(type_id, "production", "Buy list", qty)
    if may_use("doctrine", grants):
        stockpile: dict[int, float] = {}
        for f in storage.list_active_fittings():
            sets = int(f[7] or 0)
            if sets <= 0:
                continue
            stockpile[int(f[4])] = stockpile.get(int(f[4]), 0.0) + sets
            for _line, _section, type_id, qty, _off in storage.load_fitting_items(str(f[0])):
                stockpile[int(type_id)] = stockpile.get(int(type_id), 0.0) + float(qty) * sets
        for type_id, qty in stockpile.items():
            add(type_id, "doctrine", "Doctrine stockpile", qty)
    if may_use("trading", grants):
        for item in storage.load_shortlist():
            if item.active and item.item_id:
                add(item.item_id, "trading", "Trading shortlist", None)
    if may_use("refining", grants):
        for type_id, _name, qty in storage.load_mineral_requirements():
            if qty and qty > 0:
                add(type_id, "refining", "Mineral target", qty)
    return out


def do_type_detail(type_id: int) -> dict:
    """Everything about one owned type (F-5): every stack, totals, which
    tool wants it and a 90-day price history at the Production hub region
    (Goonmetrics where covered, ESI daily history otherwise; empty when
    both are unavailable - never a guess)."""
    grants, allowed, scope = _context()
    rows = [r for r in _rows(grants, allowed, scope) if r["type_id"] == int(type_id)]
    if not rows:
        raise ActionError("None of that type is in the assets this page may read.")
    by_place = query.sort_rows(query.group(rows, "type_location"), "value", True)
    return {
        "type_id": int(type_id),
        "type_name": rows[0]["type_name"],
        "group_name": rows[0].get("group_name"),
        "category_name": rows[0].get("category_name"),
        "unit_price": next((r["unit_price"] for r in rows if r.get("unit_price") is not None), None),
        "totals": query.totals(rows),
        "places": by_place,
        "stacks": [query.public_row(r) for r in sorted(rows, key=lambda r: -(r.get("quantity") or 0))][:500],
        "wanted_by": wanted_by(grants).get(int(type_id), []),
        "price_history": _price_history(int(type_id)),
    }


def _price_history(type_id: int) -> dict:
    from datetime import date, timedelta

    from ..goonmetrics_client import GoonmetricsClient
    from ..production.config import PRODUCTION_CONFIG

    region_id = int(PRODUCTION_CONFIG.hub_region_id or 0) or 10000002
    since = (date.today() - timedelta(days=90)).isoformat()
    try:
        points = GoonmetricsClient().price_history(region_id, [type_id], esi_for_missing={type_id})
    except Exception:  # noqa: BLE001 - history is optional on this page
        import logging
        logging.getLogger(__name__).warning("asset manager: price history failed for %s", type_id, exc_info=True)
        points = []
    series = sorted(
        ({"date": p.date[:10], "avg_price": p.avg_price, "volume": p.movement}
         for p in points if p.type_id == type_id and p.date[:10] >= since and p.avg_price > 0),
        key=lambda p: p["date"],
    )
    return {"region_id": region_id, "points": series}


# ------------------------------------------------------------ phase 6
# Tenants whose snapshot wrote no rows today (nothing shared): without this the
# scheduler would find the snapshot due again on every tick.
_history_empty_day: dict[str, object] = {}


def history_snapshot_due() -> bool:
    from datetime import date
    today = date.today()
    if _history_empty_day.get(storage.get_current_tenant()) == today:
        return False
    latest = storage.latest_asset_history_date()
    return latest is None or latest < today


def take_history_snapshot() -> dict:
    """Writes today's per-owner/location/type ESI quantities and prunes days
    older than `asset_history_retention_days`. Called by the scheduler's
    asset_history job and lazily by the first history read of the day."""
    from datetime import date, timedelta

    from ..config import TRADING_CONFIG

    _grants, _allowed, scope = _context()
    today = date.today()
    # Quantities only: no pricing, no other sources (a daily job must not
    # hit Goonmetrics or the invention-cost path just to count items).
    written = storage.write_asset_history(today, collect.history_rows(scope))
    if written == 0:
        _history_empty_day[storage.get_current_tenant()] = today
    else:
        _history_empty_day.pop(storage.get_current_tenant(), None)
    keep_days = max(1, int(TRADING_CONFIG.asset_history_retention_days or 90))
    pruned = storage.prune_asset_history(today - timedelta(days=keep_days))
    return {"snapshot_date": today.isoformat(), "rows": written, "pruned": pruned}


def do_history(from_date: Optional[str] = None, to_date: Optional[str] = None,
               by_owner: bool = False, limit: int = 500) -> dict:
    """Quantity changes between two history days (default: the newest day
    and the day before it), valued at today's prices."""
    from datetime import date

    from . import insights

    if history_snapshot_due():
        take_history_snapshot()
    dates = storage.asset_history_dates()
    if not dates:
        return {"dates": [], "from_date": None, "to_date": None, "rows": []}
    try:
        to_day = date.fromisoformat(to_date) if to_date else dates[-1]
        older = [d for d in dates if d < to_day]
        from_day = date.fromisoformat(from_date) if from_date else (older[-1] if older else to_day)
    except ValueError as e:
        raise ActionError("Dates must look like YYYY-MM-DD.") from e
    if from_day not in dates or to_day not in dates:
        raise ActionError("No history for that day.")
    changes = insights.diff(storage.load_asset_history(from_day), storage.load_asset_history(to_day),
                            by_owner=by_owner)
    grants, allowed, scope = _context()
    rows = _rows(grants, allowed, scope)
    prices = {r["type_id"]: r["unit_price"] for r in rows if r.get("unit_price") is not None}
    types = storage.asset_manager_type_info({c["type_id"] for c in changes})
    places = storage.asset_manager_location_details({c["location_id"] for c in changes if c["location_id"]})
    for c in changes:
        c["type_name"] = (types.get(c["type_id"]) or {}).get("type_name") or f"Type {c['type_id']}"
        place = places.get(c["location_id"]) or {}
        c["location_name"] = place.get("name")
        c["solar_system_name"] = place.get("solar_system_name")
        price = prices.get(c["type_id"])
        c["value_delta"] = price * c["delta"] if price is not None else None
    changes.sort(key=lambda c: -abs(c["value_delta"] or 0))
    return {
        "dates": [d.isoformat() for d in dates],
        "from_date": from_day.isoformat(),
        "to_date": to_day.isoformat(),
        "rows": changes[:limit],
        "total_rows": len(changes),
        "arrived_value": sum(c["value_delta"] for c in changes if (c["value_delta"] or 0) > 0),
        "left_value": sum(c["value_delta"] for c in changes if (c["value_delta"] or 0) < 0),
    }


# ------------------------------------------------------------ phase 7
def do_insights(min_value: float = 10_000_000.0) -> dict:
    """Consolidation candidates, forgotten assets and Asset Safety."""
    from ..config import TRADING_CONFIG
    from ..production.config import PRODUCTION_CONFIG
    from . import insights

    grants, allowed, scope = _context()
    rows = _rows(grants, allowed, scope)
    home = PRODUCTION_CONFIG.home_location_id
    used = {loc for loc in (home, TRADING_CONFIG.structure_id) if loc}
    wanted = set(wanted_by(grants))
    return {
        "home_location_id": home,
        "haul_cost_per_m3": float(PRODUCTION_CONFIG.haul_cost_per_m3 or 0.0),
        "min_value": min_value,
        "consolidation": insights.consolidation(rows, home, float(PRODUCTION_CONFIG.haul_cost_per_m3 or 0.0)),
        "forgotten": insights.forgotten(rows, used, wanted, float(min_value)),
        "asset_safety": insights.asset_safety(rows),
    }
