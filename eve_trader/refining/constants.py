"""Hand-curated reprocessing constants - GitHub issue #90 ("Ore & Minerals").

Two structurally different formulas, confirmed against real in-game values
during planning (2026-08-22), not an oversight that they diverge so much:

**Ore/ice path** (compressed ore/ice only, see refining/engine.py
ore_ice_yield) - structure/rig/security/implant/skills all apply:
    Yield = Base(structure, rig, security) x (1 + Reprocessing skill)
                                            x (1 + Reprocessing Efficiency skill)
                                            x (1 + ore-or-ice-family skill)
                                            x (1 + implant)
Confirmed maximum: 90.6% (Tatara, T2-Rig, null-sec, max skills, RX-804).

**Scrapmetal path** (everything else - modules/ammo/drones/loot, see
refining/engine.py scrapmetal_yield) - structure/rig/security/implant/the
general Reprocessing and Reprocessing Efficiency skills have NO effect here,
a genuine asymmetry vs. the ore/ice path, not a bug:
    Yield = 50% (fixed) + Scrapmetal Processing skill
Confirmed maximum: 55%.

Base(structure, rig, security) for the ore/ice path was back-solved to hit
the confirmed 90.6% ceiling exactly (Tatara base 52%, T2-rig base bonus 5%
scaled by the null-sec rig multiplier below), then confirmed with the user
(2026-08-22) - see STRUCTURE_BASE_YIELD/RIG_BASE_YIELD_BONUS docstrings for
the full derivation. Re-verify against wiki.eveuniversity.org/Reprocessing
if CCP ever rebalances these (structure/rig reprocessing bonuses have been
tuned before, e.g. the Tatara's own base moved 4% -> 5.5% at one point per
patch notes - the game's live values are the source of truth, this file is a
best-effort snapshot of them).
"""
from __future__ import annotations

from typing import Optional

# -- Ore/ice path: structure base yield (no rig, no skills) --
# Reused key set from production/constants.py's STRUCTURE_TYPES for the
# Settings-page dropdown (same options a user already recognizes from
# Production's own structure_type fields) - only the three that can actually
# reprocess (a Refinery or a plain Citadel) get a real entry; Engineering
# Complexes (Raitaru/Azbel/Sotiyo) can't fit reprocessing modules at all in
# real EVE, so they're deliberately absent here even though they're valid
# choices for production/config.py's manufacturing_structure_type.
STRUCTURE_BASE_YIELD: dict[str, float] = {
    "Citadel (no bonuses)": 0.50,   # matches the NPC-station-equivalent 50% base
    "Athanor (M Refinery)": 0.51,
    "Tatara (L Refinery)": 0.52,
}

# -- Ore/ice path: rig base bonus (highsec, before security scaling) --
# Additive percentage points on top of STRUCTURE_BASE_YIELD, scaled by
# production/constants.py's rig_security_multiplier(security, is_reaction=False)
# - the same 1x highsec / 1.9x lowsec / 2.1x null-sec-or-wormhole table
# Engineering Complex ME/TE rigs use (reprocessing rigs are not banned in
# highsec, unlike reactor rigs, so the reaction-only 1x/1.1x table doesn't
# apply here).
RIG_BASE_YIELD_BONUS: dict[str, float] = {
    "No Rig": 0.0,
    "T1-Rig": 0.025,
    "T2-Rig": 0.05,
}

# -- Ore/ice path: reprocessing implants (Zainou 'Beancounter' RX-80x) --
REPROCESSING_IMPLANT_BONUS: dict[str, float] = {
    "None": 0.0,
    "RX-801": 0.01,
    "RX-802": 0.02,
    "RX-804": 0.04,
}

MAX_SKILL_LEVEL = 5   # EVE's own hard skill-level ceiling (skills only ever run 0-5)

# -- Ore/ice path: per-level skill bonuses --
REPROCESSING_SKILL_BONUS_PER_LEVEL = 0.03            # "Reprocessing", max +15% (L5)
REPROCESSING_EFFICIENCY_SKILL_BONUS_PER_LEVEL = 0.02  # "Reprocessing Efficiency", max +10% (L5)
ORE_FAMILY_SKILL_BONUS_PER_LEVEL = 0.02               # e.g. "Veldspar Processing", max +10% (L5)

# -- Scrapmetal path (non-ore/ice loot: modules/ammo/drones) --
SCRAPMETAL_BASE_YIELD = 0.50
# "Scrapmetal Processing", +1%/level, max +5% (L5) - 50% + 5% = the confirmed
# 55% ceiling. Note: the +2%/level figure floating around some guides is
# wrong/inconsistent with that same confirmed 55% max (50% + 5x2% = 60%, not
# 55%) - 1%/level is the value that actually reconciles with it.
SCRAPMETAL_SKILL_BONUS_PER_LEVEL = 0.01

# -- Compression (GitHub issue #90's scope decision, apply to the whole
# feature) - NOT uniform: asteroid ore/moon ore/Mercoxit compress to 1% of
# uncompressed volume; ice and gas compress to only 10%. Keyed by SDE
# category_id (production/constants.py has no existing constant for these -
# Ore's own category_id, 25, is the one this codebase already references
# indirectly via candidate_discovery; Ice shares the "Ice Product"/"Ice Ore"
# groups under the same Ore category in the SDE, not a separate category).
# Confirmed via EVE University Wiki/forums during planning (2026-08-22).
ORE_ICE_CATEGORY_ID = 25
# SDE group_id for "Ice" (uncompressed ice ore groups) and "Ice Product"
# (refined ice products - not reprocessable, irrelevant here) - Ice's
# compression ratio (10%) differs from every other Ore-category group (1%),
# so it needs its own group_id set rather than a flat per-category constant.
ICE_GROUP_IDS: frozenset = frozenset({465})
COMPRESSED_VOLUME_RATIO_ORE = 0.01
COMPRESSED_VOLUME_RATIO_ICE = 0.10


def structure_options() -> tuple[str, ...]:
    return tuple(STRUCTURE_BASE_YIELD.keys())


def rig_options() -> tuple[str, ...]:
    return tuple(RIG_BASE_YIELD_BONUS.keys())


def implant_options() -> tuple[str, ...]:
    return tuple(REPROCESSING_IMPLANT_BONUS.keys())


def clamp_skill_level(level: Optional[int]) -> int:
    """Clamps a manually-entered skill level (this app doesn't pull skills
    via ESI, see RefiningConfig's own docstring) to EVE's real 0-5 range -
    a bad Settings-page value (typo'd 15) should never silently inflate a
    yield calculation past what's actually achievable in-game."""
    if level is None:
        return 0
    return max(0, min(MAX_SKILL_LEVEL, level))
