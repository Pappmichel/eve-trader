"""Location diagnostics (docs/LOCATION_RESOLUTION_PLAN.md phase 1): what kinds
of location ids this tenant's data holds, how many have a name, and what a
sync costs in ESI calls today. Read-only; nothing is resolved or written.

`build_report` is pure so it can be tested without a database; `collect`
gathers its inputs from storage for the current tenant.
"""
from __future__ import annotations

from typing import Iterable, Optional

STRUCTURE_ID_MIN = 1_000_000_000_000  # same bound production.esi_sync uses
SOLAR_SYSTEM_RANGE = (30_000_000, 33_000_000)
UNRESOLVED_LIMIT = 200

# Sources the bulk resolver's candidate list (storage.candidate_structure_
# location_ids) already covers; ids found only elsewhere are never looked up.
COVERED_SOURCES = frozenset({
    "character_assets", "corp_assets", "character_blueprints", "corp_blueprints",
    "character_industry_jobs", "corp_industry_jobs", "category_locations",
    "category_location_options", "manual_stock", "manual_blueprints",
})

KINDS = ("npc_station", "solar_system", "office", "container", "structure", "other")
# name states of a structure-sized id
RESOLVED, MANUAL, FAILED, NEVER = "resolved", "manual", "failed", "never_attempted"


def classify(location_id: int, stations: set[int], offices: set[int], items: frozenset[int] | set[int] = frozenset()) -> str:
    if location_id in stations:
        return "npc_station"
    if SOLAR_SYSTEM_RANGE[0] <= location_id < SOLAR_SYSTEM_RANGE[1]:
        return "solar_system"
    if location_id in offices:
        return "office"
    if location_id in items:
        return "container"
    if location_id >= STRUCTURE_ID_MIN:
        return "structure"
    return "other"


def build_report(
    sources: dict[str, set[int]],
    stations: set[int],
    offices: set[int],
    items: set[int],
    name_state: dict[int, str],
    capable_characters: int,
    corporations: int,
) -> dict:
    """`name_state` maps each structure-kind id to RESOLVED / MANUAL / FAILED /
    NEVER. `estimated_esi_calls_per_sync` is what the current bulk path spends
    on every run: one corporation-structure list per corp, plus one
    /universe/structures call per unresolved id per capable character (it
    stops at the first character that succeeds, so this is the failing case).
    """
    all_ids: set[int] = set().union(*sources.values()) if sources else set()
    kinds = {i: classify(i, stations, offices, items) for i in all_ids}

    by_kind = {k: 0 for k in KINDS}
    for k in kinds.values():
        by_kind[k] += 1

    structures = {RESOLVED: 0, MANUAL: 0, FAILED: 0, NEVER: 0}
    for i, k in kinds.items():
        if k == "structure":
            structures[name_state.get(i, NEVER)] += 1

    sources_of: dict[int, list[str]] = {}
    for label, ids in sources.items():
        for i in ids:
            sources_of.setdefault(i, []).append(label)

    by_source = {}
    for label, ids in sorted(sources.items()):
        by_source[label] = {
            "ids": len(ids),
            "structures": sum(1 for i in ids if kinds[i] == "structure"),
            "offices": sum(1 for i in ids if kinds[i] == "office"),
            "containers": sum(1 for i in ids if kinds[i] == "container"),
            "unresolved": sum(
                1 for i in ids if kinds[i] == "structure" and name_state.get(i, NEVER) in (FAILED, NEVER)
            ),
            "covered_by_bulk_resolve": label in COVERED_SOURCES,
        }

    unresolved = []
    for i in sorted(all_ids):
        k = kinds[i]
        state: Optional[str] = name_state.get(i, NEVER) if k == "structure" else None
        if k == "office" or state in (FAILED, NEVER):
            unresolved.append({
                "location_id": i, "kind": k, "state": state or "office",
                "sources": sorted(sources_of[i]),
                "reachable_by_bulk_resolve": k == "structure" and any(s in COVERED_SOURCES for s in sources_of[i]),
            })
    unresolved.sort(key=lambda r: (not r["reachable_by_bulk_resolve"], r["kind"], r["location_id"]))

    retried = structures[FAILED] + structures[NEVER]
    return {
        "totals": {"ids": len(all_ids), "by_kind": by_kind},
        "structures": structures,
        "by_source": by_source,
        "unresolved": unresolved[:UNRESOLVED_LIMIT],
        "unresolved_total": len(unresolved),
        "capable_characters": capable_characters,
        "estimated_esi_calls_per_sync": corporations + retried * capable_characters,
        "offices_resolved_through_parent": by_kind["office"],
        "containers_resolved_through_parent": by_kind["container"],
    }


def collect(capable_characters: int, corporations: int) -> dict:
    """Report for the current tenant."""
    from .. import storage

    sources = storage.location_id_sources()
    all_ids: set[int] = set().union(*sources.values()) if sources else set()
    stations, offices, items = storage.location_kind_hints(all_ids)
    structure_ids = [i for i in all_ids if classify(i, stations, offices, items) == "structure"]

    name_state: dict[int, str] = {}
    if structure_ids:
        cached = storage.get_cached_structure_names(structure_ids)
        global_names = storage.get_global_structure_names(structure_ids)
        names = storage.get_location_names(structure_ids)
        manual = _manual_ids(structure_ids)
        for i in structure_ids:
            was_cached, name = cached[i]
            if i in manual:
                name_state[i] = MANUAL
            elif name or i in global_names or names.get(i):
                name_state[i] = RESOLVED
            elif was_cached:
                name_state[i] = FAILED
            else:
                name_state[i] = NEVER
    return build_report(sources, stations, offices, items, name_state, capable_characters, corporations)


def _manual_ids(ids: Iterable[int]) -> set[int]:
    from .. import storage

    ids = list(ids)
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT location_id FROM manual_location_names WHERE location_id = ANY(?)", (ids,),
        ).fetchall()
    return {int(r[0]) for r in rows}
