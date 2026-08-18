"""Combines storage's tenant contextvar with both config modules' per-tenant
resolution into one enter/exit pair - see storage.set_current_tenant,
config.resolve_and_set_trading_config, production.config.
resolve_and_set_production_config. Used by AccessGateMiddleware
(per-request) and scheduler.py (per-tenant, per job-loop tick) - the two
places that need to fully "become" a tenant for a stretch of code.

A separate top-level module, not storage.py/config.py themselves - it needs
all three, and none of them may import each other this way: storage.py is
imported *by* config.py (for DATA_DIR), and production/config.py already
depends on the shared config.py, never the reverse (see CLAUDE.md's
layering note on production/config.py).
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
    tenant_settings under for as long as they need it."""
    storage_token = storage.set_current_tenant(tenant_id)
    trading_token = config.resolve_and_set_trading_config(tenant_id)
    production_token = production_config.resolve_and_set_production_config(tenant_id)
    try:
        yield
    finally:
        production_config.reset_production_config(production_token)
        config.reset_trading_config(trading_token)
        storage.reset_current_tenant(storage_token)
