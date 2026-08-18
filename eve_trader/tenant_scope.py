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
from .doctrine import config as doctrine_config
from .production import config as production_config


@contextmanager
def enter_tenant(tenant_id: str):
    """Sets storage's ambient tenant, then resolves and sets TRADING_CONFIG's,
    PRODUCTION_CONFIG's, and DOCTRINE_CONFIG's live instance for that same
    tenant (base defaults + config.yaml, overlaid with that tenant's own
    tenant_settings) - resets all four on exit, storage's tenant last, so
    the config-resolution steps still have a tenant to read
    tenant_settings under for as long as they need it.

    Each `set`/resolve step gets its own nested `try/finally` rather than
    one flat `try` wrapping all four - if a later resolve step raises (e.g.
    a transient Postgres error), the tokens already set by the steps before
    it must still be reset; a single flat `try` starting only after every
    `.set()` call would leave those contextvars permanently pointing at
    this tenant for the rest of the thread/task's lifetime - confirmed real
    risk for scheduler.py's per-tenant loop, which reuses one background
    thread across every tenant on each tick."""
    storage_token = storage.set_current_tenant(tenant_id)
    try:
        trading_token = config.resolve_and_set_trading_config(tenant_id)
        try:
            production_token = production_config.resolve_and_set_production_config(tenant_id)
            try:
                doctrine_token = doctrine_config.resolve_and_set_doctrine_config(tenant_id)
                try:
                    yield
                finally:
                    doctrine_config.reset_doctrine_config(doctrine_token)
            finally:
                production_config.reset_production_config(production_token)
        finally:
            config.reset_trading_config(trading_token)
    finally:
        storage.reset_current_tenant(storage_token)
