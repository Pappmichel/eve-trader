"""Pure Total Wealth breakdown.

Assets, listed sell orders and outstanding contract items are separate
piles: ESI's asset list does not contain the other two, so the same
type_id in more than one pile is not a double count. Unpriced rows are
left out of every sum. Buy-order escrow is omitted entirely when it is
unknown (None), never treated as zero.
"""
from __future__ import annotations

from typing import Optional

JITA_44 = 60003760
_OTHER_SHARE = 0.02
_PROTECTED_LOCATIONS = ("C-J", "Jita", "Contracts", "Wallet")
UNPRICED_LIMIT = 100


def _one_or_count(values: set, noun: str) -> str:
    if len(values) == 1:
        return next(iter(values))
    return f"{len(values)} {noun}"


def _share(value: float, total: float) -> Optional[float]:
    if total == 0:
        return None
    return value / total


def _rows(grouped: dict[str, float], total: float) -> list[dict]:
    rows = [
        {"label": label, "value": value, "share": _share(value, total)}
        for label, value in grouped.items()
        if value != 0
    ]
    rows.sort(key=lambda row: (-row["value"], row["label"]))
    return rows


def location_label(component: str, location_id: Optional[int], structure_id: Optional[int],
                   names: dict) -> str:
    if component == "contracts":
        return "Contracts"
    if structure_id is not None and location_id is not None and int(location_id) == int(structure_id):
        return "C-J"
    if location_id is not None and int(location_id) == JITA_44:
        return "Jita"
    if location_id is None:
        return "Unknown"
    return names.get(int(location_id)) or str(int(location_id))


def assemble(
    items: list[dict],
    wallets: list[dict],
    escrow: Optional[list[dict]],
    *,
    structure_id: Optional[int] = None,
    owner_names: Optional[dict] = None,
    location_names: Optional[dict] = None,
    category_names: Optional[dict] = None,
    type_names: Optional[dict] = None,
) -> dict:
    """`items` are {component, owner_type, owner_id, location_id, type_id,
    quantity, price}. `price` None is unpriced and excluded. `escrow` None
    means the ISK is not known yet and is left out of the total.

    Owner, location and category rows sum to `total`. `top` is at most 20
    positions and is not that sum.
    """
    owner_names = owner_names or {}
    location_names = location_names or {}
    category_names = category_names or {}
    type_names = type_names or {}

    priced_value = 0.0
    priced_items = 0
    unpriced_items = 0
    components = {"assets": 0.0, "blueprints": 0.0, "listed": 0.0, "contracts": 0.0}
    by_owner: dict[str, float] = {}
    by_category: dict[str, float] = {}
    raw_locations: dict[str, float] = {}
    unpriced_by_type: dict[int, dict] = {}
    positions: dict[tuple, dict] = {}

    for item in items:
        owner_id = int(item["owner_id"])
        type_id = int(item["type_id"])
        quantity = float(item["quantity"])
        owner = owner_names.get(owner_id) or str(owner_id)
        place = location_label(item["component"], item.get("location_id"), structure_id, location_names)
        if item.get("price") is None:
            unpriced_items += 1
            slot = unpriced_by_type.setdefault(type_id, {
                "type_id": type_id, "type_name": type_names.get(type_id), "quantity": 0.0,
                "positions": 0, "owners": set(), "locations": set(), "components": set(),
            })
            slot["quantity"] += quantity
            slot["positions"] += 1
            slot["owners"].add(owner)
            slot["locations"].add(place)
            slot["components"].add(item["component"])
            continue
        value = quantity * float(item["price"])
        priced_value += value
        priced_items += 1
        components[item["component"]] = components.get(item["component"], 0.0) + value
        by_owner[owner] = by_owner.get(owner, 0.0) + value
        category = category_names.get(type_id) or "Uncategorized"
        by_category[category] = by_category.get(category, 0.0) + value
        raw_locations[place] = raw_locations.get(place, 0.0) + value
        key = (item["component"], owner_id, item.get("location_id"), type_id)
        slot = positions.setdefault(key, {
            "type_id": type_id,
            "type_name": type_names.get(type_id) or str(type_id),
            "quantity": 0.0,
            "value": 0.0,
            "owner_name": owner,
            "location_name": place,
            "component": item["component"],
        })
        slot["quantity"] += quantity
        slot["value"] += value

    wallet_value = 0.0
    for row in wallets:
        amount = float(row["amount"])
        wallet_value += amount
        owner = owner_names.get(int(row["owner_id"])) or str(int(row["owner_id"]))
        if amount == 0:
            continue
        by_owner[owner] = by_owner.get(owner, 0.0) + amount
        by_category["ISK"] = by_category.get("ISK", 0.0) + amount

    escrow_value = None if escrow is None else sum(float(row["amount"]) for row in escrow)
    if escrow is not None:
        for row in escrow:
            amount = float(row["amount"])
            if amount == 0:
                continue
            owner = owner_names.get(int(row["owner_id"])) or str(int(row["owner_id"]))
            by_owner[owner] = by_owner.get(owner, 0.0) + amount
            by_category["ISK"] = by_category.get("ISK", 0.0) + amount

    total = priced_value + wallet_value + (escrow_value or 0.0)
    located = sum(raw_locations.values())
    threshold = _OTHER_SHARE * located
    by_location: dict[str, float] = {}
    for label, value in raw_locations.items():
        if label not in _PROTECTED_LOCATIONS and threshold > 0 and value < threshold:
            by_location["Other"] = by_location.get("Other", 0.0) + value
        else:
            by_location[label] = by_location.get(label, 0.0) + value
    if wallet_value or escrow_value:
        by_location["Wallet"] = by_location.get("Wallet", 0.0) + wallet_value + (escrow_value or 0.0)

    top = sorted(positions.values(), key=lambda row: (-row["value"], row["type_name"]))[:20]
    for row in top:
        row["share"] = _share(row["value"], total)

    # One row per type: a manual price is per type, and a tenant can hold
    # thousands of unpriced positions. Capped so the page stays usable.
    unpriced = [
        {
            "type_id": slot["type_id"],
            "type_name": slot["type_name"],
            "quantity": slot["quantity"],
            "positions": slot["positions"],
            "owner_name": _one_or_count(slot["owners"], "owners"),
            "location_name": _one_or_count(slot["locations"], "locations"),
            "component": _one_or_count(slot["components"], "components"),
        }
        for slot in sorted(
            unpriced_by_type.values(),
            key=lambda slot: (-slot["quantity"], slot["type_name"] or "", slot["type_id"]),
        )[:UNPRICED_LIMIT]
    ]

    return {
        "total": total,
        "components": {
            "assets": components["assets"],
            "blueprints": components["blueprints"],
            "listed": components["listed"],
            "contracts": components["contracts"],
            "wallet": wallet_value,
            "escrow": escrow_value,
        },
        "priced_items": priced_items,
        "unpriced_items": unpriced_items,
        "unpriced": unpriced,
        "unpriced_types": len(unpriced_by_type),
        "by_owner": _rows(by_owner, total),
        "by_location": _rows(by_location, total),
        "by_category": _rows(by_category, total),
        "top": top,
    }
