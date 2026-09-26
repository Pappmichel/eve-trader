"""Builds the Ore Shortlist's candidate universe - GitHub issue #91.

Unlike Trading's own candidate search (a market-group-path crawl over the
whole SDE, backtested one-by-one against Goonmetrics history - see
../candidate_discovery.py), the set of compressed ore/ice types is small and
fixed: every published type in a "Compressed <Family>"/"Compressed Ice" SDE
group (storage.load_ore_ice_candidate_types), confirmed with the user during
planning as not worth a full crawl.
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from .constants import ORE_ICE_CATEGORY_ID
from .models import OreCandidate


def _family_and_is_ice(type_name: str, group_name: str) -> tuple[str, bool]:
    """Ore compression groups are per-family in the real SDE ("Compressed
    Veldspar", "Compressed Scordite", ...), so the group_name itself (minus
    the "Compressed " prefix) already *is* the family - one group per family,
    confirmed against real SDE data. Ice compression instead shares one
    single "Compressed Ice" group across every ice variant (Blue Ice, Clear
    Icicle, ...), each with its own processing skill, so ice needs its family
    derived from the *type* name instead - each compressed ice type is named
    "Compressed <Family>" individually (e.g. "Compressed Blue Ice").

    is_ice is decided by "Ice" appearing in the group_name - this repo's own
    Fuzzwork SDE fetch couldn't be live-verified against the real CSV during
    development (network egress blocked), so this is a best-effort
    real-group-name check, not a name-substring heuristic on individual
    items; if a future "Refresh SDE" run turns up a compressed ice type this
    doesn't correctly flag, fix this function, not the caller."""
    is_ice = "Ice" in group_name
    if is_ice:
        family = type_name.removeprefix("Compressed ").strip()
    else:
        family = group_name.removeprefix("Compressed ").strip()
    return family, is_ice


def ore_ice_families_for_types(type_ids: list[int]) -> dict[int, tuple[str, bool]]:
    """{type_id: (family, is_ice)} for every type_id in `type_ids` that's
    ore/ice (raw or its compressed variant - real ore/ice reprocessing
    yield% is identical either way, compression only reduces volume); a
    type_id that isn't ore/ice at all (SDE category_id != ORE_ICE_CATEGORY_ID)
    or isn't in the SDE cache is simply absent from the result, not None-
    valued. One bulk lookup regardless of how many ids are passed.

    T2-01 (business-logic audit, 2026-09-25/26): the Reprocessing tab's
    paste-import (evaluate_reprocessing_line) used to apply scrapmetal_yield
    unconditionally to every pasted line, including ore/ice - silently
    understating ore/ice's real yield (up to ~90.6%, vs scrapmetal's ~55%
    cap) with no warning. build_ore_candidate_universe's own
    _family_and_is_ice already had the right classification logic for the
    Ore Shortlist's fixed, pre-scanned compressed-type universe
    (storage.load_ore_ice_candidate_types) - this is the same logic, reused
    for arbitrary type_ids instead (storage.get_types_names_and_groups_bulk),
    so an uncompressed "Veldspar" paste line classifies correctly too, not
    only "Compressed Veldspar". Kept as a separate bulk lookup (not folded
    into do_quote_reprocessing's own resolved_by_name loop) so
    evaluate_reprocessing_line itself stays storage-free/pure - the caller
    resolves everything upfront, same pattern as mineral_stats_by_id."""
    rows = storage.get_types_names_and_groups_bulk(type_ids)
    result: dict[int, tuple[str, bool]] = {}
    for type_id, (type_name, group_name, category_id) in rows.items():
        if category_id == ORE_ICE_CATEGORY_ID:
            result[type_id] = _family_and_is_ice(type_name, group_name)
    return result


def build_ore_candidate_universe() -> list[OreCandidate]:
    """The fixed, SDE-derived candidate universe for the Ore Shortlist -
    every published compressed ore/ice type, tagged with its family (for
    RefiningConfig.ore_family_skill_levels) and whether it's ice (yield
    formula is otherwise identical between the two - see refining/engine.py's
    ore_ice_yield, which doesn't itself branch on ore vs. ice at all; is_ice
    matters only for the family lookup and future haul-volume/compression
    display, not the yield math itself)."""
    rows = storage.load_ore_ice_candidate_types()
    candidates = []
    for type_id, type_name, volume, group_name in rows:
        if not type_name or not volume:
            continue
        family, is_ice = _family_and_is_ice(type_name, group_name)
        candidates.append(OreCandidate(type_id=type_id, item=type_name, family=family,
                                        is_ice=is_ice, volume_m3=volume))
    return candidates
