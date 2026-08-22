"""Hand-curated reprocessing constants - GitHub issue #90 ("Ore & Minerals").

Two structurally different formulas, confirmed against real in-game values
during planning (2026-08-22), not an oversight that they diverge so much:

**Ore/ice path** (compressed ore/ice only, see refining/engine.py
ore_ice_yield) - the real Upwell structure reprocessing formula, confirmed
live against wiki.eveuniversity.org/Reprocessing (2026-08-23):
    Yield = (50 + Rig points) x (1 + Security modifier) x (1 + Structure modifier)
                               x (1 + Reprocessing skill)
                               x (1 + Reprocessing Efficiency skill)
                               x (1 + ore-or-ice-family skill)
                               x (1 + implant)
Confirmed maximum: 90.6% (Tatara, T2-Rig, null-sec, max skills, RX-804) -
(50+3) x 1.12 x 1.055 x 1.15 x 1.10 x 1.10 x 1.04 = 90.63%.

An earlier version of this file approximated the structure/rig/security
component as a single back-solved "Base(structure, rig, security)" additive
term instead - a guess made without live access to the real wiki formula at
the time (confirmed only against the single 90.6%-ceiling data point, not
the underlying formula shape), off by ~0.1-0.2pp even at that one confirmed
point and not exactly right anywhere else in the structure/rig/security
space. See STRUCTURE_YIELD_MODIFIER/RIG_YIELD_BONUS_POINTS/
security_yield_modifier below for the real formula's own components.

**Scrapmetal path** (everything else - modules/ammo/drones/loot, see
refining/engine.py scrapmetal_yield) - structure/rig/security/implant/the
general Reprocessing and Reprocessing Efficiency skills have NO effect here,
a genuine asymmetry vs. the ore/ice path, not a bug:
    Yield = 50% (fixed) + Scrapmetal Processing skill
Confirmed maximum: 55%.

Re-verify against wiki.eveuniversity.org/Reprocessing if CCP ever rebalances
these (structure/rig reprocessing bonuses have been tuned before, e.g. the
Tatara's own structure modifier moved 4% -> 5.5% at one point per patch
notes - the game's live values are the source of truth, this file is a
snapshot of them).
"""
from __future__ import annotations

from typing import Optional

# -- Ore/ice path: universal starting point before any rig/structure/security
# modifier - every reprocessing-capable structure starts here (real EVE
# constant, not tool-specific).
BASE_YIELD_POINTS = 50

# -- Ore/ice path: structure modifier (Sm), applied multiplicatively together
# with the security modifier below to (BASE_YIELD_POINTS + rig points) - NOT
# an additive percentage-point bonus (see ore_ice_base_yield). Reused key set
# from production/constants.py's STRUCTURE_TYPES for the Settings-page
# dropdown (same options a user already recognizes from Production's own
# structure_type fields) - only the three that can actually reprocess (a
# Refinery or a plain Citadel) get a real entry; Engineering Complexes
# (Raitaru/Azbel/Sotiyo) can't fit reprocessing modules at all in real EVE,
# so they're deliberately absent here even though they're valid choices for
# production/config.py's manufacturing_structure_type.
STRUCTURE_YIELD_MODIFIER: dict[str, float] = {
    "Citadel (no bonuses)": 0.0,
    "Athanor (M Refinery)": 0.02,
    "Tatara (L Refinery)": 0.055,
}

# -- Ore/ice path: rig bonus (Rm) - flat percentage POINTS added to
# BASE_YIELD_POINTS before any multiplier is applied (not a fraction, not
# pre-scaled by security - the security modifier below applies to the whole
# (BASE_YIELD_POINTS + rig points) sum, rig points included, in one
# multiplicative step, unlike production/constants.py's rig_security_
# multiplier which scales *only* an Engineering Complex's own ME/TE rig
# bonus - a genuinely different mechanic, don't reuse that function here).
RIG_YIELD_BONUS_POINTS: dict[str, float] = {
    "No Rig": 0,
    "T1-Rig": 1,
    "T2-Rig": 3,
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


def _rounded_security(security_status: float) -> float:
    """EVE classifies/displays system security at 1-decimal precision, not
    the SDE/ESI's raw multi-decimal true-sec value - mirrors production/
    constants.py's own _rounded_security (same real CCP rule: any positive
    true-sec below 0.05 still rounds to 0.1, never down to a flat 0.0 - 0.0
    is reserved for genuine null-sec). Duplicated rather than imported -
    tiny and pure, not worth widening production/constants.py's private API
    for."""
    if 0.0 < security_status < 0.05:
        return 0.1
    return round(security_status, 1)


def security_yield_modifier(security_status: Optional[float]) -> float:
    """Sec in the real reprocessing formula (see this module's own
    docstring) - 0.00 highsec, 0.06 lowsec, 0.12 null-sec/wormhole,
    confirmed live against wiki.eveuniversity.org/Reprocessing (2026-08-23).
    A genuinely different mechanic from production/constants.py's
    rig_security_multiplier (that one scales only an Engineering Complex's
    own rig bonus; this modifies the reprocessing structure's whole base
    yield, rig points included, multiplicatively - see ore_ice_base_yield),
    despite both being keyed off the same highsec/lowsec/null classification.
    Unknown security assumes highsec (0.00, the lowest-bonus case) rather
    than over-crediting an unverified location."""
    if security_status is None:
        return 0.0
    rounded = _rounded_security(security_status)
    if rounded >= 0.5:
        return 0.0
    if rounded > 0.0:
        return 0.06
    return 0.12


def structure_options() -> tuple[str, ...]:
    return tuple(STRUCTURE_YIELD_MODIFIER.keys())


def rig_options() -> tuple[str, ...]:
    return tuple(RIG_YIELD_BONUS_POINTS.keys())


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
