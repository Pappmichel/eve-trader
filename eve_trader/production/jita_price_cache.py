"""Server-side, cross-tenant shared cache for Jita current-price quotes.

Jita's region order book is public ESI data, identical for every tenant -
unlike home_prices (tied to a specific producer character's own token,
per-tenant by nature), there's nothing tenant-specific to key this by, so
one shared, process-wide snapshot correctly serves everyone. Refreshed on
an hourly cadence by a global scheduler job (scheduler.py, mirroring
backup.py's own global/unscoped job) rather than on every plan_production()
call - see that module's own docstring for why a live per-request fetch
(~850 individual ESI calls, ~15s) was worth moving out of the request path.

Deliberately its own module rather than folded into pricing.py or
esi_client.py: pricing.jita_prices() only *reads* this cache (falling back
to a live per-type ESI/Goonmetrics fetch for whatever isn't cached yet, e.g.
a stock target added since the last refresh); the *write* side
(refresh_jita_price_cache) is triggered independently - either by the
hourly scheduler tick or by the standalone manual "refresh now" action
(admin.do_refresh_jita_price_cache, GitHub-issue-#34-style: cross-tenant-
impacting caches live in the Admin tool, not a per-tenant Production
button) - never automatically from inside jita_prices()/plan_production()
itself, so a slow/failed refresh never blocks or gets conflated with the
Buy/Build list computation it exists to speed up.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Optional

from .. import storage
from ..goonmetrics_client import CurrentPrice

log = logging.getLogger("eve_trader.production.jita_price_cache")

# This is a Jita-only cache (The Forge) - process-wide, cross-tenant, no region
# in its key. Consumers with another hub must bypass it (#222).
JITA_REGION_ID = 10000002

# Soft cap on Doctrine hulls/items/equivalents added on top of Production's
# stock-target universe. Above this the Doctrine half is skipped (and
# logged) so a huge tenant cannot blow the shared ESI budget; Production
# types still refresh.
_MAX_DOCTRINE_EXTRA_TYPES = 2000

_lock = threading.Lock()
_cache: dict[int, CurrentPrice] = {}
_updated_at: Optional[str] = None
# Last refresh's Doctrine-only addition (for tests / operator report).
_last_doctrine_extra_count: int = 0
_last_doctrine_extra_skipped: bool = False


def get_cached_prices(type_ids: list[int]) -> dict[int, CurrentPrice]:
    """Whichever of `type_ids` are currently cached - callers (pricing.
    jita_prices) fetch the rest live. Empty (not stale-but-served) until the
    first refresh has actually run, e.g. right after a fresh deploy/restart -
    self-healing, since jita_prices' own live fallback covers that case."""
    with _lock:
        return {tid: _cache[tid] for tid in type_ids if tid in _cache}


def last_updated_at() -> Optional[str]:
    return _updated_at


def cached_type_id_count() -> int:
    with _lock:
        return len(_cache)


def last_doctrine_extra_count() -> int:
    """How many type_ids the last refresh added solely from Doctrine
    fittings (0 when skipped or none)."""
    return _last_doctrine_extra_count


def last_doctrine_extra_skipped() -> bool:
    """True when the last refresh dropped the Doctrine half because it
    would have exceeded `_MAX_DOCTRINE_EXTRA_TYPES`."""
    return _last_doctrine_extra_skipped


def _production_type_ids(last_active: dict) -> set[int]:
    from .. import tenant_eligibility, tenant_scope
    from .engine import _structural_material_closure

    type_ids: set[int] = set()
    for tenant_id, _name, _created_at in storage.list_tenants():
        # Only tenants that can actually use Production prices and are still
        # around cost ESI calls here (docs/SCHEDULER_REWORK_PLAN.md decision 4).
        # Also applies to the manual admin refresh - same function.
        if not tenant_eligibility.is_active(str(tenant_id), last_active):
            continue
        if not tenant_eligibility.may_use("production", tenant_eligibility.granted_tools(str(tenant_id))):
            continue
        with tenant_scope.enter_tenant(str(tenant_id)):
            stock_targets = storage.load_stock_targets()
            # Confirmed real bug, caught live on first restart-after-deploy
            # (2026-09-01): _structural_material_closure/classify_activity
            # read SDE data through storage.connect() same as any other
            # storage call - even though the *data* itself is tenant-
            # independent, connect() unconditionally requires an ambient
            # tenant to be set. Must stay called *inside* this `with` block,
            # not after it exits - calling it outside worked by accident
            # whenever some earlier request had already warmed the
            # relevant type_ids into the @lru_cache'd SDE lookups (no
            # connect() call on a cache hit), which masked this in every
            # manual test run but not on a cold cache right after a
            # service restart, when the scheduler's own global tick (no
            # ambient tenant at all outside this `with`) is often the very
            # first caller.
            type_ids |= _structural_material_closure(t[0] for t in stock_targets)
            # Integrated special orders are priced by plan_production too
            # (SPECIAL_ORDERS_INTEGRATION_PLAN.md D14).
            order_type_ids = {row[2] for row in storage.list_integrated_special_order_items()}
            if order_type_ids:
                type_ids |= _structural_material_closure(order_type_ids)
    return type_ids


