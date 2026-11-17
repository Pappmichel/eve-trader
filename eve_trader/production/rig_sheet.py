"""Rig tiers from a shared spreadsheet (a rig-tier source per solar system and
job category).

An alliance may keep a Google sheet that lists, per solar system, which rig
tier its engineering complexes and refineries carry. The tenant stores the
sheet's link in ProductionConfig.rig_sheet_url; `engine.facility_resolution`
uses the stored result as a rig source between a tier saved for the category
and the one in the structure's name.

Two layers, deliberately apart:

* `parse_csv` and `lookup` are pure (no I/O). The parser tolerates a messy
  sheet: an unknown code, an unparseable row or a reference that cannot be
  followed means "no value", never a guess.
* `fetch_csv` is the only network code. It builds the export address itself
  from the sheet id and `gid` of the stored link (no free fetch of any URL),
  allows only docs.google.com and a redirect to *.googleusercontent.com,
  times out after 10 s and stops at 1 MiB. Planners and facility_resolution
  never call it; they read the last good stored state.

Sheet layout the parser expects (CSV export): no single header. A group row
(for example "Reactions", "Advanced Component", "Subcapital Production")
sits directly above a column row that contains the cell "Location" (the
solar system). Column names repeat from group to group, so a column is
identified by group plus name. Cell codes: T1, T2, N (no structure or no
bonus rig), F (planned), optionally with a qualifier in brackets, arrows that
point at the neighbouring column, and empty. Percent columns
(Manufacturing, Reactions) and every other column are ignored.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests

MAX_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 5
MAX_URL_LENGTH = 500

SHEET_TO_RIG = {"T1": "T1-Rig", "T2": "T2-Rig", "N": "No Rig"}


class RigSheetError(Exception):
    """A sheet problem that is safe to show to the user: never carries the URL
    or any text of the server's response."""


