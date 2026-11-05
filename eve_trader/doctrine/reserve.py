"""Strategic reserve (GitHub issue #418): one hangar that holds a per-fitting
number of fits. Production and Trading treat everything in it as nonexistent,
Doctrine does not count it as stockpile and only names it as a hint next to a
shortfall. Realized, Portfolio and Sorting still see it: the items still exist.

The hangar is (owner, structure, division): a division flag alone repeats at
every structure and for every owner. Module-level imports stay free of
doctrine.engine, since engine and production.engine both read `active_hangar`.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from typing import Iterable, Optional

from .. import storage
from ..production.constants import HANGAR_DIVISION_FLAGS
from .config import DOCTRINE_CONFIG, DoctrineConfig

HANGAR_FIELDS = ("strategic_reserve_owner_type", "strategic_reserve_owner_id",
                 "strategic_reserve_location_id", "strategic_reserve_hangar_flag")

# tenant -> (checked_at, holds the doctrine grant). active_hangar runs once per
# stock read, so the grant lookup must not be a query every time.
_GRANT_TTL_SECONDS = 60.0
_grant_cache: dict[Optional[str], tuple[float, bool]] = {}
_grant_lock = threading.Lock()


def invalidate_cache() -> None:
    with _grant_lock:
        _grant_cache.clear()


def _holds_doctrine_grant() -> bool:
    from .. import tenant_eligibility

    tenant_id = storage.get_current_tenant()
    if tenant_id is None:
        return True
    now = time.time()
    with _grant_lock:
        cached = _grant_cache.get(tenant_id)
    if cached is not None and now - cached[0] < _GRANT_TTL_SECONDS:
        return cached[1]
    held = tenant_eligibility.may_use("doctrine", tenant_eligibility.granted_tools(str(tenant_id)))
    with _grant_lock:
        _grant_cache[tenant_id] = (now, held)
    return held


def configured_hangar(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> Optional[storage.ExcludedHangar]:
    if not (cfg.strategic_reserve_owner_type and cfg.strategic_reserve_owner_id
            and cfg.strategic_reserve_location_id and cfg.strategic_reserve_hangar_flag):
        return None
    return storage.ExcludedHangar(
        owner_type=cfg.strategic_reserve_owner_type, owner_id=int(cfg.strategic_reserve_owner_id),
        location_id=int(cfg.strategic_reserve_location_id), flag=cfg.strategic_reserve_hangar_flag)


def active_hangar(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> Optional[storage.ExcludedHangar]:
    """The hangar other readers must skip, or None. A tenant that lost the
    `doctrine` grant can no longer see or change the reserve, so its stock must
    not stay hidden from Production and Trading."""
    hangar = configured_hangar(cfg)
    if hangar is None or not _holds_doctrine_grant():
        return None
    return hangar


def is_reserve_row(row: dict, hangar: Optional[storage.ExcludedHangar]) -> bool:
    """For accessor rows, which carry their own owner."""
    return storage.is_in_hangar(row, row.get("owner_type"), row.get("owner_id"), hangar)


def without_reserve(rows: list[dict], hangar: Optional[storage.ExcludedHangar]) -> list[dict]:
    if hangar is None:
        return rows
    return [r for r in rows if not is_reserve_row(r, hangar)]


def stock_by_type(asset_rows: list[dict], type_ids: Iterable[int],
                  cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict[int, float]:
    """Loose stock per type in the reserve hangar. Assembled ships and their
    fitting do not count, same rule as every other stock figure."""
    hangar = configured_hangar(cfg)
    if hangar is None:
        return {}
    rows = [r for r in asset_rows if is_reserve_row(r, hangar)]
    return storage.esi_stock_from_asset_rows(rows, list(type_ids), None)


def shared_pool(stock: dict[int, float], type_id: int, lookup) -> float:
    """Reserve stock usable for `type_id`: its own plus equivalent variants."""
    return sum(stock.get(t, 0.0) for t in (lookup(type_id) if lookup else (type_id,)))


def _sets_covered(per_set: dict[int, tuple[float, str]], allocated: dict[int, float]) -> int:
    """Complete sets an allocation holds. An empty requirement is 0 sets."""
    lines = [(t, qty) for t, (qty, _cls) in per_set.items() if qty > 0]
    if not lines:
        return 0
    return min(int(math.floor(allocated.get(t, 0.0) / qty + 1e-9)) for t, qty in lines)


def _soll(candidate, target: int, include_hulls: bool) -> dict[int, tuple[float, str]]:
    from . import validation

    soll = validation.build_stockpile_soll(candidate.items, candidate.fitting.hull_type_id, target)
    if not include_hulls:
        hull = candidate.fitting.hull_type_id
        qty, item_class = soll[hull]
        # The hull type can also sit in a cargo line: only its own unit goes.
        if qty - target > 0:
            soll[hull] = (qty - target, item_class)
        else:
            del soll[hull]
    return soll


def status(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """The Strategic Reserve tab: per fitting its target, the complete sets the
    hangar holds and what is missing. Fittings draw on the hangar in Doctrine
    priority order, so a module two fits share is never counted twice (unlike
    Assemblable now, where the checked fit gets everything)."""
    from ..esi_data.access import is_shared, read_esi
    from . import engine, validation

    hangar = configured_hangar(cfg)
    include_hulls = bool(cfg.strategic_reserve_include_hulls)
    candidates = engine.load_match_candidates()
    doctrine_names = {str(row[0]): row[1] for row in storage.list_doctrines()}
    result: dict = {"state": "not_configured", "hangar": None, "include_hulls": include_hulls,
                    "assembled_ships_not_counted": 0, "fittings": [], "below_target": [], "missing": []}

    per_fit: list[tuple] = []
    ordered: list[tuple[str, dict[int, tuple[float, str]]]] = []
    for c in candidates:
        target = max(0, int(c.fitting.reserve_target or 0))
        soll = _soll(c, target, include_hulls) if target > 0 else {}
        if target > 0:
            ordered.append((c.fitting.fitting_id, soll))
        per_fit.append((c, target, soll))

    allocation: dict[str, dict[int, float]] = {}
    sde: dict = {}
    if hangar is not None:
        result["hangar"] = _describe_hangar(hangar)
        if not is_shared("assets", "doctrine", hangar.owner_type, hangar.owner_id):
            result["state"] = "not_shared"
        else:
            result["state"] = "ok"
            rows = [r for r in read_esi("assets", "doctrine") if is_reserve_row(r, hangar)]
            result["assembled_ships_not_counted"] = sum(
                1 for r in rows
                if r.get("assembled_or_fitted") and not storage.is_fitted_location_flag(r.get("location_flag")))
            lookup = engine.equivalents_of(cfg)
            wanted = engine._with_equivalents({t for _fid, soll in ordered for t in soll}, lookup)
            available = storage.esi_stock_from_asset_rows(rows, list(wanted), None)
            allocation, _variants = validation.allocate_stockpile_with_equivalents(ordered, available, lookup)
            sde = storage.get_sde_types_bulk(list(wanted))

    totals: dict[int, dict] = defaultdict(lambda: {"required": 0.0, "on_hand": 0.0, "missing": 0.0, "fitting_count": 0})
    hulls = storage.get_sde_types_bulk(list({c.fitting.hull_type_id for c, _t, _s in per_fit}))
    for c, target, soll in per_fit:
        row = {
            "fitting_id": c.fitting.fitting_id, "fitting_name": c.fitting.name,
            "doctrine_name": doctrine_names.get(c.fitting.doctrine_id, c.fitting.doctrine_id),
            "hull_name": hulls[c.fitting.hull_type_id][2] if c.fitting.hull_type_id in hulls else str(c.fitting.hull_type_id),
            "reserve_target": target, "complete_sets": None, "missing_units": None,
        }
        if result["state"] == "ok" and target > 0:
            allocated = allocation.get(c.fitting.fitting_id, {})
            per_set = {t: (qty / target, cls) for t, (qty, cls) in soll.items()}
            row["complete_sets"] = _sets_covered(per_set, allocated)
            row["missing_units"] = 0.0
            for type_id, (required, _cls) in soll.items():
                have = allocated.get(type_id, 0.0)
                short = max(0.0, required - have)
                row["missing_units"] += short
                entry = totals[type_id]
                entry["required"] += required
                entry["on_hand"] += have
                entry["missing"] += short
                entry["fitting_count"] += 1
            if row["complete_sets"] < target:
                result["below_target"].append({"fitting_id": row["fitting_id"], "fitting_name": row["fitting_name"],
                                               "complete_sets": row["complete_sets"], "reserve_target": target})
        result["fittings"].append(row)

    result["missing"] = sorted(
        ({"type_id": t, "type_name": sde[t][2] if t in sde else str(t), **v}
         for t, v in totals.items() if v["missing"] > 1e-9),
        key=lambda r: (-r["missing"], r["type_name"].lower()))
    return result


def _describe_hangar(hangar: storage.ExcludedHangar) -> dict:
    names = storage.get_location_names([hangar.location_id])
    return {"owner_type": hangar.owner_type, "owner_id": hangar.owner_id, "location_id": hangar.location_id,
            "location_name": names.get(hangar.location_id), "hangar_flag": hangar.flag}


def hangar_options() -> list[dict]:
    """Every hangar that holds assets shared with Doctrine, for the Settings
    picker: only combinations that really exist can be chosen."""
    from ..esi_data.access import read_esi

    groups: dict[tuple, dict] = {}
    for r in read_esi("assets", "doctrine"):
        location_id, flag = r.get("resolved_location_id"), r.get("resolved_hangar_flag")
        if location_id is None or flag not in HANGAR_DIVISION_FLAGS:
            continue
        key = (r["owner_type"], r["owner_id"], int(location_id), flag)
        group = groups.setdefault(key, {"owner_name": r.get("owner_name"), "item_rows": 0})
        group["item_rows"] += 1
    names = storage.get_location_names(sorted({k[2] for k in groups})) if groups else {}
    corp_ids = sorted({k[1] for k in groups if k[0] == "corporation"})
    divisions = {(corp, f"CorpSAG{division}"): name
                 for corp, kind, division, name in storage.load_corp_divisions(corp_ids)
                 if kind == "hangar" and name}
    options = [{"owner_type": owner_type, "owner_id": owner_id, "owner_name": g["owner_name"],
                "location_id": location_id, "location_name": names.get(location_id),
                "hangar_flag": flag, "division_name": divisions.get((owner_id, flag)), "item_rows": g["item_rows"]}
               for (owner_type, owner_id, location_id, flag), g in groups.items()]
    options.sort(key=lambda o: (o["owner_name"] or "", o["location_name"] or "", o["hangar_flag"]))
    return options