def _doctrine_type_ids(last_active: dict) -> set[int]:
    """Hulls and fitting items of active doctrines for active tenants that
    hold `doctrine` and whose Doctrine hub is Jita. With
    accept_equivalent_variants, every equivalent of those items is included
    too (the Shopping List prices the cheapest twin). Tenants with another
    hub are skipped: this cache is Jita-only (#222). Errors propagate to
    the caller so Production's half still refreshes."""
    from .. import tenant_eligibility, tenant_scope
    from ..doctrine import equivalents
    from ..doctrine.config import DOCTRINE_CONFIG

    extra: set[int] = set()
    for tenant_id, _name, _created_at in storage.list_tenants():
        if not tenant_eligibility.is_active(str(tenant_id), last_active):
            continue
        if not tenant_eligibility.may_use("doctrine", tenant_eligibility.granted_tools(str(tenant_id))):
            continue
        with tenant_scope.enter_tenant(str(tenant_id)):
            if DOCTRINE_CONFIG.hub_region_id != JITA_REGION_ID:
                continue
            fittings = storage.list_active_fittings()
            if not fittings:
                continue
            fitting_ids = [str(row[0]) for row in fittings]
            for row in fittings:
                hull = row[4]  # hull_type_id
                if hull is not None:
                    extra.add(int(hull))
            items_by_fitting = storage.load_fitting_items_bulk(fitting_ids)
            item_ids: set[int] = set()
            for rows in items_by_fitting.values():
                for _line_no, _slot, type_id, _qty, _offline in rows:
                    if type_id is not None:
                        item_ids.add(int(type_id))
            extra |= item_ids
            if DOCTRINE_CONFIG.accept_equivalent_variants and item_ids:
                lookup = equivalents.lookup()
                for tid in list(item_ids):
                    extra.update(int(t) for t in lookup(tid))
    return extra


def refresh_jita_price_cache() -> int:
    """The actual refresh: prices every type_id reachable from *every
    tenant's own* stock targets and integrated special orders (their
    structural material closure - the
    same bounded, price-agnostic universe plan_production itself prices via
    _PlanContext, see engine._structural_material_closure), plus the hulls
    and fitting items of active doctrines whose hub is Jita (and their
    equivalents when accept_equivalent_variants is on), in one shared ESI
    fetch, then replaces the cache wholesale. Called from either the
    scheduler's hourly tick or the standalone manual admin action - both
    paths share this one function, so a manual run correctly "counts" and
    pushes back the next scheduled tick too, same mechanism the backup job's
    own mtime-based check already relies on.

    Local imports (engine, tenant_scope) avoid pulling this
    module into the engine.py <-> pricing.py import graph at load time -
    only this function, called from scheduler.py/admin.py, ever needs them.

    Returns the number of type_ids now cached (0 if no tenant has any stock
    targets configured yet - not an error, just nothing to price)."""
    global _last_doctrine_extra_count, _last_doctrine_extra_skipped
    from ..esi_client import ESIClient

    try:
        last_active = storage.list_tenant_last_active()
    except Exception:  # noqa: BLE001 - unknown activity must not shrink the universe
        log.warning("Could not read tenant activity for the Jita price cache", exc_info=True)
        last_active = {}

    type_ids = _production_type_ids(last_active)
    _last_doctrine_extra_count = 0
    _last_doctrine_extra_skipped = False
    try:
        doctrine_ids = _doctrine_type_ids(last_active)
        extra = doctrine_ids - type_ids
        _last_doctrine_extra_count = len(extra)
        if len(extra) > _MAX_DOCTRINE_EXTRA_TYPES:
            _last_doctrine_extra_skipped = True
            log.warning(
                "Jita price cache: skipping Doctrine types (%d extra > %d cap); "
                "Production types still refresh",
                len(extra), _MAX_DOCTRINE_EXTRA_TYPES,
            )
        else:
            type_ids |= doctrine_ids
            if extra:
                log.info("Jita price cache: adding %d Doctrine type_ids", len(extra))
    except Exception:  # noqa: BLE001 - Doctrine half must never block Production
        log.warning(
            "Jita price cache: Doctrine type collection failed; "
            "Production types still refresh",
            exc_info=True,
        )

    if not type_ids:
        return 0

    # Always Jita: this cache is process-wide and cross-tenant with no region
    # in its key, so it must not follow any tenant's/tool's hub setting (#222).
    stats = ESIClient().region_order_stats_bulk(JITA_REGION_ID, list(type_ids))
    # Skip type_ids whose bulk lookup returned no real percentile - region_
    # order_stats_bulk isolates per-type ESIError as OrderStats(None, 0, ...),
    # and ESIClient now converts transport timeouts to ESIError too. Writing
    # those as buy=sell=0 would poison the shared cache: jita_prices treats
    # a cached 0 as a hit and never falls through to Goonmetrics.
    prices = {
        tid: CurrentPrice(type_id=tid, updated="",
                          buy=s.buy_percentile or 0.0, sell=s.sell_percentile or 0.0)
        for tid, s in stats.items()
        if s.buy_percentile or s.sell_percentile
    }
    if not prices:
        log.warning("Jita price cache refresh produced no quotes - keeping previous snapshot")
        with _lock:
            return len(_cache)
    with _lock:
        global _updated_at
        _cache.clear()
        _cache.update(prices)
        _updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    log.info("Jita price cache refreshed: %d type_ids", len(prices))
    return len(prices)
