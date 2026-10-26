"""Location summary, per-location hierarchy and the ship view - pure, no I/O
(docs/ASSET_MANAGER_PLAN.md phase 3 / 3b, D9).

The hierarchy is rebuilt from the stored columns: `parent_item_id` is the
immediate container within the same owner, the outermost flag is the hangar.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..storage import OFFICE_TYPE_ID
from . import labels
from .query import _add

_VIRTUAL_LABELS = {
    "manual": "Manual stock",
    "listed": "Listed for sale",
    "contract": "In contracts",
    "in_production": "In production",
}


def _node(key: str, label: str, kind: str, **extra) -> dict:
    node = {
        "key": key, "label": label, "kind": kind, "quantity": None, "value": None, "volume": None,
        "unpriced": 0, "stacks": 0, "children": [],
    }
    node.update(extra)
    return node


def _roll_up(node: dict) -> None:
    """Totals of a node include all of its children (a container's value is
    its own plus everything inside)."""
    for child in node["children"]:
        _roll_up(child)
        node["value"] = _add(node["value"], child["value"])
        node["volume"] = _add(node["volume"], child["volume"])
        node["unpriced"] += child["unpriced"]
        node["stacks"] += child["stacks"]
    node["children"].sort(key=lambda c: (c["kind"] == "item", -(c["value"] or 0), c["label"].lower()))


def location_summary(rows: list[dict]) -> list[dict]:
    """Region -> system -> location with stacks, value, volume and owners.
    Places without a known system collect under "Unknown region"."""
    regions: dict = {}
    for r in rows:
        region_key = r.get("region_id") or 0
        system_key = r.get("solar_system_id") or 0
        loc_key = r.get("location_id") or 0
        region = regions.setdefault(region_key, _node(
            f"region:{region_key}", r.get("region_name") or "Unknown region", "region",
            region_id=r.get("region_id"), systems={}))
        system = region["systems"].setdefault(system_key, _node(
            f"system:{system_key}", r.get("solar_system_name") or "Unknown system", "system",
            solar_system_id=r.get("solar_system_id"), security=r.get("security"), locations={}))
        if loc_key:
            label = r.get("location_name") or f"Location {loc_key}"
        else:
            label = "No location"
        loc = system["locations"].setdefault(loc_key, _node(
            f"location:{loc_key}", label, "location", location_id=loc_key or None,
            location_kind=r.get("location_kind"), owners=set(), sources=set(), named=bool(r.get("location_name"))))
        loc["value"] = _add(loc["value"], r.get("value"))
        loc["volume"] = _add(loc["volume"], r.get("volume"))
        loc["unpriced"] += 0 if r.get("value") is not None else 1
        loc["stacks"] += 1
        if r.get("owner_name"):
            loc["owners"].add(r["owner_name"])
        loc["sources"].add(r["source"])
    out = []
    for region in regions.values():
        for system in region.pop("systems").values():
            for loc in system.pop("locations").values():
                loc["owners"] = sorted(loc["owners"])
                loc["sources"] = sorted(loc["sources"])
                system["children"].append(loc)
            region["children"].append(system)
        _roll_up(region)
        out.append(region)
    out.sort(key=lambda n: -(n["value"] or 0))
    return out


def _item_node(r: dict) -> dict:
    label = r["type_name"]
    if r.get("item_name"):
        label = f"{r['item_name']} ({r['type_name']})"
    is_ship = r.get("category_id") == 6 and r.get("is_singleton")
    is_container = r.get("is_singleton") and not is_ship
    return _node(
        r["key"], label, "ship" if is_ship else "container" if is_container else "item",
        quantity=r["quantity"], value=r.get("value"), volume=r.get("volume"),
        unpriced=0 if r.get("value") is not None else 1, stacks=1,
        item_id=r.get("item_id"), type_id=r["type_id"], owner_type=r["owner_type"],
        owner_id=r["owner_id"], badges=r.get("badges") or [], flag=r.get("flag"),
        section=None,
    )


def location_tree(rows: list[dict], location_id: Optional[int]) -> list[dict]:
    """Owner -> hangar -> containers/ships -> items for one location (0/None
    = rows without a location). Virtual sources get their own owner-level
    groups ("Listed for sale", ...)."""
    here = [r for r in rows if (r.get("location_id") or 0) == (location_id or 0)]
    owners: dict = {}
    item_nodes: dict[tuple, dict] = {}
    # A corp Office is a container in ESI's model; its contents are shown
    # under their division hangar instead (P-07).
    esi_rows = [r for r in here if r["source"] == "esi" and r["type_id"] != OFFICE_TYPE_ID]
    for r in esi_rows:
        item_nodes[(r["owner_type"], r["owner_id"], r["item_id"])] = _item_node(r)

    def owner_node(r):
        okey = (r["owner_type"], r["owner_id"])
        return owners.setdefault(okey, _node(
            f"owner:{r['owner_type']}:{r['owner_id']}", r.get("owner_name") or str(r["owner_id"]), "owner",
            owner_type=r["owner_type"], owner_id=r["owner_id"], hangars={}))

    for r in esi_rows:
        node = item_nodes[(r["owner_type"], r["owner_id"], r["item_id"])]
        parent = item_nodes.get((r["owner_type"], r["owner_id"], r.get("parent_item_id")))
        if parent is not None:
            if parent["kind"] == "ship":
                node["section"] = labels.ship_section(r.get("flag"))
            parent["children"].append(node)
            continue
        owner = owner_node(r)
        hangar_label = r.get("hangar") or "Unknown"
        hangar = owner["hangars"].setdefault(hangar_label, _node(
            f"{owner['key']}:hangar:{hangar_label}", hangar_label, "hangar"))
        hangar["children"].append(node)

    virtual: dict[str, dict] = {}
    for r in here:
        if r["source"] == "esi":
            continue
        group = virtual.setdefault(r["source"], _node(
            f"source:{r['source']}", _VIRTUAL_LABELS.get(r["source"], r["source"]), "source"))
        node = _node(r["key"], r["type_name"], "item", quantity=r["quantity"], value=r.get("value"),
                     volume=r.get("volume"), unpriced=0 if r.get("value") is not None else 1, stacks=1,
                     type_id=r["type_id"], owner_type=r["owner_type"], owner_id=r["owner_id"],
                     badges=r.get("badges") or [], owner_name=r.get("owner_name"))
        group["children"].append(node)

    out = []
    for owner in owners.values():
        owner["children"] = list(owner.pop("hangars").values())
        _roll_up(owner)
        out.append(owner)
    for group in virtual.values():
        _roll_up(group)
        out.append(group)
    out.sort(key=lambda n: (n["kind"] != "owner", -(n["value"] or 0)))
    return out


# ------------------------------------------------------------------ ship view
_EFT_ORDER = (
    ("Low slots", "LoSlot"), ("Mid slots", "MedSlot"), ("High slots", "HiSlot"),
    ("Rig slots", "RigSlot"), ("Subsystems", "SubSystemSlot"), ("Service slots", "ServiceSlot"),
)
CHARGE_CATEGORY_ID = 8


def ship_contents(rows: list[dict], owner_type: str, owner_id: int, item_id: int) -> Optional[dict]:
    """The ship row and everything inside it, sectioned by flag (F-1)."""
    ship = next((r for r in rows if r["source"] == "esi" and r["owner_type"] == owner_type
                 and r["owner_id"] == owner_id and r["item_id"] == item_id), None)
    if ship is None:
        return None
    inside = [r for r in rows if r["source"] == "esi" and r["owner_type"] == owner_type
              and r["owner_id"] == owner_id and item_id in (r.get("ancestors") or ())]
    direct = [r for r in inside if r.get("parent_item_id") == item_id]
    sections: dict[str, list[dict]] = defaultdict(list)
    for r in direct:
        sections[labels.ship_section(r.get("flag"))].append(r)
    return {"ship": ship, "inside": inside, "direct": direct, "sections": sections}


def eft_text(ship: dict, direct: list[dict]) -> str:
    """EFT block of an assembled ship from its direct contents. A loaded
    charge shares its module's slot flag and is appended as ", Charge"."""
    title = ship.get("item_name") or ship["type_name"]
    lines = [f"[{ship['type_name']}, {title}]"]
    for _label, prefix in _EFT_ORDER:
        slot_rows: dict[str, dict] = {}
        for r in direct:
            flag = r.get("flag") or ""
            if not flag.startswith(prefix):
                continue
            entry = slot_rows.setdefault(flag, {"module": None, "charge": None})
            if r.get("category_id") == CHARGE_CATEGORY_ID:
                entry["charge"] = r["type_name"]
            else:
                entry["module"] = r["type_name"]
        block = []
        for flag in sorted(slot_rows, key=lambda f: (len(f), f)):
            entry = slot_rows[flag]
            if entry["module"] is None:
                continue
            block.append(entry["module"] + (f", {entry['charge']}" if entry["charge"] else ""))
        if block:
            lines.append("")
            lines.extend(block)
    for flag_names in (("DroneBay",), ("FighterBay",), ("Cargo",)):
        counts: dict[str, float] = defaultdict(float)
        for r in direct:
            if r.get("flag") in flag_names:
                counts[r["type_name"]] += r["quantity"]
        if counts:
            lines.append("")
            lines.extend(f"{name} x{int(qty)}" for name, qty in sorted(counts.items()))
    return "\n".join(lines) + "\n"


def contents_multiset(inside: list[dict]) -> dict[int, float]:
    """Everything inside the ship (hull excluded) as type -> quantity, the
    Ist side of Doctrine's match score."""
    out: dict[int, float] = defaultdict(float)
    for r in inside:
        out[r["type_id"]] += r["quantity"]
    return dict(out)
