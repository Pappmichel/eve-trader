"""Builds the Ore Shortlist's candidate universe - GitHub issue #91.

Unlike Trading's own candidate search (a market-group-path crawl over the
whole SDE, backtested one-by-one against Goonmetrics history - see
../candidate_discovery.py), the set of compressed ore/ice types is small and
fixed: every published type in a "Compressed <Family>"/"Compressed Ice" SDE
group (storage.load_ore_ice_candidate_types), confirmed with the user during
planning as not worth a full crawl.
"""
from __future__ import annotations

from .. import storage
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
