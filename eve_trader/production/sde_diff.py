"""Diff a fetched Fuzzwork dump against the current sde_* cache.

Used by the Admin SDE preview so Apply is an explicit second step. Item
comparison includes unpublished types - a published-only filter would hide
exactly the leftover/test blueprints that have bitten this tool before.
"""
from __future__ import annotations

import struct

from .constants import ACTIVITY_COPYING, ACTIVITY_INVENTION, ACTIVITY_MANUFACTURING, ACTIVITY_REACTION
from .sde import FetchedSde

_ITEM_COMPARE_FIELDS = (
    ("name", 2),
    ("volume", 3),
    ("published", 4),
    ("meta_group_id", 7),
)

_PRIMARY_PRODUCT_ACTIVITIES = (ACTIVITY_MANUFACTURING, ACTIVITY_REACTION)
_TIME_ACTIVITY_ORDER = (
    ACTIVITY_MANUFACTURING, ACTIVITY_REACTION, ACTIVITY_INVENTION, ACTIVITY_COPYING,
)

def _pg_real(value):
    """Round-trip through Postgres REAL (float4). sde_* float columns are
    REAL, so a CSV value like 65449847 comes back as 65449848 after apply;
    comparing the raw Python float would flag a phantom change."""
    if value is None:
        return None
    return struct.unpack("f", struct.pack("f", float(value)))[0]


def _floats_differ(old, new) -> bool:
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return _pg_real(old) != _pg_real(new)


def _rows(snapshot: dict, key: str) -> list:
    return list(snapshot.get(key) or ())


def _type_name(type_id: int, new_types: dict, old_types: dict) -> str:
    row = new_types.get(type_id) or old_types.get(type_id)
    if row is None:
        return str(type_id)
    return row[2]


def _primary_product(products: dict[tuple[int, int], tuple[int, float]], bp_id: int):
    for activity_id in _PRIMARY_PRODUCT_ACTIVITIES:
        found = products.get((bp_id, activity_id))
        if found is not None:
            return found
    matches = [(act, prod) for (bid, act), prod in products.items() if bid == bp_id]
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def _key_str(key) -> str:
    if isinstance(key, tuple):
        return ":".join(str(part) for part in key)
    return str(key)


def _diff_rows(old_rows, new_rows, key_fn, name_fn, fields) -> dict:
    """new/removed/changed for one table. `fields` is (name, index, is_float).
    A row in both snapshots with equal fields is omitted."""
    old_by_key = {key_fn(row): row for row in old_rows}
    new_by_key = {key_fn(row): row for row in new_rows}
    new = []
    removed = []
    changed = []
    for key, row in new_by_key.items():
        label = name_fn(row)
        key_s = _key_str(key)
        if key not in old_by_key:
            new.append({"key": key_s, "name": label})
            continue
        old = old_by_key[key]
        changes = {}
        for field_name, idx, is_float in fields:
            old_val, new_val = old[idx], row[idx]
            if is_float:
                if _floats_differ(old_val, new_val):
                    changes[field_name] = [old_val, new_val]
            elif old_val != new_val:
                changes[field_name] = [old_val, new_val]
        if changes:
            changed.append({"key": key_s, "name": label, "changes": changes})
    for key, row in old_by_key.items():
        if key not in new_by_key:
            removed.append({"key": _key_str(key), "name": name_fn(row)})
    return {"new": new, "removed": removed, "changed": changed}


