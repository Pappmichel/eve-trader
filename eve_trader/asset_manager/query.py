"""Filter, group, sort and page Asset Manager rows - pure, no I/O.

Totals always cover the whole filter, never just the page
(docs/ASSET_MANAGER_PLAN.md D8 / R-2): rows are priced before they are
filtered and paged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SOURCES = ("esi", "manual", "listed", "contract", "in_production")
GROUP_MODES = ("none", "type", "type_location")
SORT_KEYS = (
    "value", "quantity", "volume", "unit_price", "type_name", "location_name",
    "owner_name", "category_name",
)
MAX_PAGE_SIZE = 500

# Fields every API row carries. Internal enrichment helpers are dropped.
ROW_FIELDS = (
    "key", "source", "owner_type", "owner_id", "owner_name", "item_id", "item_name", "type_id",
    "type_name", "group_id", "group_name", "category_id", "category_name", "quantity",
    "location_id", "location_name", "location_kind", "solar_system_id", "solar_system_name",
    "security", "region_id", "region_name", "flag", "hangar", "container_path",
    "parent_item_id", "is_singleton", "badges", "stock", "me", "te", "runs", "unit_volume",
    "volume", "unit_price", "value", "job_end",
)


@dataclass(frozen=True)
class ItemQuery:
    q: str = ""
    owners: tuple[str, ...] = ()          # "character:123", "corporation:9", "manual:0"
    region_id: Optional[int] = None
    system_id: Optional[int] = None
    location_id: Optional[int] = None
    hangar: Optional[str] = None
    container_item_id: Optional[int] = None
    category_ids: tuple[int, ...] = ()
    group_ids: tuple[int, ...] = ()
    type_ids: tuple[int, ...] = ()
    sources: tuple[str, ...] = ()         # empty = every source
    hide_badges: tuple[str, ...] = ()
    stock_only: bool = False
    group_by: str = "type_location"
    sort: str = "value"
    desc: bool = True
    page: int = 1
    page_size: int = 100


class QueryError(ValueError):
    """A parameter outside the documented vocabulary."""


def validate(query: ItemQuery) -> None:
    if query.group_by not in GROUP_MODES:
        raise QueryError(f"group_by must be one of {', '.join(GROUP_MODES)}")
    if query.sort not in SORT_KEYS:
        raise QueryError(f"sort must be one of {', '.join(SORT_KEYS)}")
    unknown = set(query.sources) - set(SOURCES)
    if unknown:
        raise QueryError(f"unknown source(s): {', '.join(sorted(unknown))}")
    if query.page < 1:
        raise QueryError("page starts at 1")
    if not 1 <= query.page_size <= MAX_PAGE_SIZE:
        raise QueryError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    for owner in query.owners:
        owner_type, _, owner_id = owner.partition(":")
        if owner_type not in ("character", "corporation", "manual") or not owner_id.lstrip("-").isdigit():
            raise QueryError(f"owner must look like character:<id>, corporation:<id> or manual:0, got {owner!r}")


def _haystack(row: dict) -> str:
    parts = [
        row.get("type_name"), row.get("item_name"), row.get("location_name"),
        row.get("solar_system_name"), row.get("region_name"), row.get("owner_name"),
        row.get("group_name"), row.get("category_name"), row.get("hangar"),
        *(row.get("container_path") or []),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def matches(row: dict, query: ItemQuery) -> bool:
    if query.sources and row["source"] not in query.sources:
        return False
    if query.stock_only and not row.get("stock"):
        return False
    if query.hide_badges and set(row.get("badges") or ()) & set(query.hide_badges):
        return False
    if query.owners and f"{row['owner_type']}:{row['owner_id']}" not in query.owners:
        return False
    if query.region_id is not None and row.get("region_id") != query.region_id:
        return False
    if query.system_id is not None and row.get("solar_system_id") != query.system_id:
        return False
    if query.location_id is not None and (row.get("location_id") or 0) != query.location_id:
        return False
    if query.hangar is not None and row.get("hangar") != query.hangar:
        return False
    if query.container_item_id is not None and query.container_item_id not in (row.get("ancestors") or ()):
        return False
    if query.category_ids and row.get("category_id") not in query.category_ids:
        return False
    if query.group_ids and row.get("group_id") not in query.group_ids:
        return False
    if query.type_ids and row.get("type_id") not in query.type_ids:
        return False
    tokens = query.q.lower().split()
    if tokens:
        hay = _haystack(row)
        if not all(t in hay for t in tokens):
            return False
    return True


def public_row(row: dict) -> dict:
    return {k: row.get(k) for k in ROW_FIELDS}


def _add(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None:
        return b
    if b is None:
        return a
    return a + b


def group(rows: list[dict], mode: str) -> list[dict]:
    """`none` keeps one row per stack. `type` sums per type across every
    owner and place; `type_location` per type and location. A group's
    value is the sum of its priced rows; `unpriced` counts the rest."""
    if mode == "none":
        out = []
        for r in rows:
            item = public_row(r)
            item["stacks"] = 1
            item["unpriced"] = 0 if r.get("value") is not None else 1
            item["owners"] = [r["owner_name"]] if r.get("owner_name") else []
            item["sources"] = [r["source"]]
            out.append(item)
        return out
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["type_id"],) if mode == "type" else (r["type_id"], r.get("location_id") or 0)
        g = groups.get(key)
        if g is None:
            g = {
                "key": f"{mode}:" + ":".join(str(k) for k in key),
                "type_id": r["type_id"], "type_name": r["type_name"],
                "group_id": r.get("group_id"), "group_name": r.get("group_name"),
                "category_id": r.get("category_id"), "category_name": r.get("category_name"),
                "quantity": 0.0, "volume": None, "value": None, "unit_price": r.get("unit_price"),
                "stacks": 0, "unpriced": 0, "owners": set(), "sources": set(), "badges": set(),
                "locations": set(),
            }
            if mode == "type_location":
                for k in ("location_id", "location_name", "location_kind", "solar_system_id",
                          "solar_system_name", "security", "region_id", "region_name"):
                    g[k] = r.get(k)
            groups[key] = g
        g["quantity"] += r["quantity"]
        g["volume"] = _add(g["volume"], r.get("volume"))
        g["value"] = _add(g["value"], r.get("value"))
        if r.get("value") is None:
            g["unpriced"] += 1
        if g["unit_price"] is None:
            g["unit_price"] = r.get("unit_price")
        g["stacks"] += 1
        if r.get("owner_name"):
            g["owners"].add(r["owner_name"])
        g["sources"].add(r["source"])
        g["badges"].update(r.get("badges") or ())
        g["locations"].add(r.get("location_id") or 0)
    out = []
    for g in groups.values():
        g["owners"] = sorted(g["owners"])
        g["sources"] = sorted(g["sources"])
        g["badges"] = sorted(g["badges"])
        g["location_count"] = len(g.pop("locations"))
        out.append(g)
    return out


def sort_rows(rows: list[dict], key: str, desc: bool) -> list[dict]:
    """Missing values (unpriced, unnamed) sort last in either direction."""
    def value(r):
        v = r.get(key)
        if key == "owner_name" and v is None:
            v = ", ".join(r.get("owners") or [])
        return v
    present = [r for r in rows if value(r) not in (None, "")]
    missing = [r for r in rows if value(r) in (None, "")]
    text = key in ("type_name", "location_name", "owner_name", "category_name")
    present.sort(key=lambda r: str(value(r)).lower() if text else value(r), reverse=desc)
    tie = sorted(missing, key=lambda r: str(r.get("type_name") or "").lower())
    return present + tie


def totals(rows: list[dict]) -> dict:
    return {
        "stacks": len(rows),
        "quantity": sum(r["quantity"] for r in rows),
        "volume": sum(r["volume"] for r in rows if r.get("volume") is not None),
        "value": sum(r["value"] for r in rows if r.get("value") is not None),
        "unpriced": sum(1 for r in rows if r.get("value") is None),
        "types": len({r["type_id"] for r in rows}),
        "locations": len({r.get("location_id") or 0 for r in rows}),
        "owners": len({(r["owner_type"], r["owner_id"]) for r in rows}),
    }


def facets(rows: list[dict]) -> dict:
    """Filter options from the unfiltered rows, so a dropdown never loses
    its other choices while one is selected."""
    owners: dict[str, dict] = {}
    regions: dict[int, dict] = {}
    systems: dict[int, dict] = {}
    locations: dict[int, dict] = {}
    categories: dict[int, dict] = {}
    groups_: dict[int, dict] = {}
    for r in rows:
        okey = f"{r['owner_type']}:{r['owner_id']}"
        owners.setdefault(okey, {"key": okey, "name": r.get("owner_name") or okey,
                                 "owner_type": r["owner_type"], "count": 0})["count"] += 1
        if r.get("region_id") is not None:
            regions.setdefault(r["region_id"], {"id": r["region_id"], "name": r.get("region_name"), "count": 0})["count"] += 1
        if r.get("solar_system_id") is not None:
            systems.setdefault(r["solar_system_id"], {
                "id": r["solar_system_id"], "name": r.get("solar_system_name"),
                "region_id": r.get("region_id"), "security": r.get("security"), "count": 0,
            })["count"] += 1
        loc = r.get("location_id") or 0
        locations.setdefault(loc, {
            "id": loc, "name": r.get("location_name"), "kind": r.get("location_kind"),
            "solar_system_id": r.get("solar_system_id"), "region_id": r.get("region_id"), "count": 0,
        })["count"] += 1
        if r.get("category_id") is not None:
            categories.setdefault(r["category_id"], {"id": r["category_id"], "name": r.get("category_name"), "count": 0})["count"] += 1
        if r.get("group_id") is not None:
            groups_.setdefault(r["group_id"], {
                "id": r["group_id"], "name": r.get("group_name"), "category_id": r.get("category_id"), "count": 0,
            })["count"] += 1

    def by_name(items):
        return sorted(items, key=lambda x: str(x.get("name") or "").lower())
    return {
        "owners": by_name(owners.values()),
        "regions": by_name(regions.values()),
        "systems": by_name(systems.values()),
        "locations": by_name(locations.values()),
        "categories": by_name(categories.values()),
        "groups": by_name(groups_.values()),
        "sources": sorted({r["source"] for r in rows}),
        "badges": sorted({b for r in rows for b in (r.get("badges") or ())}),
    }


def run(rows: list[dict], query: ItemQuery) -> dict:
    """The /items response body for already-loaded rows."""
    validate(query)
    filtered = [r for r in rows if matches(r, query)]
    grouped = sort_rows(group(filtered, query.group_by), query.sort, query.desc)
    start = (query.page - 1) * query.page_size
    return {
        "rows": grouped[start:start + query.page_size],
        "total_rows": len(grouped),
        "page": query.page,
        "page_size": query.page_size,
        "totals": totals(filtered),
        "facets": facets(rows),
    }
