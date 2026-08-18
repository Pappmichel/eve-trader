"""EFT fitting format parser (Phase 3 spec section A) - a pure, stateless
text -> ParsedFitting translator. No storage, no network: every SDE lookup
the parser needs is injected by the caller (doctrine/engine.py, which reads
storage.get_sde_type/storage.get_type_slot) as plain callables, so this
module is fully unit-testable with fake in-memory lookups and carries no
tenant context of its own (SDE data is a shared, non-RLS table - see
constants.py's own module docstring).

Grammar (Phase 3 spec A.2, CCP's official Fitting Formats doc):

    fitting        := ws-prefix header LF body
    header         := "[" hull-name "," WS? fit-name "]"
    body           := { block | blank-line }
    block          := item-line { LF item-line }
    item-line      := empty-marker | typed-line
    empty-marker   := "[Empty" WS slot-word WS? "slot]"      (case-insensitive)
    typed-line     := names [ qty-suffix ] [ offline-suffix ]
    names          := type-name [ "," WS? charge-name ]
    qty-suffix     := WS "x" integer
    offline-suffix := WS "/offline"                           (case-insensitive)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .constants import (
    CHARGE_CATEGORY_ID, DRONE_CATEGORY_ID, FIGHTER_CATEGORY_ID, ISSUE_KIND_AMBIGUOUS_SPLIT,
    ISSUE_KIND_MALFORMED, ISSUE_KIND_UNKNOWN_SECTION, ISSUE_KIND_UNRESOLVED_NAME,
    LEVENSHTEIN_SUGGESTION_MAX_RATIO, VALID_HULL_CATEGORY_IDS,
)
from .models import ParsedFitting, ParsedIssue, ParsedItem

# Positional block order (Phase 3 A.2/A.4) - only used for the marker-export
# position-vs-SDE gegenprobe (A.4's last rule), never to *decide* a section.
_EFT_SECTION_ORDER = ("low", "med", "high", "rig", "subsystem", "service", "drone", "cargo")


@dataclass
class ResolvedType:
    """What the injected name/type_id resolvers hand back - only type_id and
    category_id are actually used by parsing logic itself (Phase 3 A.6: the
    parser stores no variant/meta info), group_id/meta_group_id/meta_level
    are carried through for callers (e.g. B.6's variant-pairing in
    engine.py) that need them without a second lookup."""
    type_id: int
    group_id: Optional[int]
    category_id: Optional[int]
    meta_group_id: Optional[int]
    meta_level: Optional[int]
    type_name: str


NameResolver = Callable[[str], Optional[ResolvedType]]
SlotResolver = Callable[[int], Optional[str]]


class FittingParseError(Exception):
    """Hard parse failure (Phase 3 A.7's "harte Fehler" table) - header
    missing/invalid, or the hull name doesn't resolve to a real ship/
    structure. Caller (actions.do_add_fitting/do_parse_fitting) converts
    this to ActionError; nothing gets persisted."""


_EMPTY_MARKER_RE = re.compile(r"^\[\s*empty\s+\S+(?:\s+\S+)*\s+slot\s*\]$", re.IGNORECASE)
_OFFLINE_SUFFIX_RE = re.compile(r"\s*/offline\s*$", re.IGNORECASE)
_QTY_SUFFIX_RE = re.compile(r"\s+x(\d+)\s*$", re.IGNORECASE)
_HEADER_RE = re.compile(r"^\[(.*)\]$")


def _levenshtein(a: str, b: str) -> int:
    """Standard edit-distance DP - no external dependency, used only for
    A.7's "did you mean" hull-name suggestion, never for auto-correction
    (see this module's own top docstring / Phase 3 A.3: no fuzzy matching in
    real resolution, ever)."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def _suggest(name: str, candidates: Iterable[str]) -> Optional[str]:
    best: Optional[str] = None
    best_dist = None
    for candidate in candidates:
        dist = _levenshtein(name, candidate)
        if best_dist is None or dist < best_dist:
            best, best_dist = candidate, dist
    if best is None or best_dist is None:
        return None
    if best_dist > len(name) * LEVENSHTEIN_SUGGESTION_MAX_RATIO:
        return None
    return best


def _normalize(raw_text: str) -> list[str]:
    """Phase 3 A.3: CRLF/CR -> LF defines line_no's numbering (done first,
    nothing before this point may change line counts); per-line whitespace
    normalization (tabs, repeated spaces, trim) happens after, for parsing
    only - never mutates `raw_eft` itself, which the caller keeps verbatim."""
    text = raw_text.lstrip("﻿")  # strip BOM if present
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    normalized = []
    for line in lines:
        line = line.replace("\t", " ")
        line = re.sub(r" {2,}", " ", line)
        normalized.append(line.strip())
    return normalized


def _comma_split_candidates(text: str) -> list[tuple[str, str]]:
    """Comma-ambiguity resolution driver (Phase 3 A.5): splits at each comma
    from *right to left* - the rightmost valid split is the most
    conservative one, since module names are more likely to themselves
    contain comma-adjacent-looking text than charge names."""
    positions = [i for i, ch in enumerate(text) if ch == ","]
    return [(text[:i].strip(), text[i + 1:].strip()) for i in reversed(positions)]


def _resolve_names(text: str, resolve_name: NameResolver) -> tuple[Optional[ResolvedType], Optional[ResolvedType], Optional[str]]:
    """Phase 3 A.5. Returns (module_or_single, charge_or_none, issue_kind).
    issue_kind is None on success (1 or 2 resolved items), otherwise
    'ambiguous_split' or 'unresolved_name'."""
    whole = resolve_name(text)
    if whole is not None:
        return whole, None, None
    for left, right in _comma_split_candidates(text):
        if not left or not right:
            continue
        left_t = resolve_name(left)
        right_t = resolve_name(right)
        if left_t is not None and right_t is not None and right_t.category_id == CHARGE_CATEGORY_ID:
            return left_t, right_t, None
    # Any split where both halves resolve, but the right half isn't a
    # charge, is a real ambiguity (Phase 3 A.5 case 3) - distinguish it from
    # "resolves nowhere" (case 4) so the two get different issue kinds/messages.
    for left, right in _comma_split_candidates(text):
        if not left or not right:
            continue
        if resolve_name(left) is not None and resolve_name(right) is not None:
            return None, None, ISSUE_KIND_AMBIGUOUS_SPLIT
    return None, None, ISSUE_KIND_UNRESOLVED_NAME


def _classify_section(resolved: ResolvedType, has_qty: bool, resolve_slot: SlotResolver) -> str:
    """Phase 3 A.4 - SDE wins over position, with the qty-suffix override
    (a mengen-Zeile is never a slot-fitted module, regardless of what
    resolve_slot says - real gear never carries an xN suffix)."""
    if has_qty:
        if resolved.category_id in (DRONE_CATEGORY_ID, FIGHTER_CATEGORY_ID):
            return "drone"
        return "cargo"
    slot = resolve_slot(resolved.type_id)
    if slot is not None:
        return slot
    if resolved.category_id in (DRONE_CATEGORY_ID, FIGHTER_CATEGORY_ID):
        return "drone"
    return "cargo"  # includes the "bare Charge line" case - see caller for its own issue


def parse_fitting(raw_text: str, resolve_name: NameResolver, resolve_slot: SlotResolver,
                   hull_name_candidates: Iterable[str] = ()) -> ParsedFitting:
    """Phase 3 spec A - full entry point. Raises FittingParseError for the
    two hard-fail cases (A.7); everything else becomes a ParsedItem or a
    ParsedIssue, never an exception, so a fitting with warnings is still
    fully parseable and (by the caller's choice) storable."""
    lines = _normalize(raw_text)

    header_idx = None
    for i, line in enumerate(lines):
        if line:
            header_idx = i
            break
    if header_idx is None:
        raise FittingParseError(
            "Kein gültiger EFT-Header - erste Zeile muss `[Schiffsname, Fittingname]` sein."
        )
    header_line = lines[header_idx]
    m = _HEADER_RE.match(header_line)
    if not m:
        raise FittingParseError(
            "Kein gültiger EFT-Header - erste Zeile muss `[Schiffsname, Fittingname]` sein."
        )
    inner = m.group(1)
    if "," not in inner:
        raise FittingParseError(
            "Header ohne Komma - erwartet `[Schiffsname, Fittingname]`."
        )
    comma_idx = inner.index(",")
    hull_name = inner[:comma_idx].strip()
    fit_name = inner[comma_idx + 1:].strip()
    if not hull_name:
        raise FittingParseError("Leerer Schiffsname im Header.")

    hull = resolve_name(hull_name)
    if hull is None:
        suggestion = _suggest(hull_name, hull_name_candidates)
        msg = f"Schiff '{hull_name}' nicht gefunden."
        if suggestion:
            msg += f" Meintest du '{suggestion}'?"
        raise FittingParseError(msg)
    if hull.category_id not in VALID_HULL_CATEGORY_IDS:
        raise FittingParseError(f"'{hull_name}' ist kein Schiff.")

    body_lines = lines[header_idx + 1:]

    # Blocks: contiguous runs of non-blank lines, separated by 1+ blank
    # lines - used only for the position-vs-SDE gegenprobe (A.4's last
    # rule), never to decide a section outright. has_markers gates whether
    # the gegenprobe fires at all (A.4: only for exports that actually
    # encode position via [Empty ... slot] markers).
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for offset, line in enumerate(body_lines):
        line_no = header_idx + 2 + offset  # 1-based; body starts right after the header line
        if line == "":
            if current:
                blocks.append(current)
                current = []
            continue
        current.append((line_no, line))
    if current:
        blocks.append(current)

    has_markers = any(_EMPTY_MARKER_RE.match(line) for block in blocks for _ln, line in block)
    expected_section_by_line: dict[int, str] = {}
    if has_markers:
        for block_idx, block in enumerate(blocks):
            if block_idx >= len(_EFT_SECTION_ORDER):
                continue
            expected = _EFT_SECTION_ORDER[block_idx]
            for line_no, _line in block:
                expected_section_by_line[line_no] = expected

    items: list[ParsedItem] = []
    issues: list[ParsedIssue] = []

    for block in blocks:
        for line_no, line in block:
            if _EMPTY_MARKER_RE.match(line):
                continue  # A.7: no item, no issue

            remainder = line
            is_offline = False
            if _OFFLINE_SUFFIX_RE.search(remainder):
                remainder = _OFFLINE_SUFFIX_RE.sub("", remainder)
                is_offline = True

            has_qty = False
            qty = 1
            qty_match = _QTY_SUFFIX_RE.search(remainder)
            if qty_match:
                qty = int(qty_match.group(1))
                if qty >= 1:
                    remainder = _QTY_SUFFIX_RE.sub("", remainder)
                    has_qty = True
                # qty == 0 is nonsensical (grammar requires >=1) - fall through
                # and let name resolution run on the untouched line, which
                # will most likely just fail to resolve as unresolved_name.

            remainder = remainder.strip()
            if remainder.startswith("[") and not _EMPTY_MARKER_RE.match(line):
                issues.append(ParsedIssue(line_no, line, ISSUE_KIND_MALFORMED,
                                           f"Nicht erkennbare Zeile: '{line}'"))
                continue
            if not remainder:
                issues.append(ParsedIssue(line_no, line, ISSUE_KIND_MALFORMED,
                                           f"Leere Zeile nach Suffix-Erkennung: '{line}'"))
                continue

            primary, charge, issue_kind = _resolve_names(remainder, resolve_name)
            if issue_kind == ISSUE_KIND_AMBIGUOUS_SPLIT:
                issues.append(ParsedIssue(line_no, line, ISSUE_KIND_AMBIGUOUS_SPLIT,
                                           f"Mehrdeutige Zeile, mehrere gültige Aufteilungen: '{line}'"))
                continue
            if issue_kind == ISSUE_KIND_UNRESOLVED_NAME or primary is None:
                suggestion = _suggest(remainder, hull_name_candidates)
                msg = f"Unbekanntes Item: '{remainder}'."
                if suggestion:
                    msg += f" Meintest du '{suggestion}'?"
                else:
                    msg += " Möglicherweise ein mutiertes (Abyssal-)Modul - diese können nicht validiert werden."
                issues.append(ParsedIssue(line_no, line, ISSUE_KIND_UNRESOLVED_NAME, msg))
                continue

            section = _classify_section(primary, has_qty, resolve_slot)
            bare_charge_without_qty = (not has_qty and charge is None
                                        and primary.category_id == CHARGE_CATEGORY_ID and section == "cargo")
            if bare_charge_without_qty:
                section = "charge"
                issues.append(ParsedIssue(line_no, line, ISSUE_KIND_UNKNOWN_SECTION,
                                           f"Nackte Ladungs-Zeile ohne Modul/Menge: '{line}'"))
            elif not has_qty and charge is None and primary.category_id not in (
                    DRONE_CATEGORY_ID, FIGHTER_CATEGORY_ID, CHARGE_CATEGORY_ID) and section == "cargo" \
                    and resolve_slot(primary.type_id) is None:
                issues.append(ParsedIssue(line_no, line, ISSUE_KIND_UNKNOWN_SECTION,
                                           f"Unerwartetes Item ohne Slot/Menge, als Cargo behandelt: '{line}'"))
            elif has_qty:
                # A.7's "xN-Suffix außerhalb Drones/Cargo" case: only a
                # genuine surprise if this type otherwise *has* a real slot
                # (a fitted-module type carrying a quantity suffix).
                if resolve_slot(primary.type_id) is not None:
                    issues.append(ParsedIssue(line_no, line, ISSUE_KIND_UNKNOWN_SECTION,
                                               f"Mengenangabe bei fittbarem Modul: '{line}'"))

            if has_markers and not has_qty and line_no in expected_section_by_line:
                expected = expected_section_by_line[line_no]
                if expected in ("low", "med", "high", "rig", "subsystem", "service") and expected != section \
                        and resolve_slot(primary.type_id) is not None:
                    issues.append(ParsedIssue(line_no, line, ISSUE_KIND_UNKNOWN_SECTION,
                                               f"Erwartete Sektion '{expected}', SDE sagt '{section}': '{line}'"))

            items.append(ParsedItem(line_no=line_no, slot_section=section, type_id=primary.type_id,
                                     quantity=float(qty if has_qty else 1), is_offline=is_offline))
            if charge is not None:
                items.append(ParsedItem(line_no=line_no, slot_section="charge", type_id=charge.type_id,
                                         quantity=1.0, is_offline=False))

    return ParsedFitting(hull_type_id=hull.type_id, hull_name=hull.type_name, fit_name=fit_name,
                          items=items, issues=issues)
