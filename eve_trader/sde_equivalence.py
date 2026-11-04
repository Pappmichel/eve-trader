"""Functionally identical item variants, derived from the SDE.

Two types are equivalent when they are published, market-listed, in the same
group of one of EQUIVALENCE_CATEGORY_IDS, carry the same set of dogma effects
and the same dogma attributes (CPU, powergrid, calibration, heat, skill
requirements and every bonus) apart from IGNORED_ATTRIBUTE_IDS, and have the
same invTypes volume, capacity and mass (PHYSICAL_COLUMNS). Example: True
Sansha and Dark Blood Thermal Armor Hardener. Not equivalent: Cap Booster 800
and Navy Cap Booster 800 - same dogma, but the Navy charge has 75% of the
volume, which is not a dogma attribute (user report 2026-11-04).

Pure: input is already-parsed SDE rows, output is {type_id: class_id}.
production/sde.py calls it during an SDE fetch; the result is stored in
`sde_type_equivalents` (docs/doctrine_schema.sql) and read by Doctrine,
which accepts any member of a class in place of another (contracts,
stockpile, shopping list).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Optional

#: Module, Charge, Drone, Subsystem, Fighter.
EQUIVALENCE_CATEGORY_IDS = frozenset({7, 8, 18, 32, 87})

#: Attributes that only describe where a type sits in the meta hierarchy and
#: have no in-game effect. Checked against True Sansha vs Dark Blood Thermal
#: Armor Hardener (identical apart from these, 2026-11-04).
IGNORED_ATTRIBUTE_IDS = frozenset({
    633,   # metaLevel
    1692,  # metaGroupID
})

#: invTypes columns that are part of an item but not dogma attributes. They
#: matter for cargo space and hauling, so a difference keeps types apart.
PHYSICAL_COLUMNS = ("volume", "capacity", "mass")

#: Abyssal / mutated templates: their real stats come from the mutation,
#: the base type's attributes say nothing.
EXCLUDED_META_GROUP_IDS = frozenset({15})

#: Cosmetic charges: all fireworks share one attribute set.
EXCLUDED_GROUP_NAMES = frozenset({"Festival Charges", "Structure Festival Charges"})


def candidate_type_ids(types: Iterable[dict], groups: Iterable[dict],
                       meta_group_by_type: dict[int, Optional[int]]) -> frozenset[int]:
    """Type ids worth comparing, from invTypes / invGroups CSV rows."""
    group_ok = {
        int(g["groupID"]) for g in groups
        if _int(g.get("categoryID")) in EQUIVALENCE_CATEGORY_IDS
        and g.get("groupName") not in EXCLUDED_GROUP_NAMES
    }
    out = set()
    for t in types:
        if str(t.get("published")) != "1" or _int(t.get("marketGroupID")) is None:
            continue
        if _int(t.get("groupID")) not in group_ok:
            continue
        type_id = int(t["typeID"])
        if meta_group_by_type.get(type_id) in EXCLUDED_META_GROUP_IDS:
            continue
        out.add(type_id)
    return frozenset(out)


def _round(value: float) -> float:
    """Six significant digits: the dump carries float noise on some values."""
    if value == 0 or not math.isfinite(value):
        return value
    return round(value, 5 - int(math.floor(math.log10(abs(value)))))


def physical_properties(types: Iterable[dict], type_ids: frozenset[int]) -> dict[int, tuple[float, ...]]:
    """{type_id: (volume, capacity, mass)} from invTypes CSV rows, for
    equivalence_classes. A missing value counts as 0."""
    out = {}
    for t in types:
        type_id = int(t["typeID"])
        if type_id in type_ids:
            out[type_id] = tuple(_round(_float(t.get(col))) for col in PHYSICAL_COLUMNS)
    return out


def equivalence_classes(group_by_type: dict[int, int],
                        attributes: dict[int, dict[int, float]],
                        effects: dict[int, Iterable[int]],
                        physical: Optional[dict[int, tuple[float, ...]]] = None) -> dict[int, int]:
    """{type_id: class_id} for every type that has at least one equivalent.
    class_id is the smallest type id in the class, so it stays stable across
    SDE refreshes as long as the members do. Types without any attribute row
    are skipped (nothing to compare). `physical` (physical_properties) must
    match too; a type missing from it compares as having none."""
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for type_id, group_id in group_by_type.items():
        attrs = attributes.get(type_id)
        if not attrs:
            continue
        key = (
            group_id,
            (physical or {}).get(type_id, ()),
            tuple(sorted(effects.get(type_id, ()))),
            tuple(sorted((a, _round(v)) for a, v in attrs.items() if a not in IGNORED_ATTRIBUTE_IDS)),
        )
        buckets[key].append(type_id)
    result: dict[int, int] = {}
    for members in buckets.values():
        if len(members) < 2:
            continue
        class_id = min(members)
        for type_id in members:
            result[type_id] = class_id
    return result


def _float(v) -> float:
    if v in (None, "", "None"):
        return 0.0
    return float(v)


def _int(v) -> Optional[int]:
    if v in (None, "", "None"):
        return None
    return int(float(v))
