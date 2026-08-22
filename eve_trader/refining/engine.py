"""Pure calculation functions for reprocessing - GitHub issue #90 ("Ore &
Minerals"). No framework imports, no I/O beyond storage's cached SDE lookups -
mirrors production/engine.py's own "the real logic lives here" role (see
CLAUDE.md's "Architecture: actions.py is the one entry point").

Two structurally different yield-% formulas (ore_ice_yield/scrapmetal_yield -
see constants.py's module docstring for the full derivation/confirmed
maximums), sharing one material-application step (apply_reprocessing_yield) -
both are "type -> material yield" lookups against the same SDE
sde_type_materials table (storage.get_type_materials).
"""
from __future__ import annotations

import math
from typing import Optional

from .. import storage
from ..production.constants import rig_security_multiplier
from .config import RefiningConfig
from .constants import (
    ORE_FAMILY_SKILL_BONUS_PER_LEVEL,
    REPROCESSING_EFFICIENCY_SKILL_BONUS_PER_LEVEL,
    REPROCESSING_IMPLANT_BONUS,
    REPROCESSING_SKILL_BONUS_PER_LEVEL,
    RIG_BASE_YIELD_BONUS,
    SCRAPMETAL_BASE_YIELD,
    SCRAPMETAL_SKILL_BONUS_PER_LEVEL,
    STRUCTURE_BASE_YIELD,
    clamp_skill_level,
)


def ore_ice_base_yield(cfg: RefiningConfig) -> float:
    """Base(structure, rig, security) - the one component of the ore/ice
    formula that isn't a flat per-level skill/implant bonus (see constants.py's
    module docstring for how this was derived/confirmed). Rig bonus is scaled
    by security status using the same Engineering-Complex-style 1x/1.9x/2.1x
    table production/constants.py's rig_security_multiplier already applies
    to ME/TE rigs (reprocessing rigs aren't reaction-only, so the reactor-only
    1x/1.1x table doesn't apply here - see RIG_BASE_YIELD_BONUS's own
    docstring)."""
    structure_base = STRUCTURE_BASE_YIELD[cfg.structure_type]
    rig_base = RIG_BASE_YIELD_BONUS[cfg.rig_tier]
    sec_multiplier = rig_security_multiplier(cfg.security_status, is_reaction=False)
    return structure_base + rig_base * sec_multiplier


def ore_ice_yield(cfg: RefiningConfig, ore_family: Optional[str] = None) -> float:
    """Effective reprocessing yield % for a compressed ore/ice item, given its
    family (e.g. "Veldspar" - see RefiningConfig.ore_family_skill_levels'
    docstring for what a family is). A *known* family simply missing from the
    dict (the user hasn't entered a skill level for it in Settings yet) is
    assumed maxed (level 5), not unskilled - these are cheap skills almost
    every active player has trained to 5, and defaulting to 0 would
    understate every not-yet-configured family's profit; ore_family=None
    itself (no family at all, e.g. a non-ore/ice item) still gets 0, there's
    no family skill to assume anything about.

    Yield = Base(structure, rig, security) x (1 + Reprocessing skill)
                                            x (1 + Reprocessing Efficiency skill)
                                            x (1 + ore-or-ice-family skill)
                                            x (1 + implant)
    Confirmed maximum 90.6% (Tatara, T2-Rig, null-sec, max skills, RX-804) -
    see constants.py's module docstring."""
    base = ore_ice_base_yield(cfg)
    reprocessing = clamp_skill_level(cfg.reprocessing_skill_level)
    efficiency = clamp_skill_level(cfg.reprocessing_efficiency_skill_level)
    ore_family_level = clamp_skill_level(cfg.ore_family_skill_levels.get(ore_family, 5) if ore_family else 0)
    implant_bonus = REPROCESSING_IMPLANT_BONUS[cfg.implant]
    return (
        base
        * (1 + reprocessing * REPROCESSING_SKILL_BONUS_PER_LEVEL)
        * (1 + efficiency * REPROCESSING_EFFICIENCY_SKILL_BONUS_PER_LEVEL)
        * (1 + ore_family_level * ORE_FAMILY_SKILL_BONUS_PER_LEVEL)
        * (1 + implant_bonus)
    )


def scrapmetal_yield(cfg: RefiningConfig) -> float:
    """Effective reprocessing yield % for a non-ore/ice item (modules/ammo/
    drones/loot - the Reprocessing tab's #92 primary use case). Structure/rig/
    security/implant/the two ore-path skills above have NO effect here - a
    genuine asymmetry vs. ore_ice_yield, confirmed against real in-game values
    during planning, not an oversight (see constants.py's module docstring).
    Confirmed maximum 55%."""
    skill = clamp_skill_level(cfg.scrapmetal_processing_skill_level)
    return SCRAPMETAL_BASE_YIELD + skill * SCRAPMETAL_SKILL_BONUS_PER_LEVEL


def apply_reprocessing_yield(type_id: int, quantity: int, yield_pct: float) -> dict[int, int]:
    """Reprocesses `quantity` units of `type_id` at `yield_pct` efficiency,
    returning {material_type_id: output_quantity}. Two real-EVE roundings,
    both confirmed with the user during planning (not a continuous
    approximation):
    1. Whole-**portion** batching first - `quantity` is floor-divided by the
       type's SDE portionSize (storage.get_portion_size, e.g. Veldspar=100)
       to get the number of complete portions; leftover units below one
       portion yield nothing, same as reprocessing a partial stack in-game.
    2. Each material's output is then floored independently (not the total
       across materials), matching EVE's own per-material rounding.

    Returns {} for a type with no SDE portion_size/material rows (not yet
    SDE-refreshed, or genuinely not reprocessable - e.g. a ship/skillbook/BPO/
    BPC, see the Reprocessing tab's #92 own "flag non-reprocessable items"
    requirement, which reads an empty result here as exactly that signal)."""
    portion_size = storage.get_portion_size(type_id)
    if not portion_size or portion_size <= 0:
        return {}
    portions = quantity // portion_size
    if portions <= 0:
        return {}
    materials = storage.get_type_materials(type_id)
    return {
        material_type_id: math.floor(portions * qty_per_portion * yield_pct)
        for material_type_id, qty_per_portion in materials
    }
