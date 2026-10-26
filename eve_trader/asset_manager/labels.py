"""Display vocabulary for asset flags and location ids - pure, no I/O."""
from __future__ import annotations

from typing import Optional

from ..storage import is_fitted_location_flag

# ESI location_flag -> label for the outermost hangar an item sits in.
_HANGAR_LABELS = {
    "Hangar": "Item hangar",
    "ShipHangar": "Ship hangar",
    "Deliveries": "Deliveries",
    "CorpDeliveries": "Corp deliveries",
    "CorpMarket": "Corp market",
    "AssetSafety": "Asset safety",
    "Impounded": "Impounded",
    "OfficeFolder": "Office",
    "Locked": "Item hangar",
    "Unlocked": "Item hangar",
}

# Sections of a ship's fitting/holds for the ship view (F-1), in display order.
SHIP_SECTIONS = (
    "High slots", "Mid slots", "Low slots", "Rig slots", "Subsystems", "Service slots",
    "Drone bay", "Fighter bay", "Fighter tubes", "Cargo", "Other holds",
)


def hangar_label(flag: Optional[str], division_names: Optional[dict[int, str]] = None) -> str:
    """Label for the outermost hangar flag. Corp divisions use the corp's
    own division names when known (D7), else "Division N"."""
    if not flag:
        return "Unknown"
    if flag.startswith("CorpSAG") and flag[7:].isdigit():
        number = int(flag[7:])
        name = (division_names or {}).get(number)
        return name or f"Division {number}"
    return _HANGAR_LABELS.get(flag, flag)


def ship_section(flag: Optional[str]) -> str:
    """Which part of a ship an item with this flag sits in."""
    flag = flag or ""
    for prefix, label in (
        ("HiSlot", "High slots"), ("MedSlot", "Mid slots"), ("LoSlot", "Low slots"),
        ("RigSlot", "Rig slots"), ("SubSystemSlot", "Subsystems"), ("ServiceSlot", "Service slots"),
        ("FighterTube", "Fighter tubes"),
    ):
        if flag.startswith(prefix):
            return label
    if flag == "DroneBay":
        return "Drone bay"
    if flag == "FighterBay":
        return "Fighter bay"
    if flag == "Cargo":
        return "Cargo"
    return "Other holds"


def location_kind(location_id: Optional[int], has_name: bool) -> str:
    """station / structure / space / unknown / none, from the id range.
    A large id is a structure only when something named it: an item id of a
    container missing from a partial sync is in the same range."""
    if not location_id:
        return "none"
    if 60_000_000 <= location_id < 64_000_000:
        return "station"
    if 30_000_000 <= location_id < 33_000_000:
        return "space"
    if location_id >= 1_000_000_000_000 and has_name:
        return "structure"
    return "unknown"


def badges_for_asset(location_flag: Optional[str], hangar_flag: Optional[str],
                     assembled_or_fitted: bool, is_blueprint: bool, is_copy: bool) -> list[str]:
    """Badges of one ESI asset row (D4). `hangar_flag` is the outermost
    flag, so contents of an Asset Safety wrap are badged too."""
    out: list[str] = []
    flags = {location_flag, hangar_flag}
    if "AssetSafety" in flags:
        out.append("asset_safety")
    if flags & {"Deliveries", "CorpDeliveries"}:
        out.append("delivery")
    if "CorpMarket" in flags:
        out.append("corp_market")
    if assembled_or_fitted:
        out.append("fitted" if is_fitted_location_flag(location_flag) else "assembled")
    if is_blueprint:
        out.append("bpc" if is_copy else "bpo")
    return out


def is_stock(source: str, badges: list[str]) -> bool:
    """The planners' notion of stock: ESI or manual rows that are not in a
    non-stock flag and not an assembled ship or its fitting."""
    if source not in ("esi", "manual"):
        return False
    return not ({"asset_safety", "delivery", "corp_market", "assembled", "fitted"} & set(badges))