# -------------------------------------------------------------------- columns
def _norm(text: str) -> str:
    """Lower case, alphanumeric words only, trailing plural 's' dropped, so
    "Advanced Components" and "Advanced Component" are one key."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


# (group, column) -> (job category, advanced hull or None for "either",
# structure family or None when the column does not depend on the structure).
# One table, the only place that ties sheet columns to job categories.
# Not mapped, so no sheet value for these categories/columns: "Capital Ship"
# freighter columns (Freighter, Jump Freighter), the "Structure" column,
# everything under "Reprocessing", the percent columns, and the Research /
# Copy / Invention blocks.
_COLUMN_TABLE: dict[tuple[str, str], tuple[str, Optional[bool], Optional[str]]] = {}


def _register(group: str, column: str, category: str, advanced: Optional[bool], family: Optional[str]) -> None:
    _COLUMN_TABLE[(_norm(group), _norm(column))] = (category, advanced, family)


for _family in ("Athanor", "Tatara"):
    _register("Reactions", _family, "Reactions", None, _family)
for _family in ("Raitaru", "Azbel", "Sotiyo"):
    _register("Advanced Component", _family, "Advanced Components", None, _family)
    _register("Capital Component", _family, "Capital Components", None, _family)
for _family in ("Azbel", "Sotiyo"):
    _register("Capital Production", _family, "Capital Ship", None, _family)
_register("Supercapital", "Sotiyo", "Super Capital Ship", None, "Sotiyo")
for _size, _category in (("Small", "Small Ships"), ("Med", "Medium Ships"), ("Large", "Large Ships")):
    _register("Subcapital Production", f"T1 {_size} Ship", _category, False, None)
    _register("Subcapital Production", f"T2 {_size} Ship", _category, True, None)
_register("Subcapital Production", "Fighter / Drone", "Drones & Fighters", None, None)
_register("Subcapital Production", "Equipment", "Equipment", None, None)
_register("Subcapital Production", "Ammo", "Ammunition", None, None)

STRUCTURE_FAMILIES = ("raitaru", "azbel", "sotiyo", "athanor", "tatara")


def structure_family(structure_type: Optional[str]) -> Optional[str]:
    """"Azbel (L Engineering Complex)" -> "Azbel"; a Citadel or anything
    unknown -> None (the sheet then gives no value for a type-dependent column)."""
    first = (structure_type or "").strip().split(" ", 1)[0].lower()
    return first.capitalize() if first in STRUCTURE_FAMILIES else None


def mapped_categories() -> dict[str, list[str]]:
    """{job category: ["Group / Column", ...]} for the report and tests."""
    result: dict[str, list[str]] = {}
    for (group, column), (category, _advanced, _family) in _COLUMN_TABLE.items():
        result.setdefault(category, []).append(f"{group} / {column}")
    return result


# ---------------------------------------------------------------------- cells
_RIGHT_ARROWS = ("→", "->", "⇒", "➡", "➜", "⟶")
_LEFT_ARROWS = ("←", "<-", "⇐", "⬅", "⟵")
_VALUE_RE = re.compile(r"^(t1|t2|n)\s*(?:\((.*)\))?$", re.IGNORECASE | re.DOTALL)


def _parse_cell(text: str) -> tuple[str, Any, Any]:
    """("value", tier, qualifier) | ("arrow", +1 or -1, None) | ("none", None, None)."""
    cell = (text or "").strip()
    if cell in _RIGHT_ARROWS:
        return "arrow", 1, None
    if cell in _LEFT_ARROWS:
        return "arrow", -1, None
    match = _VALUE_RE.match(cell)
    if match:
        qualifier = match.group(2)
        return "value", match.group(1).upper(), (qualifier.strip().lower() if qualifier is not None else None)
    return "none", None, None


def _resolve_cell(parsed: list[tuple[str, Any, Any]], groups: list[str], column: int) -> Optional[tuple[str, Any]]:
    """The (tier, qualifier) a cell stands for, following arrows to the
    neighbouring column within the same group. A loop, the group's end or a
    neighbour without a T1/T2/N value means no value."""
    kind, first, _ = parsed[column]
    if kind == "value":
        return first, parsed[column][2]
    if kind != "arrow":
        return None
    visited = {column}
    index = column + first
    while 0 <= index < len(parsed) and groups[index] == groups[column] and index not in visited:
        visited.add(index)
        kind, tier_or_direction, qualifier = parsed[index]
        if kind == "value":
            return tier_or_direction, qualifier
        if kind != "arrow":
            return None
        index += tier_or_direction
    return None


# --------------------------------------------------------------------- parser
def _find_header_rows(rows: list[list[str]]) -> list[int]:
    return [i for i, row in enumerate(rows) if any(cell.strip().lower() == "location" for cell in row)]


def parse_csv(text: str) -> dict:
    """Parses the sheet's CSV export into
    {"systems": {key: {"name", "cells": {"group|column": {"tier", "qualifier"}}}},
     "duplicates": [system name, ...], "blocks": n}.

    Only blocks with at least one column of the mapping table count (the
    Research / Copy / Invention blocks have none). A system listed in several
    blocks keeps its first row; the repeats are reported in `duplicates`.
    Raises RigSheetError when no usable column row or no system is found."""
    rows = [list(r) for r in csv.reader(io.StringIO(text))]
    headers = _find_header_rows(rows)
    systems: dict[str, dict] = {}
    duplicates: list[str] = []
    blocks = 0
    for position, header_index in enumerate(headers):
        header = rows[header_index]
        location_col = next(i for i, c in enumerate(header) if c.strip().lower() == "location")
        group_row = rows[header_index - 1] if header_index > 0 else []
        width = len(header)
        groups: list[str] = []
        current = ""
        for i in range(width):
            cell = group_row[i].strip() if i < len(group_row) else ""
            if cell:
                current = _norm(cell)
            groups.append(current)
        columns = [_norm(c) for c in header]
        mapped = {i: (groups[i], columns[i]) for i in range(width)
                  if i != location_col and (groups[i], columns[i]) in _COLUMN_TABLE}
        # Repeated (group, name) inside one block: the first column wins.
        seen: set[tuple[str, str]] = set()
        for i in sorted(mapped):
            if mapped[i] in seen:
                del mapped[i]
            else:
                seen.add(mapped[i])
        if not mapped:
            continue
        blocks += 1
        # The data rows end before the next block's group row (the row above its header).
        end = (headers[position + 1] - 1) if position + 1 < len(headers) else len(rows)
        for row in rows[header_index + 1:end]:
            name = row[location_col].strip() if location_col < len(row) else ""
            if not name or name.lower() == "location":
                continue
            padded = [(row[i] if i < len(row) else "") for i in range(width)]
            parsed = [_parse_cell(c) for c in padded]
            key = name.casefold()
            if key in systems:
                if name not in duplicates:
                    duplicates.append(name)
                continue
            cells: dict[str, dict] = {}
            for i, (group, column) in mapped.items():
                resolved = _resolve_cell(parsed, groups, i)
                if resolved is not None:
                    cells[f"{group}|{column}"] = {"tier": resolved[0], "qualifier": resolved[1]}
            systems[key] = {"name": name, "cells": cells}
    if blocks == 0:
        raise RigSheetError("No column row with a Location column and known rig columns was found in the sheet.")
    if not systems:
        raise RigSheetError("The sheet has no system rows under its column row.")
    return {"systems": systems, "duplicates": duplicates, "blocks": blocks}


def system_cells(state_systems: Optional[dict], system_name: Optional[str]) -> Optional[dict]:
    """The stored cells of one system by name, or None when it is not listed."""
    if not state_systems or not system_name:
        return None
    entry = state_systems.get(system_name.strip().casefold())
    return entry.get("cells") if entry else None


def lookup(cells: Optional[dict], category: str, advanced: bool, structure_type: Optional[str]) -> Optional[str]:
    """The rig tier ("T1-Rig" | "T2-Rig" | "No Rig") the sheet states for a
    job category of one system, or None for "no value".

    `advanced` picks the Tech II column of a ship size (`T2 ... Ship`), else
    the Tech I one. A column that depends on the structure type (Raitaru,
    Azbel, Sotiyo, Athanor, Tatara) is read at the family of `structure_type`;
    an unknown type gives no value. A qualifier in brackets limits the tier to
    some reaction kinds: only "All" is usable, because a job category is not
    one kind (any other qualifier means no value)."""
    if not cells:
        return None
    family = structure_family(structure_type)
    for (group, column), (cat, adv, fam) in _COLUMN_TABLE.items():
        if cat != category:
            continue
        if adv is not None and adv != bool(advanced):
            continue
        if fam is not None and fam != family:
            continue
        cell = cells.get(f"{group}|{column}")
        if not cell:
            return None
        qualifier = cell.get("qualifier")
        if qualifier is not None and qualifier != "all":
            return None
        return SHEET_TO_RIG.get(cell.get("tier"))
    return None


# ---------------------------------------------------------------------- fetch
_SHEET_PATH_RE = re.compile(r"^/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9_-]{10,100})(?:/|$)")
_ALLOWED_REDIRECT_SUFFIX = ".googleusercontent.com"


def parse_sheet_url(url: str) -> tuple[str, str]:
    """(sheet id, gid) of a docs.google.com spreadsheet link; RigSheetError
    for anything else (another host, a scheme other than https, no sheet id)."""
    text = (url or "").strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise RigSheetError("The rig sheet link is empty or too long.")
    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError:
        raise RigSheetError("The rig sheet link is not a valid web address.") from None
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or port not in (None, 443) \
            or parsed.username or parsed.password:
        raise RigSheetError("The rig sheet link must be an https://docs.google.com/spreadsheets link.")
    match = _SHEET_PATH_RE.match(parsed.path)
    if not match:
        raise RigSheetError("The rig sheet link does not contain a spreadsheet id.")
    gid = (parse_qs(parsed.query).get("gid") or [None])[0]
    if gid is None:
        gid = (parse_qs(parsed.fragment).get("gid") or ["0"])[0]
    if not gid.isdigit() or len(gid) > 20:
        raise RigSheetError("The rig sheet link has an invalid gid.")
    return match.group(1), gid


def export_url(url: str) -> str:
    sheet_id, gid = parse_sheet_url(url)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def source_key(url: str) -> str:
    """Identifies the sheet and tab a stored state came from without keeping
    the address: a changed link must not reuse the old sheet's state."""
    sheet_id, gid = parse_sheet_url(url)
    return hashlib.sha256(f"{sheet_id}:{gid}".encode()).hexdigest()[:20]