def _other_tables(fetched: FetchedSde, snapshot: dict, new_types: dict, old_types: dict) -> dict:
    """Row diffs for the eight tables that are not types or blueprint
    materials/products/time. Invention probability is also summarized per
    blueprint inside changed_blueprints; both views are kept."""
    def type_label(type_id: int) -> str:
        return _type_name(int(type_id), new_types, old_types)

    specs = (
        (
            "sde_groups", fetched.groups,
            lambda row: int(row[0]), lambda row: row[2],
            (("category_id", 1, False), ("group_name", 2, False)),
        ),
        (
            "sde_market_groups", fetched.market_groups,
            lambda row: int(row[0]), lambda row: row[2],
            (("parent_group_id", 1, False), ("market_group_name", 2, False)),
        ),
        (
            "sde_solar_systems", fetched.solar_systems,
            lambda row: int(row[0]), lambda row: row[1],
            (("name", 1, False), ("security", 2, True), ("region_id", 3, False)),
        ),
        (
            "sde_stations", fetched.stations,
            lambda row: int(row[0]), lambda row: row[2],
            (("system_id", 1, False), ("station_name", 2, False)),
        ),
        (
            "sde_categories", fetched.categories,
            lambda row: int(row[0]), lambda row: row[1],
            (("category_name", 1, False),),
        ),
        (
            "sde_type_slots", fetched.type_slots,
            lambda row: int(row[0]), lambda row: type_label(row[0]),
            (("slot", 1, False),),
        ),
        (
            "sde_type_materials", fetched.type_materials,
            lambda row: (int(row[0]), int(row[1])),
            lambda row: f"{type_label(row[0])} → {type_label(row[1])}",
            (("quantity", 2, True),),
        ),
        (
            "sde_invention_probability", fetched.invention_probability,
            lambda row: (int(row[0]), int(row[1])),
            lambda row: f"{type_label(row[0])} → {type_label(row[1])}",
            (("probability", 2, True),),
        ),
    )
    return {
        table: _diff_rows(_rows(snapshot, table), new_rows, key_fn, name_fn, fields)
        for table, new_rows, key_fn, name_fn, fields in specs
    }


def _blueprint_ids(*row_groups: list) -> set[int]:
    ids: set[int] = set()
    for rows in row_groups:
        for row in rows:
            if row:
                ids.add(int(row[0]))
    return ids


