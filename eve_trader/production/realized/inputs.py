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
    ACTIVITY_MODS,
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

# Tolerance when matching probability -> decryptor multiplier. Phase 0 V8
# matched every live job within 0.005; multipliers are 0.1 apart, so
# anything outside this band is not a match and must not be guessed.
_DECRYPTOR_MULT_TOLERANCE = 0.005

# Activity labels whose blueprints are never researched (ME 0).
_UNRESEARCHED_LABELS = frozenset({"Faction", "Storyline", "Officer", "Deadspace", "Reaction"})


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
    # None when this input has no structure/rig decision (invention, or a
    # row frozen before the column existed). Never inferred later.
    structure_bonus_applied: Optional[bool] = None


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
    product_type_id: Optional[int] = None,
    selected_decryptors: Optional[dict[int, str]] = None,
) -> tuple[Optional[int], str]:
    """Returns (me, source). source matches ledger_job_inputs.source vocabulary.

    Fallbacks when the blueprint itself was never seen (plan 13b):
    - Tech II: an invented BPC's ME is the decryptor's (DECRYPTORS me_bonus is
      the absolute ME); the decryptor selected in Production for the product,
      else the no-decryptor baseline (ME 2);
    - Tech I: the planner's research baseline (ACTIVITY_MODS["Tech I"]);
    - Faction/Storyline/Officer/Deadspace/Reaction: ME 0 (not researchable).
    """
    if blueprint_id is not None:
        hit = db.lookup_blueprint_me_te(int(blueprint_id))
        if hit is not None:
            me, _te, source = hit
            return me, source
    manual = db.lookup_manual_blueprint_me(int(blueprint_type_id))
    if manual is not None:
        return int(manual[0]), "manual_blueprint"
    if activity_label == "Tech II":
        name = (selected_decryptors or {}).get(int(product_type_id)) if product_type_id is not None else None
        decryptor = DECRYPTORS.get(name) if name else None
        return (decryptor or DECRYPTORS["None"]).me_bonus, "assumed_me"
    if activity_label in _UNRESEARCHED_LABELS:
        return 0, "assumed_me"
    baseline = ACTIVITY_MODS["Tech I"].material_multiplier
    return int(round((1.0 - baseline) * 100)), "assumed_me"


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
    if not rows:
        # Shared but never synced: no skill data at all, not "every skill 0".
        return {}
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
) -> tuple[Optional[float], Optional[int], Optional[int], Optional[int]]:
    """Skill factor from the installer's real skills: 1 + encryption/40 +
    (science 1 + science 2)/30. Returns (factor, enc, sci1, sci2).

    factor is None whenever the real levels are not known (skills not shared
    with Production, never synced, or a recipe whose skills are not exactly
    one encryption + two science skills). A guessed factor would turn the
    probability match into a confident wrong answer, so the caller must
    then treat the decryptor as assumed."""
    required = list(storage.get_blueprint_skills(int(blueprint_type_id), ACTIVITY_INVENTION))
    skill_ids = [int(sid) for sid, _lvl in required]
    enc_ids = [sid for sid in skill_ids if _is_encryption_skill(sid)]
    science_ids = [sid for sid in skill_ids if sid not in enc_ids]
    if len(enc_ids) != 1 or len(science_ids) != 2:
        return None, None, None, None
    levels = _installer_skill_levels(installer_id, skill_ids)
    if not levels:
        return None, None, None, None
    enc = levels[enc_ids[0]]
    sci1, sci2 = levels[science_ids[0]], levels[science_ids[1]]
    return 1.0 + enc / 40.0 + (sci1 + sci2) / 30.0, enc, sci1, sci2


def _selected_decryptor(
    t2_blueprint_type_id: Optional[int], selected_decryptors: dict[int, str],
) -> Optional[str]:
    """The decryptor selected in Production for an invention output.
    `selected_decryptors` is keyed by the manufactured T2 *item* (engine.
    _tech_ii_mods), while an invention job names the T2 *blueprint*."""
    if t2_blueprint_type_id is None:
        return None
    item = db.manufactured_product_of(int(t2_blueprint_type_id))
    name = selected_decryptors.get(item) if item is not None else None
    return name if name in DECRYPTORS else None


def _match_decryptor(
    probability: Optional[float],
    blueprint_type_id: int,
    product_type_id: Optional[int],
    installer_id: Optional[int],
    selected_decryptors: dict[int, str],
) -> tuple[Optional[int], str, Optional[int], Optional[int], Optional[int]]:
    """Returns (decryptor_type_id, source, enc, sci1, sci2).

    Phase 0 V8: implied multiplier = probability / (base x skill factor)
    with the installer's real skills, accepted only within
    _DECRYPTOR_MULT_TOLERANCE of a decryptor's multiplier ->
    `probability_decryptor`. Everything else is `assumed_decryptor`: the
    decryptor selected in Production for the product, else none. None and
    Symmetry share multiplier 1.0; the selection decides between them only
    when it is one of the two."""
    selected = _selected_decryptor(product_type_id, selected_decryptors)
    skill_factor, enc, sci1, sci2 = _invention_skill_factor(blueprint_type_id, installer_id)

    def assumed(name: Optional[str]):
        d = DECRYPTORS.get(name) if name else None
        return (d.type_id or None) if d else None, "assumed_decryptor", enc, sci1, sci2

    recipe = storage.get_invention_recipe(int(blueprint_type_id), product_type_id)
    base = recipe.get("base_probability") if recipe else None
    # A probability of 1.0 may be capped (min(1, ...)) and says nothing.
    if (probability is None or skill_factor is None or not base
            or float(probability) >= 1.0):
        return assumed(selected)

    implied = float(probability) / (float(base) * skill_factor)
    matches = [
        name for name, d in DECRYPTORS.items()
        if abs(d.probability_multiplier - implied) <= _DECRYPTOR_MULT_TOLERANCE
    ]
    if not matches:
        return assumed(selected)
    if set(matches) <= {"None", "Symmetry"}:
        if selected in ("None", "Symmetry"):
            d = DECRYPTORS[selected]
            return (d.type_id or None), "probability_decryptor", enc, sci1, sci2
        return assumed("None")
    d = DECRYPTORS[matches[0]]
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
        product_type_id=product_type_id_i,
        selected_decryptors=selected,
    )
    me = int(me or 0)
    material_mult, bonus_applied = _material_multiplier_for_job(
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
            structure_bonus_applied=bool(bonus_applied),
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
        inp.source, inp.structure_bonus_applied,
    )
