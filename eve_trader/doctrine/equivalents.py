"""Equivalent module variants as Doctrine sees them: the SDE classes
(eve_trader/sde_equivalence.py, `sde_type_equivalents`) plus the tenant's own
edits from the Equivalents tab (docs/doctrine_schema.sql).

Every edit names items, never a class id, because an SDE update recomputes
the classes (and their ids) from scratch:

- `doctrine_equivalent_overrides`: an item taken out of its group
  (`excluded`), or put into the group of another item (`anchor_type_id`).
  When the anchor has no group (any more), the two form a manual group.
- `doctrine_equivalent_disabled_groups`: the group that contains this item
  is switched off.

So a group the SDE update grows, shrinks or splits keeps the tenant's edits
on the items they were made for; edits on items the SDE no longer groups
simply have no effect.

`effective_classes` is pure; `lookup()` reads the tenant's rows and caches
the result per tenant for a minute (`invalidate()` on every write and on an
SDE apply), since contract matching asks once per contract.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from .. import storage

_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, dict[int, int], dict[int, tuple[int, ...]]]] = {}
_lock = threading.Lock()


def assigned_classes(sde_class_of: dict[int, int],
                     overrides: list[tuple[int, Optional[int], bool]]) -> dict[int, int]:
    """{type_id: class_id} after exclusions and manual adds, before disabled
    groups and the two-member minimum. `overrides` rows are
    (type_id, anchor_type_id, excluded). A manual group without an SDE class
    gets the id minus its anchor's type id, so it never collides with an SDE
    class id (always a positive type id)."""
    class_of = dict(sde_class_of)
    for type_id, _anchor, excluded in overrides:
        if excluded:
            class_of.pop(type_id, None)
    pending = {t: a for t, a, x in overrides if not x and a is not None and t != a}
    # An anchor that is itself an added item resolves after its own anchor;
    # a cycle falls back to a manual group per anchor.
    while pending:
        ready = [(t, a) for t, a in pending.items() if a not in pending]
        if not ready:
            ready = list(pending.items())
        for type_id, anchor in ready:
            class_of[type_id] = class_of.setdefault(anchor, -anchor)
            del pending[type_id]
    return class_of


def effective_classes(sde_class_of: dict[int, int], overrides: list[tuple[int, Optional[int], bool]],
                      disabled_types: set[int]) -> tuple[dict[int, int], dict[int, tuple[int, ...]]]:
    """({type_id: class_id}, {class_id: members}) the tenant actually uses.
    A group containing one of `disabled_types` is switched off; groups left
    with fewer than two members drop out."""
    class_of = assigned_classes(sde_class_of, overrides)
    disabled = {class_of[t] for t in disabled_types if t in class_of}
    members: dict[int, list[int]] = {}
    for type_id, class_id in class_of.items():
        if class_id not in disabled:
            members.setdefault(class_id, []).append(type_id)
    kept = {c: tuple(sorted(ts)) for c, ts in members.items() if len(ts) > 1}
    return {t: c for t, c in class_of.items() if c in kept}, kept


def _load() -> tuple[dict[int, int], dict[int, tuple[int, ...]]]:
    tenant = str(storage.get_current_tenant())
    now = time.time()
    with _lock:
        hit = _cache.get(tenant)
        if hit is not None and now - hit[0] < _TTL_SECONDS:
            return hit[1], hit[2]
    class_of, members = effective_classes(
        storage.get_sde_equivalence_class_map(), storage.list_doctrine_equivalent_overrides(),
        storage.list_doctrine_disabled_equivalent_groups())
    with _lock:
        _cache[tenant] = (now, class_of, members)
    return class_of, members


def invalidate() -> None:
    with _lock:
        _cache.clear()


def lookup():
    """type_id -> every type of its effective class (itself included), or
    (type_id,) when it has none. Built once per call site."""
    class_of, members = _load()

    def _equivalents(type_id: int) -> tuple[int, ...]:
        class_id = class_of.get(int(type_id))
        return members[class_id] if class_id is not None else (int(type_id),)

    return _equivalents


def effective_class_of(type_id: int) -> Optional[int]:
    return _load()[0].get(int(type_id))
