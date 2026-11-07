"""Per-tenant cache of the priced rows behind Total Wealth.

`/wealth`, `/wealth/breakdown` and `/attention` each priced every owned item
again; the page opens all three at once. This holds one `_collect_wealth`
result per tenant for a short window so they share one computation (same
shape as `asset_manager.actions._rows`: a module dict plus one lock per key,
CLAUDE.md "Caching pattern").

It lives in its own module, importing only `storage`, so the ESI orchestrator,
the sharing actions and the settings saves can invalidate it without importing
`portfolio` (`esi_data/` imports no tool package). The wealth rules stay in
`portfolio.py`; this file only stores and drops a value.

Writers never read or fill it: `portfolio.take_portfolio_snapshot` computes
fresh inside `bypassed()`. A value is stored only when the caller's `cacheable()` check passes, and
only if nothing invalidated the tenant while it was being computed.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from typing import Any, Callable, Optional

from . import storage

TTL_SECONDS = 120

# tenant id -> (stored at, value). The value may be None (nobody shares
# anything), so a hit is told apart by the tuple, not by the value.
_cache: dict[str, tuple[float, Any]] = {}
_locks: dict[str, threading.Lock] = {}
# Bumped by every invalidation: a computation that started before one must
# not store its (older) result afterwards.
_generations: dict[str, int] = {}
_guard = threading.Lock()


_bypass: contextvars.ContextVar[bool] = contextvars.ContextVar("wealth_cache_bypass", default=False)


@contextlib.contextmanager
def bypassed():
    """Inside this block `get_or_compute` computes anew and neither reads nor
    fills the cache. For paths that write the result somewhere (the portfolio
    snapshot)."""
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


def _key() -> str:
    return str(storage.get_current_tenant())


def invalidate(tenant_id: Optional[str] = None) -> None:
    """Drops one tenant's entry (None = every tenant). Never raises."""
    with _guard:
        keys = list(_generations.keys() | _cache.keys()) if tenant_id is None else [str(tenant_id)]
        for key in keys:
            _generations[key] = _generations.get(key, 0) + 1
            _cache.pop(key, None)


def invalidate_current_tenant() -> None:
    invalidate(_key())


def clear() -> None:
    """Forget everything, including locks and generations (tests)."""
    with _guard:
        _cache.clear()
        _locks.clear()
        _generations.clear()


def get_or_compute(compute: Callable[[], Any], cacheable: Callable[[], bool]) -> Any:
    """The tenant's cached value, else `compute()`. Concurrent callers of one
    tenant wait on one computation. The result is stored only when
    `cacheable()` is true after computing and no invalidation happened since
    the computation began. The returned object is shared: callers copy it."""
    if _bypass.get():
        return compute()
    key = _key()
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        now = time.time()
        with _guard:
            hit = _cache.get(key)
            generation = _generations.get(key, 0)
        if hit is not None and now - hit[0] < TTL_SECONDS:
            return hit[1]
        value = compute()
        if cacheable():
            with _guard:
                if _generations.get(key, 0) == generation:
                    _cache[key] = (time.time(), value)
        return value