def build_diff(fetched: FetchedSde, snapshot: dict) -> dict:
    old_type_rows = _rows(snapshot, "sde_types")
    old_types = {int(r[0]): r for r in old_type_rows}
    new_types = {int(r[0]): r for r in fetched.types}

    new_items = []
    removed_items = []
    changed_items = []
    for type_id, row in new_types.items():
        if type_id not in old_types:
            new_items.append({"type_id": type_id, "name": row[2]})
            continue
        old = old_types[type_id]
        changes = {}
        for field_name, idx in _ITEM_COMPARE_FIELDS:
            old_val, new_val = old[idx], row[idx]
            if field_name == "volume":
                if _floats_differ(old_val, new_val):
                    changes[field_name] = [old_val, new_val]
            elif old_val != new_val:
                changes[field_name] = [old_val, new_val]
        if changes:
            changed_items.append({"type_id": type_id, "name": row[2], "changes": changes})
    for type_id, row in old_types.items():
        if type_id not in new_types:
            removed_items.append({"type_id": type_id, "name": row[2]})

    old_materials = {
        (int(r[0]), int(r[1]), int(r[2])): float(r[3])
        for r in _rows(snapshot, "sde_blueprint_materials")
    }
    new_materials = {
        (int(r[0]), int(r[1]), int(r[2])): float(r[3])
        for r in fetched.blueprint_materials
    }
    old_products = {
        (int(r[0]), int(r[1])): (int(r[2]), float(r[3]))
        for r in _rows(snapshot, "sde_blueprint_products")
    }
    new_products = {
        (int(r[0]), int(r[1])): (int(r[2]), float(r[3]))
        for r in fetched.blueprint_products
    }
    old_time = {
        (int(r[0]), int(r[1])): float(r[2])
        for r in _rows(snapshot, "sde_blueprint_time")
    }
    new_time = {
        (int(r[0]), int(r[1])): float(r[2])
        for r in fetched.blueprint_time
    }
    old_prob = {
        (int(r[0]), int(r[1])): float(r[2])
        for r in _rows(snapshot, "sde_invention_probability")
    }
    new_prob = {
        (int(r[0]), int(r[1])): float(r[2])
        for r in fetched.invention_probability
    }

    changed_blueprints = []
    bp_ids = _blueprint_ids(
        _rows(snapshot, "sde_blueprint_materials"),
        fetched.blueprint_materials,
        _rows(snapshot, "sde_blueprint_products"),
        fetched.blueprint_products,
        _rows(snapshot, "sde_blueprint_time"),
        fetched.blueprint_time,
        _rows(snapshot, "sde_invention_probability"),
        fetched.invention_probability,
    )
    for bp_id in bp_ids:
        # New/removed blueprint types already appear under items; don't
        # double-count their recipes as "changed".
        if bp_id not in old_types or bp_id not in new_types:
            continue

        materials = []
        mat_keys = {k for k in old_materials if k[0] == bp_id} | {k for k in new_materials if k[0] == bp_id}
        for key in sorted(mat_keys, key=lambda k: (k[1], k[2])):
            old_qty = old_materials.get(key)
            new_qty = new_materials.get(key)
            if old_qty is None and new_qty is None:
                continue
            if old_qty is not None and new_qty is not None and not _floats_differ(old_qty, new_qty):
                continue
            materials.append({
                "material_type_id": key[2],
                "name": _type_name(key[2], new_types, old_types),
                "old_qty": 0.0 if old_qty is None else old_qty,
                "new_qty": 0.0 if new_qty is None else new_qty,
            })

        old_product = _primary_product(old_products, bp_id)
        new_product = _primary_product(new_products, bp_id)
        products = None
        if old_product is not None and new_product is not None:
            old_pid, old_qty = old_product
            new_pid, new_qty = new_product
            if old_pid != new_pid or _floats_differ(old_qty, new_qty):
                products = {"old_qty": old_qty, "new_qty": new_qty}
        elif old_product is not None or new_product is not None:
            old_qty = 0.0 if old_product is None else old_product[1]
            new_qty = 0.0 if new_product is None else new_product[1]
            products = {"old_qty": old_qty, "new_qty": new_qty}

        time_change = None
        for activity_id in _TIME_ACTIVITY_ORDER:
            old_t = old_time.get((bp_id, activity_id))
            new_t = new_time.get((bp_id, activity_id))
            if old_t is None and new_t is None:
                continue
            if old_t is not None and new_t is not None and not _floats_differ(old_t, new_t):
                continue
            time_change = {"old": old_t, "new": new_t}
            break

        invention_probability = None
        pids = {pid for (bid, pid) in old_prob if bid == bp_id} | {
            pid for (bid, pid) in new_prob if bid == bp_id
        }
        for pid in sorted(pids):
            old_p = old_prob.get((bp_id, pid))
            new_p = new_prob.get((bp_id, pid))
            if old_p is None and new_p is None:
                continue
            if old_p is not None and new_p is not None and not _floats_differ(old_p, new_p):
                continue
            invention_probability = {"old": old_p, "new": new_p}
            break

        if not materials and products is None and time_change is None and invention_probability is None:
            continue

        product_type_id = 0
        if new_product is not None:
            product_type_id = new_product[0]
        elif old_product is not None:
            product_type_id = old_product[0]
        product_name = _type_name(product_type_id, new_types, old_types) if product_type_id else _type_name(
            bp_id, new_types, old_types,
        )
        changed_blueprints.append({
            "blueprint_type_id": bp_id,
            "product_type_id": product_type_id,
            "product_name": product_name,
            "materials": materials,
            "products": products,
            "time": time_change,
            "invention_probability": invention_probability,
        })

    return {
        "new_items": new_items,
        "removed_items": removed_items,
        "changed_items": changed_items,
        "changed_blueprints": changed_blueprints,
        "other_tables": _other_tables(fetched, snapshot, new_types, old_types),
    }
