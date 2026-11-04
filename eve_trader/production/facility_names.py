"""Read ME rig tiers from a structure's name.

Corps often name an engineering structure after its rig fit, for example
"IAK-JW - CEZ T2 Adv L T1 Basic L": a Tech II rig for Advanced Large Ships
(Tech II/III hulls) and a Tech I rig for Basic Large Ships (Tech I hulls).
ESI does not expose a structure's rigs unless the corporation owns it, so the
name is the only automatic source. production/engine.py's `_facility_mults`
uses this only when the user has not set a tier for the job category.

Pure: no I/O. Anything not recognised returns None, so the caller falls back
to the next source instead of guessing.
"""
from __future__ import annotations

import re
from typing import Optional

_TIER_TOKENS = {"t1": "T1-Rig", "t2": "T2-Rig"}
_ADVANCED_WORDS = frozenset({"adv", "advanced"})
_BASIC_WORDS = frozenset({"basic"})
_CAP_WORDS = frozenset({"cap", "caps", "capital", "capitals"})
_COMP_WORDS = frozenset({"comp", "comps", "component", "components"})
_SUPER_WORDS = frozenset({"super", "supers", "supercap", "supercaps", "supercapital", "supercapitals",
                          "titan", "titans"})

# Ship categories split their rigs into Basic (Tech I) and Advanced (Tech
# II/III) sizes; every other category has one rig for all tech levels, so
# "adv"/"basic" there is part of the category's name, not a tech level.
_SHIP_SIZE_WORDS: dict[str, frozenset] = {
    "Small Ships": frozenset({"s", "small", "frig", "frigs", "frigate", "frigates", "destroyer", "destroyers"}),
    "Medium Ships": frozenset({"m", "medium", "cruiser", "cruisers", "bc", "bcs"}),
    "Large Ships": frozenset({"l", "large", "bs", "battleship", "battleships"}),
}
_PLAIN_WORDS: dict[str, frozenset] = {
    "Equipment": frozenset({"equip", "equipment", "mod", "mods", "module", "modules"}),
    "Drones & Fighters": frozenset({"drone", "drones", "fighter", "fighters"}),
    "Ammunition": frozenset({"ammo", "ammunition", "charge", "charges"}),
    "Reactions": frozenset({"react", "reacts", "reaction", "reactions", "reactor", "reactors"}),
}
# Categories whose rig depends on the item's tech level.
SPLIT_BY_TECH_CATEGORIES: frozenset = frozenset(_SHIP_SIZE_WORDS)

_ALL_KEYWORDS = (
    _CAP_WORDS | _COMP_WORDS | _SUPER_WORDS
    | frozenset().union(*_SHIP_SIZE_WORDS.values())
    | frozenset().union(*_PLAIN_WORDS.values())
)


def _tokens(name: str) -> list[str]:
    # The part before the first " - " is the solar system; tags before the
    # first tier token (owner tickers such as "CEZ") are ignored below.
    label = name.split(" - ", 1)[1] if " - " in name else name
    return re.findall(r"[a-z0-9]+", label.lower())


def _segments(tokens: list[str]) -> list[tuple[str, set[str]]]:
    """[(rig tier, words)] - each segment starts at a T1/T2 token."""
    segments: list[tuple[str, set[str]]] = []
    for token in tokens:
        if token in _TIER_TOKENS:
            segments.append((_TIER_TOKENS[token], set()))
        elif segments:
            segments[-1][1].add(token)
    return segments


def _applies(category: str, words: set[str]) -> bool:
    if category in _SHIP_SIZE_WORDS:
        return bool(words & _SHIP_SIZE_WORDS[category])
    if category in _PLAIN_WORDS:
        return bool(words & _PLAIN_WORDS[category])
    if category == "Capital Components":
        return bool(words & _CAP_WORDS) and bool(words & _COMP_WORDS)
    if category == "Advanced Components":
        return bool(words & _COMP_WORDS) and not (words & _CAP_WORDS)
    if category == "Capital Ship":
        return bool(words & _CAP_WORDS) and not (words & _COMP_WORDS) and not (words & _SUPER_WORDS)
    if category == "Super Capital Ship":
        return bool(words & _SUPER_WORDS)
    return False


def rig_tiers_from_name(name: Optional[str], category: str) -> tuple[Optional[str], Optional[str]]:
    """(rig tier for Tech I items, rig tier for Tech II/III items) that the
    structure name states for `category`, each a constants.RIG_TIERS key or
    None when the name says nothing about it."""
    if not name:
        return None, None
    segments = _segments(_tokens(name))
    if not segments:
        return None, None
    basic: Optional[str] = None
    advanced: Optional[str] = None
    split_by_tech = category in SPLIT_BY_TECH_CATEGORIES
    for tier, words in segments:
        if not _applies(category, words):
            continue
        is_advanced = split_by_tech and bool(words & _ADVANCED_WORDS)
        is_basic = split_by_tech and bool(words & _BASIC_WORDS)
        if is_advanced == is_basic:  # neither (or both) stated: the rig covers both
            is_advanced = is_basic = True
        if is_basic and basic is None:
            basic = tier
        if is_advanced and advanced is None:
            advanced = tier
    if basic is None and advanced is None and len(segments) == 1 and not (segments[0][1] & _ALL_KEYWORDS):
        # "XYZ - T2" or "XYZ - T1 rigged": one tier, no category words.
        return segments[0][0], segments[0][0]
    return basic, advanced
