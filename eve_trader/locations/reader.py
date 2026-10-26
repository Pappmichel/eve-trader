"""The one reader for "what is this location id" (docs/LOCATION_RESOLUTION_PLAN.md
2.2). Two queries cover names, kinds and parents (one tenant-scoped, one for the
shared global names) plus one for system/region, instead of up to five
sequential lookups per caller.

Name tiers keep the order storage.get_location_names always had: the tenant's
structure_names, then the registry, then the shared global names, then the
tenant's manual names, then SDE stations. An Office or container shows its
parent's name and system (its own id has no name).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .. import storage

SOLAR_SYSTEM_RANGE = (30_000_000, 33_000_000)


@dataclass
class LocationInfo:
    location_id: int
    name: Optional[str] = None
    kind: str = "unknown"          # npc_station, solar_system, structure, office, container, other, unknown
    status: Optional[str] = None   # registry status; None when the id is not registered
    parent_id: Optional[int] = None
    solar_system_id: Optional[int] = None
    solar_system_name: Optional[str] = None
    security: Optional[float] = None
    region_id: Optional[int] = None
    region_name: Optional[str] = None

    @property
    def effective_id(self) -> int:
        return self.parent_id or self.location_id


_RAW_SQL = (
    "SELECT i.id, sn.name, sn.solar_system_id, r.kind, r.parent_location_id, r.name, r.solar_system_id, "
    "r.status, m.name, st.station_name, st.solar_system_id "
    "FROM unnest(?::bigint[]) AS i(id) "
    "LEFT JOIN structure_names sn ON sn.location_id = i.id "
    "LEFT JOIN location_registry r ON r.location_id = i.id "
    "LEFT JOIN manual_location_names m ON m.location_id = i.id "
    "LEFT JOIN sde_stations st ON st.station_id = i.id"
)


def _raw(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    with storage.connect() as conn:
        rows = conn.execute(_RAW_SQL, (ids,)).fetchall()
    out = {}
    for (i, sn_name, sn_sys, r_kind, r_parent, r_name, r_sys, r_status, m_name, st_name, st_sys) in rows:
        out[int(i)] = {
            "structure_name": sn_name, "structure_sys": sn_sys, "kind": r_kind, "parent": r_parent,
            "registry_name": r_name, "registry_sys": r_sys, "status": r_status, "manual": m_name,
            "station_name": st_name, "station_sys": st_sys,
        }
    return out


def describe(location_ids: Iterable[int]) -> dict[int, LocationInfo]:
    """LocationInfo for every requested id (unknown ids come back empty, never
    guessed)."""
    ids = sorted({int(i) for i in location_ids})
    if not ids:
        return {}
    raw = _raw(ids)
    parents = sorted({
        int(r["parent"]) for r in raw.values()
        if r["kind"] in ("office", "container") and r["parent"] and int(r["parent"]) not in raw
    })
    raw.update(_raw(parents))

    needs_global = [i for i, r in raw.items() if not (r["structure_name"] or r["registry_name"])]
    global_names = storage.get_global_structure_names(needs_global) if needs_global else {}

    def own(i: int) -> tuple[Optional[str], Optional[int]]:
        r = raw[i]
        name = r["structure_name"] or r["registry_name"]
        system = r["structure_sys"] or r["registry_sys"]
        if not name and i in global_names:
            name, g_sys = global_names[i]
            system = system or g_sys
        name = name or r["manual"] or r["station_name"]
        system = system or r["station_sys"]
        if system is None and SOLAR_SYSTEM_RANGE[0] <= i < SOLAR_SYSTEM_RANGE[1]:
            system = i
        return name, system

    resolved: dict[int, tuple[Optional[str], Optional[int], Optional[int]]] = {}
    for i in ids:
        r = raw[i]
        name, system = own(i)
        parent = int(r["parent"]) if r["kind"] in ("office", "container") and r["parent"] else None
        if parent is not None and parent in raw:
            p_name, p_system = own(parent)
            name, system = name or p_name, system or p_system
        resolved[i] = (name, system, parent)

    system_ids = sorted({s for _n, s, _p in resolved.values() if s is not None})
    systems: dict[int, tuple] = {}
    if system_ids:
        with storage.connect() as conn:
            systems = {
                int(r[0]): (r[1], r[2], r[3], r[4])
                for r in conn.execute(
                    "SELECT s.solar_system_id, s.solar_system_name, s.security, s.region_id, g.region_name "
                    "FROM sde_solar_systems s LEFT JOIN sde_regions g ON g.region_id = s.region_id "
                    "WHERE s.solar_system_id = ANY(?)", (system_ids,),
                ).fetchall()
            }

    out: dict[int, LocationInfo] = {}
    for i in ids:
        name, system, parent = resolved[i]
        sys_name, security, region_id, region_name = systems.get(system, (None, None, None, None))
        if name is None and system == i:
            name = sys_name  # an id in the solar-system range is its own system
        r = raw[i]
        kind = r["kind"] or (
            "npc_station" if r["station_name"] else
            "solar_system" if SOLAR_SYSTEM_RANGE[0] <= i < SOLAR_SYSTEM_RANGE[1] else "unknown"
        )
        out[i] = LocationInfo(
            location_id=i, name=name, kind=kind, status=r["status"], parent_id=parent,
            solar_system_id=system, solar_system_name=sys_name, security=security,
            region_id=region_id, region_name=region_name,
        )
    return out
