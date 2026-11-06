"""Ready to list (GitHub issue #429): one hangar that holds assembled ships
ready to be contracted. The Doctrine Detail table's Ready-to-list column
counts ships whose fit validates as valid or tolerable (same rules as
contracts). Assembled ships are never stock, so this hangar is not excluded
from Production or Trading the way the strategic reserve is.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .. import storage
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import VALIDATION_TOLERABLE, VALIDATION_VALID

HANGAR_FIELDS = ("ready_to_list_owner_type", "ready_to_list_owner_id",
                 "ready_to_list_location_id", "ready_to_list_hangar_flag")


def configured_hangar(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> Optional[storage.ExcludedHangar]:
    if not (cfg.ready_to_list_owner_type and cfg.ready_to_list_owner_id
            and cfg.ready_to_list_location_id and cfg.ready_to_list_hangar_flag):
        return None
    return storage.ExcludedHangar(
        owner_type=cfg.ready_to_list_owner_type, owner_id=int(cfg.ready_to_list_owner_id),
        location_id=int(cfg.ready_to_list_location_id), flag=cfg.ready_to_list_hangar_flag)


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


def _inside_another_item(row: dict, by_item: dict[int, dict]) -> bool:
    """A ship inside another ship's bay is that ship's content, not one more
    ship ready to list. A corp Office is an item too, but only the hangar."""
    parent = by_item.get(row["location_id"])
    return parent is not None and parent.get("type_id") != storage.OFFICE_TYPE_ID


def counts(cfg: DoctrineConfig = DOCTRINE_CONFIG,
           candidates: Optional[list] = None) -> Optional[dict[str, int]]:
    """Assembled ships in the hangar that validate as valid or tolerable,
    pooled by fit identity (same as active contracts). None when no hangar is
    set, the hangar owner does not share assets with Doctrine, or that owner
    has no asset rows at all (not synced yet, or cleared as stale: unknown, not
    an empty hangar). A hangar with nothing ready is 0."""
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

    rows = read_esi("assets", "doctrine")
    owner_rows = [r for r in rows
                  if r.get("owner_type") == hangar.owner_type and r.get("owner_id") == hangar.owner_id]
    if not owner_rows:
        return None
    children: dict[int, list[dict]] = defaultdict(list)
    by_item = {r["item_id"]: r for r in owner_rows}
    for r in owner_rows:
        children[r["location_id"]].append(r)

    ships = [
        r for r in owner_rows
        if is_hangar_row(r, hangar)
        and r.get("assembled_or_fitted")
        and not storage.is_fitted_location_flag(r.get("location_flag"))
        and not _inside_another_item(r, by_item)
    ]

    by_identity: dict[tuple, int] = defaultdict(int)
    for ship in ships:
        # The hull must be the ship itself: a carrier holding a matching ship
        # in its bay is not that fit.
        same_hull = [c for c in candidates if c.fitting.hull_type_id == ship["type_id"]]
        if not same_hull:
            continue
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
            0, None, items, same_hull, cfg)
        if fitting_id is None or status not in (VALIDATION_VALID, VALIDATION_TOLERABLE):
            continue
        key = identity_by_fitting.get(str(fitting_id))
        if key is not None:
            by_identity[key] += 1

    return {fid: by_identity[key] for fid, key in identity_by_fitting.items()}
