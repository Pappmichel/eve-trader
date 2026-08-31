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

_lock = threading.Lock()
_cache: dict[int, CurrentPrice] = {}
_updated_at: Optional[str] = None


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


def refresh_jita_price_cache() -> int:
    """The actual refresh: prices every type_id reachable from *every
    tenant's own* stock targets (their structural material closure - the
    same bounded, price-agnostic universe plan_production itself prices via
    _PlanContext, see engine._structural_material_closure), in one shared
    ESI fetch, then replaces the cache wholesale. Called from either the
    scheduler's hourly tick or the standalone manual admin action - both
    paths share this one function, so a manual run correctly "counts" and
    pushes back the next scheduled tick too, same mechanism the backup job's
    own mtime-based check already relies on.

    Local imports (engine, tenant_scope, TRADING_CONFIG) avoid pulling this
    module into the engine.py <-> pricing.py import graph at load time -
    only this function, called from scheduler.py/admin.py, ever needs them.

    Returns the number of type_ids now cached (0 if no tenant has any stock
    targets configured yet - not an error, just nothing to price)."""
    from .. import tenant_scope
    from ..config import TRADING_CONFIG
    from ..esi_client import ESIClient
    from .engine import _structural_material_closure

    type_ids: set[int] = set()
    for tenant_id, _name, _created_at in storage.list_tenants():
        with tenant_scope.enter_tenant(str(tenant_id)):
            stock_targets = storage.load_stock_targets()
        type_ids |= _structural_material_closure(t[0] for t in stock_targets)

    if not type_ids:
        return 0

    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        jita_region_id = TRADING_CONFIG.jita_region_id

    stats = ESIClient().region_order_stats_bulk(jita_region_id, list(type_ids))
    prices = {
        tid: CurrentPrice(type_id=tid, updated="", buy=s.buy_percentile or 0.0, sell=s.sell_percentile or 0.0)
        for tid, s in stats.items()
    }
    with _lock:
        global _updated_at
        _cache.clear()
        _cache.update(prices)
        _updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    log.info("Jita price cache refreshed: %d type_ids", len(prices))
    return len(prices)
