"""Ready to list (GitHub issue #429): one hangar that holds assembled ships
ready to be contracted. The Doctrine Detail table's Ready-to-list column
counts ships whose fit validates as valid or tolerable (same rules as
contracts). Assembled ships are never stock, so this hangar is not excluded
from Production or Trading the way the strategic reserve is.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Optional

from .. import storage
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import VALIDATION_TOLERABLE, VALIDATION_VALID

HANGAR_FIELDS = ("ready_to_list_owner_type", "ready_to_list_owner_id",
                 "ready_to_list_location_id", "ready_to_list_hangar_flag",
                 "ready_to_list_container_id")

_SUBTREE_TTL_SECONDS = 60.0
_subtree_cache: dict[tuple, tuple[float, frozenset]] = {}
_subtree_lock = threading.Lock()


def invalidate_cache() -> None:
    with _subtree_lock:
        _subtree_cache.clear()


def _container_items(owner_type: str, owner_id: int, container_id: int) -> frozenset:
    key = (storage.get_current_tenant(), owner_type, owner_id, container_id)
    now = time.time()
    with _subtree_lock:
        cached = _subtree_cache.get(key)
    if cached is not None and now - cached[0] < _SUBTREE_TTL_SECONDS:
        return cached[1]
    items = storage.asset_subtree_item_ids(owner_type, owner_id, container_id)
    with _subtree_lock:
        _subtree_cache[key] = (now, items)
    return items


def configured_hangar(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> Optional[storage.ExcludedHangar]:
    if not (cfg.ready_to_list_owner_type and cfg.ready_to_list_owner_id
            and cfg.ready_to_list_location_id and cfg.ready_to_list_hangar_flag):
        return None
    owner_type, owner_id = cfg.ready_to_list_owner_type, int(cfg.ready_to_list_owner_id)
    container = int(cfg.ready_to_list_container_id) if cfg.ready_to_list_container_id else None
    return storage.ExcludedHangar(
        owner_type=owner_type, owner_id=owner_id,
        location_id=int(cfg.ready_to_list_location_id), flag=cfg.ready_to_list_hangar_flag,
        container_item_id=container,
        item_ids=_container_items(owner_type, owner_id, container) if container else frozenset())


def is_hangar_row(row: dict, hangar: Optional[storage.ExcludedHangar]) -> bool:
    return storage.is_in_hangar(row, row.get("owner_type"), row.get("owner_id"), hangar)


def _ship_contents(ship_item_id: int, children: dict[int, list[dict]]) -> list[dict]:
    """Every asset nested under the ship (fitted modules, cargo, containers)."""
    contents: list[dict] = []
    seen = {ship_item_id}
    stack = [ship_item_id]
    while stack:
        for child in children.get(stack.pop(), ()):
            item_id = child["item_id"]
            if item_id in seen:
                continue
            seen.add(item_id)
            contents.append(child)
            stack.append(item_id)
    return contents


def counts(cfg: DoctrineConfig = DOCTRINE_CONFIG,
           candidates: Optional[list] = None) -> Optional[dict[str, int]]:
    """Assembled ships in the hangar that validate as valid or tolerable,
    pooled by fit identity (same as active contracts). None when no hangar is
    set or the hangar owner does not share assets with Doctrine; an empty
    hangar (or a missing container) is 0, not None."""
    from ..esi_data.access import is_shared, read_esi
    from . import engine
    from .models import ContractItemRow

    hangar = configured_hangar(cfg)
    if hangar is None:
        return None
    if not is_shared("assets", "doctrine", hangar.owner_type, hangar.owner_id):
        return None
    if candidates is None:
        candidates = engine.load_match_candidates()
    identity_by_fitting = {c.fitting.fitting_id: engine._fit_identity(c) for c in candidates}
    zeros = {fid: 0 for fid in identity_by_fitting}
    if hangar.container_item_id is not None and not hangar.item_ids:
        return zeros

    rows = read_esi("assets", "doctrine")
    owner_rows = [r for r in rows
                  if r.get("owner_type") == hangar.owner_type and r.get("owner_id") == hangar.owner_id]
    children: dict[int, list[dict]] = defaultdict(list)
    for r in owner_rows:
        children[r["location_id"]].append(r)

    ships = [
        r for r in owner_rows
        if is_hangar_row(r, hangar)
        and r.get("assembled_or_fitted")
        and not storage.is_fitted_location_flag(r.get("location_flag"))
    ]

    by_identity: dict[tuple, int] = defaultdict(int)
    for ship in ships:
        contents = _ship_contents(ship["item_id"], children)
        items = [ContractItemRow(
            contract_id=0, record_id=0, type_id=ship["type_id"],
            quantity=float(ship.get("quantity") or 1), is_included=True, is_singleton=True)]
        for i, child in enumerate(contents, start=1):
            items.append(ContractItemRow(
                contract_id=0, record_id=i, type_id=child["type_id"],
                quantity=float(child.get("quantity") or 1), is_included=True,
                is_singleton=bool(child.get("is_singleton"))))
        fitting_id, _score, _devs, status = engine.match_and_validate_contract(
            0, None, items, candidates, cfg)
        if fitting_id is None or status not in (VALIDATION_VALID, VALIDATION_TOLERABLE):
            continue
        key = identity_by_fitting.get(str(fitting_id))
        if key is not None:
            by_identity[key] += 1

    return {fid: by_identity[key] for fid, key in identity_by_fitting.items()}
