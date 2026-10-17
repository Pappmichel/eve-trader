"""Freeze industry-job inputs once, when a job is first archived.

Reuses the planner's formulas (`engine._material_qty`, structure/rig
multipliers, `constants.DECRYPTORS`) - never re-derived. See
docs/PRODUCTION_REALIZED_PROFIT_PLAN.md section 5 and Phase 0 V8.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ... import storage
from ...esi_data.access import is_shared, read_esi
from .. import config as production_config
from ..constants import (
    ACTIVITY_INVENTION,
    ACTIVITY_MANUFACTURING,
    ACTIVITY_REACTION,
    DECRYPTORS,
    structure_rig_multiplier,
)
from ..engine import (
    _material_qty,
    _security_multiplier_for,
    _structure_profile,
    _structure_rig,
    classify_activity,
    job_category,
)
from . import db

# Invention activity in ESI/SDE.
_ACTIVITY_COPYING = 5
_ACTIVITY_RESEARCH_TE = 3
_ACTIVITY_RESEARCH_ME = 4

# Tolerance when matching probability -> decryptor multiplier.
_DECRYPTOR_MULT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class FrozenInput:
    type_id: int
    quantity: float
    blueprint_me: Optional[int]
    material_multiplier: Optional[float]
    decryptor_type_id: Optional[int]
    encryption_skill_level: Optional[int]
    science_skill_1_level: Optional[int]
    science_skill_2_level: Optional[int]
    source: str


def _activity_label(activity_id: int, product_type_id: Optional[int]) -> str:
    if activity_id == ACTIVITY_REACTION:
        return "Reaction"
    if activity_id == ACTIVITY_INVENTION:
        return "Tech II"
    if product_type_id is not None:
        label, _bp = classify_activity(int(product_type_id))
        return label
    return "Tech I"


def _resolve_blueprint_me(
    blueprint_id: Optional[int],
    blueprint_type_id: int,
    activity_label: str,
    cfg,
) -> tuple[Optional[int], str]:
    """Returns (me, source). source matches ledger_job_inputs.source vocabulary."""
    if blueprint_id is not None:
        hit = db.lookup_blueprint_me_te(int(blueprint_id))
        if hit is not None:
            me, _te, source = hit
            return me, source
    manual = db.lookup_manual_blueprint_me(int(blueprint_type_id))
    if manual is not None:
        return int(manual[0]), "manual_blueprint"
    # Activity defaults: T2/faction ME0; Tech I uses the configured baseline
    # (ACTIVITY_MODS "perfect research" ME10 -> material_mult 0.90 => ME 10).
    if activity_label in ("Tech II", "Faction", "Storyline", "Officer", "Deadspace", "Reaction"):
        return 0, "assumed_me"
    # Tech I baseline ME10 (1 - me/100 = 0.90).
    return 10, "assumed_me"


def _material_multiplier_for_job(
    activity_label: str,
    product_type_id: Optional[int],
    facility_id: Optional[int],
    blueprint_me: int,
    cfg,
) -> tuple[float, bool]:
    """(material_multiplier, structure_bonus_applied).

    Structure/rig bonus only when facility_id matches the job category's
    configured location (plan section 5).
    """
    base_me_mult = 1.0 - blueprint_me / 100.0
    type_id = int(product_type_id) if product_type_id is not None else 0
    applied = False
    me_structure = 1.0
    if facility_id is not None and product_type_id is not None:
        category = job_category(type_id)
        locations = storage.load_category_locations()
        if category is not None and locations.get(category) == int(facility_id):
            profile = _structure_profile(activity_label, type_id)
            structure_type, rig_tier = _structure_rig(profile, cfg)
            security = _security_multiplier_for(profile, cfg)
            _cost, me_structure, _te = structure_rig_multiplier(
                structure_type, rig_tier, security,
            )
            applied = True
    return base_me_mult * me_structure, applied


def _installer_skill_levels(installer_id: Optional[int], skill_ids: list[int]) -> dict[int, int]:
    """Active skill levels for the installer, gated on Production skills share."""
    if installer_id is None or not skill_ids:
        return {}
    if not is_shared("skills", "production", "character", int(installer_id)):
        return {}
    rows = read_esi(
        "skills", "production", owner_id=int(installer_id), table="skills",
    )
    by_id = {int(r["skill_id"]): int(r["active_level"]) for r in rows}
    return {sid: by_id.get(sid, 0) for sid in skill_ids}


def _is_encryption_skill(skill_type_id: int) -> bool:
    sde = storage.get_sde_type(int(skill_type_id))
    if sde is None:
        return False
    name = (sde[2] or "") if len(sde) > 2 else ""
    return "Encryption" in name


def _invention_skill_factor(
    blueprint_type_id: int, installer_id: Optional[int],
) -> tuple[float, Optional[int], Optional[int], Optional[int], list[int]]:
    """Skill factor from installer's real skills (enc/40 + sci/30 each).

    Returns (factor, enc_level, sci1_level, sci2_level, skill_type_ids).
    Missing share/levels -> factor 1.0 with None levels (still usable for
    decryptor match when probability already includes skills - Phase 0 used
    real skills; without them we fall back carefully).
    """
    required = list(storage.get_blueprint_skills(int(blueprint_type_id), ACTIVITY_INVENTION))
    skill_ids = [int(sid) for sid, _lvl in required]
    levels = _installer_skill_levels(installer_id, skill_ids)
    enc_id = next((sid for sid in skill_ids if _is_encryption_skill(sid)), None)
    science_ids = [sid for sid in skill_ids if sid != enc_id]
    enc_level = levels.get(enc_id) if enc_id is not None else None
    sci1 = levels.get(science_ids[0]) if len(science_ids) > 0 else None
    sci2 = levels.get(science_ids[1]) if len(science_ids) > 1 else None
    if enc_level is None and sci1 is None and sci2 is None:
        return 1.0, None, None, None, skill_ids
    factor = 1.0
    if enc_level is not None:
        factor += enc_level / 40.0
    if sci1 is not None:
        factor += sci1 / 30.0
    if sci2 is not None:
        factor += sci2 / 30.0
    return factor, enc_level, sci1, sci2, skill_ids


def _match_decryptor(
    probability: Optional[float],
    blueprint_type_id: int,
    product_type_id: Optional[int],
    installer_id: Optional[int],
    selected_decryptors: dict[int, str],
) -> tuple[Optional[int], str, Optional[int], Optional[int], Optional[int]]:
    """Returns (decryptor_type_id, source, enc, sci1, sci2).

    Phase 0 V8: implied_mult = probability / (base * skill_factor).
    None vs Symmetry (both 1.0): selected_decryptors, else None, flagged.
    """
    skill_factor, enc, sci1, sci2, _ids = _invention_skill_factor(
        blueprint_type_id, installer_id,
    )
    recipe = storage.get_invention_recipe(int(blueprint_type_id), product_type_id)
    if probability is None or recipe is None or recipe.get("base_probability") is None:
        # Fall back to selected / None.
        override = None
        if product_type_id is not None:
            override = selected_decryptors.get(int(product_type_id))
        if override and override in DECRYPTORS:
            return DECRYPTORS[override].type_id or None, "assumed_decryptor", enc, sci1, sci2
        return None, "assumed_decryptor", enc, sci1, sci2

    base = float(recipe["base_probability"])
    denom = base * skill_factor
    if denom <= 0:
        return None, "assumed_decryptor", enc, sci1, sci2
    implied = float(probability) / denom

    matches = [
        (name, d) for name, d in DECRYPTORS.items()
        if abs(d.probability_multiplier - implied) <= _DECRYPTOR_MULT_TOLERANCE
    ]
    if not matches:
        # Closest match within a looser band, else assumed.
        closest = min(
            DECRYPTORS.items(),
            key=lambda kv: abs(kv[1].probability_multiplier - implied),
        )
        if abs(closest[1].probability_multiplier - implied) < 0.05:
            matches = [closest]
        else:
            override = selected_decryptors.get(int(product_type_id)) if product_type_id else None
            if override and override in DECRYPTORS:
                return DECRYPTORS[override].type_id or None, "assumed_decryptor", enc, sci1, sci2
            return None, "assumed_decryptor", enc, sci1, sci2

    # None and Symmetry both 1.0.
    names = {m[0] for m in matches}
    if names == {"None", "Symmetry"} or (len(matches) == 1 and matches[0][0] in ("None", "Symmetry") and abs(implied - 1.0) <= _DECRYPTOR_MULT_TOLERANCE):
        # Prefer selected_decryptors for the invented *product* type when set.
        override = None
        if product_type_id is not None:
            override = selected_decryptors.get(int(product_type_id))
        if override in ("None", "Symmetry"):
            d = DECRYPTORS[override]
            return (d.type_id or None), "probability_decryptor", enc, sci1, sci2
        if override and override in DECRYPTORS:
            # Selected something else but probability says 1.0 - still assume
            # from selection only when probability is ambiguous.
            d = DECRYPTORS[override]
            return (d.type_id or None), "assumed_decryptor", enc, sci1, sci2
        return None, "assumed_decryptor", enc, sci1, sci2

    name, d = matches[0]
    return (d.type_id or None), "probability_decryptor", enc, sci1, sci2


def freeze_job_inputs(
    *,
    owner_type: str,
    owner_id: int,
    job: dict,
    cfg=None,
    selected_decryptors: Optional[dict[int, str]] = None,
) -> list[FrozenInput]:
    """Compute and return the frozen input rows for one industry job.

    Does not write. Caller inserts only when the job is new to the archive.
    """
    cfg = cfg if cfg is not None else production_config.PRODUCTION_CONFIG
    selected = selected_decryptors if selected_decryptors is not None else storage.load_selected_decryptors()

    activity_id = int(job["activity_id"])
    # ESI reactions arrive as 9; fetchers normalize to 11 before archive.
    if activity_id == 9:
        activity_id = ACTIVITY_REACTION

    runs = float(job.get("runs") or 0)
    blueprint_type_id = int(job["blueprint_type_id"])
    product_type_id = job.get("product_type_id")
    product_type_id_i = int(product_type_id) if product_type_id is not None else None
    facility_id = job.get("facility_id")
    blueprint_id = job.get("blueprint_id")
    installer_id = job.get("installer_id")
    probability = job.get("probability")

    # Research / copy: no material inputs (job cost only in costing).
    if activity_id in (_ACTIVITY_RESEARCH_ME, _ACTIVITY_RESEARCH_TE, _ACTIVITY_COPYING):
        return []

    if activity_id == ACTIVITY_INVENTION:
        return _freeze_invention_inputs(
            blueprint_type_id=blueprint_type_id,
            product_type_id=product_type_id_i,
            runs=runs,
            installer_id=installer_id,
            probability=float(probability) if probability is not None else None,
            selected_decryptors=selected,
        )

    # Manufacturing / reaction.
    sde_activity = ACTIVITY_REACTION if activity_id == ACTIVITY_REACTION else ACTIVITY_MANUFACTURING
    materials = storage.get_blueprint_materials(blueprint_type_id, sde_activity)
    if not materials:
        return []

    activity_label = _activity_label(activity_id, product_type_id_i)
    me, me_source = _resolve_blueprint_me(
        int(blueprint_id) if blueprint_id is not None else None,
        blueprint_type_id,
        activity_label,
        cfg,
    )
    me = int(me or 0)
    material_mult, _bonus = _material_multiplier_for_job(
        activity_label, product_type_id_i, facility_id, me, cfg,
    )
    source = me_source
    out: list[FrozenInput] = []
    for material_id, base_qty in materials:
        qty = _material_qty(float(base_qty), material_mult, runs)
        out.append(FrozenInput(
            type_id=int(material_id),
            quantity=float(qty),
            blueprint_me=me,
            material_multiplier=material_mult,
            decryptor_type_id=None,
            encryption_skill_level=None,
            science_skill_1_level=None,
            science_skill_2_level=None,
            source=source,
        ))
    return out


def _freeze_invention_inputs(
    *,
    blueprint_type_id: int,
    product_type_id: Optional[int],
    runs: float,
    installer_id: Optional[int],
    probability: Optional[float],
    selected_decryptors: dict[int, str],
) -> list[FrozenInput]:
    recipe = storage.get_invention_recipe(blueprint_type_id, product_type_id)
    decryptor_type_id, source, enc, sci1, sci2 = _match_decryptor(
        probability, blueprint_type_id, product_type_id, installer_id, selected_decryptors,
    )
    out: list[FrozenInput] = []
    if recipe is not None:
        for material_id, qty in recipe.get("datacores") or []:
            out.append(FrozenInput(
                type_id=int(material_id),
                quantity=float(qty) * runs,
                blueprint_me=None,
                material_multiplier=None,
                decryptor_type_id=decryptor_type_id,
                encryption_skill_level=enc,
                science_skill_1_level=sci1,
                science_skill_2_level=sci2,
                source=source,
            ))
    if decryptor_type_id:
        out.append(FrozenInput(
            type_id=int(decryptor_type_id),
            quantity=runs,  # one decryptor per attempt
            blueprint_me=None,
            material_multiplier=None,
            decryptor_type_id=decryptor_type_id,
            encryption_skill_level=enc,
            science_skill_1_level=sci1,
            science_skill_2_level=sci2,
            source=source,
        ))
    # One T1 BPC run per attempt (relics are regular items, same qty=runs).
    # Represented as consuming blueprint_type_id runs (costing treats BPC runs).
    out.append(FrozenInput(
        type_id=int(blueprint_type_id),
        quantity=runs,
        blueprint_me=None,
        material_multiplier=None,
        decryptor_type_id=decryptor_type_id,
        encryption_skill_level=enc,
        science_skill_1_level=sci1,
        science_skill_2_level=sci2,
        source=source,
    ))
    return out


def frozen_to_row(
    owner_type: str, owner_id: int, job_id: int, inp: FrozenInput,
) -> tuple:
    return (
        owner_type, int(owner_id), int(job_id), int(inp.type_id), float(inp.quantity),
        inp.blueprint_me, inp.material_multiplier, inp.decryptor_type_id,
        inp.encryption_skill_level, inp.science_skill_1_level, inp.science_skill_2_level,
        inp.source,
    )
