"""Parses EVE's standard inventory "Copy As" clipboard paste - GitHub issue
#92. Confirmed against evepraisal.com's own open-source `evepaste` library
(`evepaste/parsers/assets.py`, the real, actively-maintained reference
implementation for this exact format - WebFetch verified during
implementation since the EVE wikis/forums themselves are network-blocked in
this dev environment) - the real column shape (Ctrl+A, Ctrl+C from an
Inventory window in "Assets"/list view) is 9 tab-separated columns, no header
row:

    Name \t Quantity \t Group \t Category \t Size \t Slot \t Volume \t Meta Level \t Tech Level

Size/Slot/Meta Level/Tech Level are frequently empty for many item types
(e.g. a T1 ammo stack has no Size/Slot at all) - trailing empty fields still
produce real tab characters in the real client's own copy, so a short
`split("\t")` result (missing trailing empties) is treated as padding with
empty strings, not a parse error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from . import storage

_QUANTITY_CLEAN_RE = re.compile(r"[^\d]")  # EVE item quantities are always whole numbers -
                                             # any comma/period present is a thousands separator, never a decimal.
_VOLUME_CLEAN_RE = re.compile(r"[^\d.]")   # volume IS fractional ("5.0 m3") - strip the " m3" suffix/commas only.

_EXPECTED_COLUMNS = 9


@dataclass
class ParsedPasteLine:
    raw_line: str
    name: str
    quantity: int
    category: str
    volume_m3: Optional[float]
    error: Optional[str] = None  # set instead of raising - a single bad line shouldn't abort the whole paste


def _parse_quantity(raw: str) -> Optional[int]:
    cleaned = _QUANTITY_CLEAN_RE.sub("", raw)
    return int(cleaned) if cleaned else None


def _parse_volume(raw: str) -> Optional[float]:
    raw = raw.replace("m3", "").strip()
    cleaned = _VOLUME_CLEAN_RE.sub("", raw)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_paste(text: str) -> list[ParsedPasteLine]:
    """Parses every non-blank line of `text` independently - a malformed line
    is recorded with its own `error` (still returned, not dropped, per
    #92's "flag rather than silently drop" requirement) instead of aborting
    the whole paste."""
    results = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            results.append(ParsedPasteLine(raw_line=line, name=line.strip(), quantity=0, category="",
                                            volume_m3=None, error="Not tab-separated - paste from an Inventory "
                                                                   "window's list view, not free text."))
            continue
        fields = (fields + [""] * _EXPECTED_COLUMNS)[:_EXPECTED_COLUMNS]
        name, qty_raw, _group, category, _size, _slot, volume_raw, _meta, _tech = fields
        name = name.strip()
        quantity = _parse_quantity(qty_raw)
        if not name or quantity is None:
            results.append(ParsedPasteLine(raw_line=line, name=name or line.strip(), quantity=0,
                                            category=category.strip(), volume_m3=None,
                                            error="Could not read a name/quantity from this line."))
            continue
        results.append(ParsedPasteLine(raw_line=line, name=name, quantity=quantity, category=category.strip(),
                                        volume_m3=_parse_volume(volume_raw)))
    return results


def merge_duplicate_stacks(lines: list[ParsedPasteLine]) -> list[ParsedPasteLine]:
    """Sums quantities for repeated names (multiple stacks of the same item
    in different cargo slots, confirmed with the user during planning) - case
    -insensitive, since EVE's own item names are unique regardless of case
    and a paste could plausibly mix casing across separate copy operations."""
    merged: dict[str, ParsedPasteLine] = {}
    order: list[str] = []
    for line in lines:
        if line.error:
            continue
        key = line.name.lower()
        if key in merged:
            existing = merged[key]
            merged[key] = ParsedPasteLine(raw_line=existing.raw_line, name=existing.name,
                                           quantity=existing.quantity + line.quantity, category=existing.category,
                                           volume_m3=existing.volume_m3)
        else:
            merged[key] = line
            order.append(key)
    return [merged[k] for k in order]


def resolve_paste(text: str, skip_blueprints: bool = True) -> dict:
    """Parses an inventory paste and resolves every name to a type_id via
    storage.resolve_type_names_exact/suggest_type_names (the batch "Did you
    mean...?" pattern, docs/MANUAL_TRACKING_PLAN.md decision 18).

    `skip_blueprints` leaves Blueprint-category lines out and lists them
    instead: manual stock needs that, because no reliable ME/TE/Runs clipboard
    format exists (decision 10). A caller that only needs type and quantity
    passes False.

    Returns {"resolved": {type_id: (type_name, quantity)}, "skipped_blueprints":
    [name, ...], "unresolved": [{"line": raw_line, "suggestion": name|None}, ...],
    "errors": [{"line": raw_line, "error": message}, ...]}. Two different
    paste lines that both resolve to the same type_id (distinct names for
    the same item is not a real case, but merge_duplicate_stacks only
    merges identical names) have their quantities summed."""
    all_lines = parse_paste(text)
    error_lines = [line for line in all_lines if line.error]
    parsed = merge_duplicate_stacks(all_lines)

    def is_skipped(line: ParsedPasteLine) -> bool:
        return skip_blueprints and line.category.strip().lower() == "blueprint"

    skipped_blueprints = [line.name for line in parsed if is_skipped(line)]
    item_lines = [line for line in parsed if not is_skipped(line)]

    exact = storage.resolve_type_names_exact([line.name for line in item_lines])
    missing = [line.name for line in item_lines if line.name.strip().lower() not in exact]
    suggestions = storage.suggest_type_names(missing) if missing else {}

    resolved: dict[int, list] = {}
    unresolved = []
    for line in item_lines:
        key = line.name.strip().lower()
        hit = exact.get(key)
        if hit is None:
            suggestion = suggestions.get(key)
            unresolved.append({"line": line.raw_line, "suggestion": suggestion[1] if suggestion else None})
            continue
        type_id, resolved_name, _category_id = hit
        if type_id in resolved:
            resolved[type_id][1] += line.quantity
        else:
            resolved[type_id] = [resolved_name, float(line.quantity)]

    return {
        "resolved": {type_id: (name, qty) for type_id, (name, qty) in resolved.items()},
        "skipped_blueprints": skipped_blueprints,
        "unresolved": unresolved,
        "errors": [{"line": line.raw_line, "error": line.error} for line in error_lines],
    }
