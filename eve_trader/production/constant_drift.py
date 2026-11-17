"""Compare the hard-coded industry constants with the dogma of a fetched SDE.

Nothing here changes a constant or writes anything: the Admin SDE preview
shows the differences (`constant_drift` in the diff) and a human decides.

Attribute ids are never hard-coded. They are looked up by attribute name in
dgmAttributeTypes.csv at fetch time (sde._fetch_attribute_ids); a name the
dump does not know is reported as unchecked instead of being guessed.
Structure type bonuses are not compared (their dogma encoding is not
confirmed); the rig, decryptor and skill constants are.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import constants

# Dogma is stored as float4; a looser tolerance than that would hide a real
# change of a one-percent figure.
_REL_TOLERANCE = 1e-4

# The no-decryptor invention baseline (constants.DECRYPTORS["None"]): a
# decryptor's dogma modifier is a delta on top of it, the constant is the
# resulting absolute stat.
_BASE_ME = 2
_BASE_TE = 4

ATTR_SKILL_INDUSTRY = "manufacturingTimeBonus"
ATTR_SKILL_ADVANCED_INDUSTRY = "advancedIndustrySkillIndustryJobTimeBonus"
ATTR_SKILL_REACTIONS = "reactionTimeBonus"
ATTR_SKILL_SPECIALIST = "manufactureTimePerLevel"
ATTR_DECRYPTOR_PROBABILITY = "inventionPropabilityMultiplier"  # sic, CCP's spelling
ATTR_DECRYPTOR_ME = "inventionMEModifier"
ATTR_DECRYPTOR_TE = "inventionTEModifier"
ATTR_DECRYPTOR_RUNS = "inventionMaxRunModifier"
ATTR_RIG_MATERIAL = "attributeEngRigMatBonus"
ATTR_RIG_TIME = "attributeEngRigTimeBonus"

# Example Standup M-Set rigs whose ids and values the constants' comments
# already document (43867 / 43866: Advanced Component Manufacturing Material
# Efficiency I / II, 43876: Structure Manufacturing Time Efficiency I).
_RIGS = (
    (43867, ATTR_RIG_MATERIAL, "T1-Rig", "me_bonus"),
    (43866, ATTR_RIG_MATERIAL, "T2-Rig", "me_bonus"),
    (43876, ATTR_RIG_TIME, "T1-Rig", "te_bonus"),
)


@dataclass(frozen=True)
class Check:
    constant: str
    type_id: int
    attribute: str
    expected: float
    # Rig and skill bonuses are negative in dogma and positive in the
    # constants: compared by magnitude.
    magnitude_only: bool = False


def checks() -> list[Check]:
    out: list[Check] = []
    out.append(Check("INDUSTRY_TIME_BONUS_PCT", constants.SKILL_INDUSTRY, ATTR_SKILL_INDUSTRY,
                     constants.INDUSTRY_TIME_BONUS_PCT, True))
    out.append(Check("ADVANCED_INDUSTRY_TIME_BONUS_PCT", constants.SKILL_ADVANCED_INDUSTRY,
                     ATTR_SKILL_ADVANCED_INDUSTRY, constants.ADVANCED_INDUSTRY_TIME_BONUS_PCT, True))
    out.append(Check("REACTIONS_TIME_BONUS_PCT", constants.SKILL_REACTIONS, ATTR_SKILL_REACTIONS,
                     constants.REACTIONS_TIME_BONUS_PCT, True))
    for skill_id, pct in sorted(constants.SPECIALIST_TIME_SKILLS.items()):
        out.append(Check(f"SPECIALIST_TIME_SKILLS[{skill_id}]", skill_id, ATTR_SKILL_SPECIALIST, pct, True))
    for name, dec in constants.DECRYPTORS.items():
        if not dec.type_id:
            continue
        out.append(Check(f"DECRYPTORS[{name}].probability_multiplier", dec.type_id,
                         ATTR_DECRYPTOR_PROBABILITY, dec.probability_multiplier))
        out.append(Check(f"DECRYPTORS[{name}].run_bonus", dec.type_id, ATTR_DECRYPTOR_RUNS, dec.run_bonus))
        out.append(Check(f"DECRYPTORS[{name}].me_bonus", dec.type_id, ATTR_DECRYPTOR_ME,
                         dec.me_bonus - _BASE_ME))
        out.append(Check(f"DECRYPTORS[{name}].te_bonus", dec.type_id, ATTR_DECRYPTOR_TE,
                         dec.te_bonus - _BASE_TE))
    for type_id, attribute, tier, field in _RIGS:
        pct = getattr(constants.RIG_TIERS[tier], field) * 100.0
        out.append(Check(f"RIG_TIERS[{tier}].{field} (rig {type_id})", type_id, attribute, pct, True))
    return out


def wanted_type_ids() -> frozenset[int]:
    return frozenset(c.type_id for c in checks())


def wanted_attribute_names() -> frozenset[str]:
    return frozenset(c.attribute for c in checks())


def _equal(expected: float, actual: float, magnitude_only: bool) -> bool:
    if magnitude_only:
        expected, actual = abs(expected), abs(actual)
    return abs(expected - actual) <= _REL_TOLERANCE * max(1.0, abs(expected))


def compare(attribute_ids: dict[str, int], values: dict[int, dict[int, float]],
            type_names: dict[int, str]) -> dict:
    """{checked, drifts, unchecked}: `attribute_ids` maps attribute name ->
    id (from the dump), `values` is {typeID: {attributeID: value}} for the
    wanted types and attributes."""
    drifts: list[dict] = []
    unchecked: list[str] = []
    unknown_attributes: dict[str, int] = {}
    checked = 0
    for check in checks():
        attr_id = attribute_ids.get(check.attribute)
        label = f"{check.constant} ({type_names.get(check.type_id, check.type_id)})"
        if attr_id is None:
            unknown_attributes[check.attribute] = unknown_attributes.get(check.attribute, 0) + 1
            continue
        actual = values.get(check.type_id, {}).get(attr_id)
        if actual is None:
            unchecked.append(f"{label}: no {check.attribute} value for type {check.type_id}")
            continue
        checked += 1
        if not _equal(check.expected, actual, check.magnitude_only):
            drifts.append({
                "constant": check.constant,
                "type_id": check.type_id,
                "type_name": type_names.get(check.type_id),
                "attribute": check.attribute,
                "expected": check.expected,
                "actual": actual,
            })
    unchecked = [f"attribute {name} is not in the dump: {n} constant(s) not compared"
                 for name, n in sorted(unknown_attributes.items())] + unchecked
    return {"checked": checked, "drifts": drifts, "unchecked": unchecked}
