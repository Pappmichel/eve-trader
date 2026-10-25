"""History diff and insights over loaded Asset Manager rows - pure, no I/O
(docs/ASSET_MANAGER_PLAN.md phases 6 and 7)."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional


def diff(before: list[tuple], after: list[tuple], *, by_owner: bool = False) -> list[dict]:
    """Quantity change per type and location (and owner when `by_owner`)
    between two history days. Only changed keys are returned."""
    def keyed(rows):
        out: dict[tuple, float] = defaultdict(float)
        for owner_type, owner_id, location_id, type_id, qty in rows:
            key = (type_id, location_id, owner_type, owner_id) if by_owner else (type_id, location_id)
            out[key] += qty
        return out
    a, b = keyed(before), keyed(after)
    out = []
    for key in set(a) | set(b):
        delta = b.get(key, 0.0) - a.get(key, 0.0)
        if abs(delta) < 1e-9:
            continue
        row = {"type_id": key[0], "location_id": key[1] or None, "before": a.get(key, 0.0),
               "after": b.get(key, 0.0), "delta": delta}
        if by_owner:
            row["owner_type"], row["owner_id"] = key[2], key[3]
        out.append(row)
    return out


def consolidation(rows: list[dict], home_location_id: Optional[int], haul_cost_per_m3: float,
                  limit: int = 100) -> list[dict]:
    """Types whose stock sits in more than one place: how much is outside
    the home structure, its m3 and the freight to bring it home at the
    configured haul rate. Stock rows only (no listed, contracted or
    assembled items)."""
    by_type: dict[int, dict] = {}
    for r in rows:
        if not r.get("stock") or r["source"] != "esi":
            continue
        t = by_type.setdefault(r["type_id"], {
            "type_id": r["type_id"], "type_name": r["type_name"], "category_name": r.get("category_name"),
            "locations": defaultdict(float), "quantity": 0.0, "outside_quantity": 0.0,
            "outside_volume": 0.0, "outside_value": 0.0, "unit_price": r.get("unit_price"),
        })
        loc = r.get("location_id") or 0
        t["locations"][loc] += r["quantity"]
        t["quantity"] += r["quantity"]
        if home_location_id is None or loc != home_location_id:
            t["outside_quantity"] += r["quantity"]
            t["outside_volume"] += r.get("volume") or 0.0
            t["outside_value"] += r.get("value") or 0.0
    out = []
    for t in by_type.values():
        if len(t["locations"]) < 2:
            continue
        t["location_count"] = len(t.pop("locations"))
        t["freight_cost"] = t["outside_volume"] * haul_cost_per_m3
        out.append(t)
    out.sort(key=lambda t: -t["outside_value"])
    return out[:limit]


def forgotten(rows: list[dict], used_locations: set[int], wanted: set[int], min_value: float,
              limit: int = 200) -> list[dict]:
    """Stock worth at least `min_value` per type and place, at a place no
    tool uses and of a type no tool wants (F-4)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        if not r.get("stock") or r["source"] != "esi" or r["type_id"] in wanted:
            continue
        loc = r.get("location_id") or 0
        if loc in used_locations:
            continue
        g = groups.setdefault((r["type_id"], loc), {
            "type_id": r["type_id"], "type_name": r["type_name"], "location_id": loc or None,
            "location_name": r.get("location_name"), "solar_system_name": r.get("solar_system_name"),
            "region_name": r.get("region_name"), "quantity": 0.0, "value": 0.0, "volume": 0.0, "owners": set(),
        })
        g["quantity"] += r["quantity"]
        g["value"] += r.get("value") or 0.0
        g["volume"] += r.get("volume") or 0.0
        if r.get("owner_name"):
            g["owners"].add(r["owner_name"])
    out = []
    for g in groups.values():
        if g["value"] < min_value:
            continue
        g["owners"] = sorted(g["owners"])
        out.append(g)
    out.sort(key=lambda g: -g["value"])
    return out[:limit]


def asset_safety(rows: list[dict]) -> list[dict]:
    """Items in Asset Safety per owner and place. ESI's asset list has no
    wrap date, so no timer is shown (plan phase 7)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        if "asset_safety" not in (r.get("badges") or ()):
            continue
        key = (r["owner_type"], r["owner_id"], r.get("location_id") or 0)
        g = groups.setdefault(key, {
            "owner_name": r.get("owner_name"), "location_id": key[2] or None,
            "location_name": r.get("location_name"), "solar_system_name": r.get("solar_system_name"),
            "region_name": r.get("region_name"), "stacks": 0, "value": 0.0, "unpriced": 0, "volume": 0.0,
        })
        g["stacks"] += 1
        if r.get("value") is None:
            g["unpriced"] += 1
        else:
            g["value"] += r["value"]
        g["volume"] += r.get("volume") or 0.0
    return sorted(groups.values(), key=lambda g: -g["value"])
