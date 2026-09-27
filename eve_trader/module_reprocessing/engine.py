"""Pure calculation functions for the Module Reprocessing Import tool.

Only the scrapmetal yield formula applies here - this tool never reprocesses
ore/ice, so eve_trader/refining/engine.py's ore_ice_yield/ore_ice_base_yield
(structure/rig/security/implant-dependent) are irrelevant; see
module_reprocessing/config.py's own docstring for why those fields don't
even exist on ModuleReprocessingConfig. apply_reprocessing_yield itself
(portion-size batching + per-material yield/floor) is fully generic - reused
directly from refining.engine rather than redefined here (see that
function's own docstring; it depends only on storage's cached SDE lookups,
not on any RefiningConfig field), same cross-tool pure-function reuse
refining/actions.py already does for production.engine.
"""
from __future__ import annotations

from ..refining.constants import (
    SCRAPMETAL_BASE_YIELD, SCRAPMETAL_SKILL_BONUS_PER_LEVEL, clamp_skill_level,
)
from .config import ModuleReprocessingConfig


def scrapmetal_yield(cfg: ModuleReprocessingConfig) -> float:
    """Effective reprocessing yield % for a module/drone. Structure/rig/
    security/implant/the ore-path Reprocessing skills have NO effect here -
    confirmed against real EVE mechanics (see refining/constants.py's module
    docstring, which documents and derives this same formula for the
    identical scrapmetal path). Confirmed maximum 55% (skill level 5)."""
    skill = clamp_skill_level(cfg.scrapmetal_processing_skill_level)
    return SCRAPMETAL_BASE_YIELD + skill * SCRAPMETAL_SKILL_BONUS_PER_LEVEL