def is_valid_url(url: str) -> bool:
    try:
        parse_sheet_url(url)
    except RigSheetError:
        return False
    return True


def _http_get(url: str, timeout: float):
    """The one network call (tests replace it)."""
    return requests.get(url, timeout=timeout, stream=True, allow_redirects=False,
                        headers={"User-Agent": "eve-trader-rig-sheet"})


def _redirect_allowed(location: str) -> bool:
    try:
        parsed = urlparse(location)
    except ValueError:
        return False
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "docs.google.com" or host.endswith(_ALLOWED_REDIRECT_SUFFIX))


def fetch_csv(url: str) -> str:
    """The sheet's CSV export as text. Only the export address built from the
    stored link is requested; a redirect must stay on docs.google.com or a
    *.googleusercontent.com host. Errors never include the address or the
    server's answer."""
    target = export_url(url)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    for _ in range(MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RigSheetError("The rig sheet request timed out.")
        try:
            response = _http_get(target, remaining)
        except requests.RequestException:
            raise RigSheetError("The rig sheet could not be reached (network error).") from None
        try:
            status = response.status_code
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location") or ""
                if not _redirect_allowed(location):
                    raise RigSheetError("The rig sheet redirects to a host that is not allowed "
                                        "(is the sheet shared by link?).")
                target = location
                continue
            if status != 200:
                raise RigSheetError(f"The rig sheet could not be fetched (HTTP {status}).")
            body = bytearray()
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise RigSheetError("The rig sheet is larger than 1 MiB.")
                    if time.monotonic() > deadline:
                        raise RigSheetError("The rig sheet request timed out.")
            except requests.RequestException:
                raise RigSheetError("The rig sheet could not be read (network error).") from None
            return bytes(body).decode("utf-8-sig", errors="replace")
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
    raise RigSheetError("The rig sheet redirected too often.")
