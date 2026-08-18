"""Combines storage's tenant contextvar with both config modules' per-tenant
resolution into one enter/exit pair - see storage.set_current_tenant,
config.resolve_and_set_trading_config, production.config.
resolve_and_set_production_config. Used by AccessGateMiddleware
(per-request) and scheduler.py (per-tenant, per job-loop tick) - the two
places that need to fully "become" a tenant for a stretch of code.

A separate top-level module, not storage.py/config.py themselves - it needs
all three, and production/config.py depends on the shared config.py, never
the reverse (see CLAUDE.md's layering note on production/config.py).
"""
from __future__ import annotations

from contextlib import contextmanager

from . import config, storage
from .production import config as production_config


@contextmanager
def enter_tenant(tenant_id: str):
    """Sets storage's ambient tenant, then resolves and sets both
    TRADING_CONFIG's and PRODUCTION_CONFIG's live instance for that same
    tenant (base defaults + config.yaml, overlaid with that tenant's own
    tenant_settings) - resets all three on exit, storage's tenant last, so
    the config-resolution steps still have a tenant to read
    tenant_settings under for as long as they need it.

    Each `set`/resolve step gets its own nested `try/finally` rather than
    one flat `try` wrapping all three - if `resolve_and_set_production_config`
    raises (e.g. a transient Postgres error), `storage_token`/`trading_token`
    were already set by the two steps before it and must still be reset;
    a single flat `try` starting only after all three `.set()` calls would
    leave those two contextvars permanently pointing at this tenant for the
    rest of the thread/task's lifetime - confirmed real risk for
    scheduler.py's per-tenant loop, which reuses one background thread
    across every tenant on each tick."""
    storage_token = storage.set_current_tenant(tenant_id)
    try:
        trading_token = config.resolve_and_set_trading_config(tenant_id)
        try:
            production_token = production_config.resolve_and_set_production_config(tenant_id)
            try:
                yield
            finally:
                production_config.reset_production_config(production_token)
        finally:
            config.reset_trading_config(trading_token)
    finally:
        storage.reset_current_tenant(storage_token)
