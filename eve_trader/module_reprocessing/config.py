"""Configuration for the "Module Reprocessing Import" tool. Loaded the same
way as TradingConfig/ProductionConfig/RefiningConfig (config.py's
load_trading_config): built-in defaults, then config.yaml overrides, then
per-tenant Settings-page overrides on top (see
resolve_and_set_module_reprocessing_config below).

Deliberately has NO structure_type/rig_tier/security_status/implant fields,
unlike RefiningConfig - this tool only ever reprocesses via the scrapmetal
path (modules/drones, never ore/ice), and refining/constants.py's own
confirmed-against-the-wiki formula documents that structure/rig/security/
implant/the general Reprocessing and Reprocessing Efficiency skills have NO
effect on that path at all. Adding those fields here would be pure UI
decoration with zero effect on the computed yield - confirmed with the user
during planning not to do this. The only yield-relevant field is
scrapmetal_processing_skill_level.
"""
from __future__ import annotations

import contextvars
import copy
from dataclasses import dataclass
from typing import Optional

import yaml

from .. import storage
from ..config import (
    ConfigProxy, DEFAULT_CONFIG_PATH, apply_config_overrides, validate_config_overrides,
)

# Real EVE region id for The Forge (Jita) - same default TradingConfig.
# jita_region_id already uses; this tool's own purchase source defaults to
# the same real market rather than an invented one.
JITA_REGION_ID = 10000002


@dataclass
class ModuleReprocessingConfig:
    # -- Scrapmetal path (the only reprocessing path this tool ever uses -
    # see this module's own docstring) --
    scrapmetal_processing_skill_level: int = 0

    # -- Economics --
    # Own value, deliberately not shared with RefiningConfig.refining_tax_rate
    # even though both may in practice be the same physical C-J structure -
    # confirmed with the user during planning: every tool gets its own config
    # dataclass, see CLAUDE.md's Config section.
    refining_tax_rate: float = 0.0
    freight_cost_per_m3: float = 800.0
    min_profit_threshold: float = 0.0
    min_margin_threshold: float = 0.05
    # When on, Refresh Shortlist skips both threshold checks above entirely
    # (every priced candidate is a "hit", regardless of est_profit_per_unit/
    # est_margin) - for a tenant who wants to see the full priced universe
    # (e.g. while Goonmetrics data is thin/stale and nothing clears even a
    # 0 threshold) rather than tightening/loosening the two numeric fields.
    # Off by default - same "opt in" shape as enforce_shortlist_cap below.
    ignore_thresholds: bool = False

    # -- Purchase source (Default Jita - a region-wide buy, same shape as
    # Ore & Minerals' own Jita-only ore sourcing) --
    purchase_region_id: int = JITA_REGION_ID
    # Set to source from a specific player structure's own sell orders
    # instead of the whole region's public order book - None (the default)
    # means "region only", matching this tool's initial scope.
    purchase_structure_id: Optional[int] = None

    # -- Shortlist auto-maintenance (candidate_discovery.discover_candidates) --
    # Same "off by default, user opts in" shape as TradingConfig's/
    # StationTradingConfig's own enforce_shortlist_cap/max_active_shortlist_
    # items (confirmed with the user for Station Trading 2026-08-29: an
    # always-on hard cap was wrong there because min_daily_volume was
    # already the real noise filter - a cap on top of that should be an
    # explicit choice, not a silent default). Same reasoning applies here:
    # min_profit_threshold/min_margin_threshold above are this tool's own
    # real noise filter over the (much larger than Ore & Minerals') T1/Meta
    # module+drone universe - confirmed with the user 2026-09-27 that manual
    # per-item shortlist curation doesn't scale at this size, so Refresh
    # Shortlist now auto-discovers and auto-adds every candidate clearing
    # that bar (see actions.do_refresh_shortlist) instead of requiring a
    # separate manual "add" step.
    enforce_shortlist_cap: bool = False
    max_active_shortlist_items: int = 300


_module_reprocessing_config_yaml_cache: dict = {}


def load_module_reprocessing_config(path=DEFAULT_CONFIG_PATH) -> ModuleReprocessingConfig:
    """Cached per `path` after the first real disk read - same reasoning as
    eve_trader/config.py's load_trading_config (see its own docstring):
    config.yaml can't change without a process restart anyway. Returns a
    deep copy each call so a caller's in-place tenant-override mutation
    never leaks into what every other call/tenant sees."""
    if path not in _module_reprocessing_config_yaml_cache:
        cfg = ModuleReprocessingConfig()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            validate_config_overrides(cfg, overrides)
            apply_config_overrides(cfg, overrides)
        _module_reprocessing_config_yaml_cache[path] = cfg
    return copy.deepcopy(_module_reprocessing_config_yaml_cache[path])


# See eve_trader/config.py's ConfigProxy docstring for why this is a proxy,
# not a plain ModuleReprocessingConfig instance.
_module_reprocessing_config_var: contextvars.ContextVar[ModuleReprocessingConfig] = contextvars.ContextVar(
    "module_reprocessing_config", default=load_module_reprocessing_config()
)
MODULE_REPROCESSING_CONFIG = ConfigProxy(_module_reprocessing_config_var)


def resolve_and_set_module_reprocessing_config(tenant_id: str) -> contextvars.Token:
    """Same idea as eve_trader/config.py's resolve_and_set_trading_config, for
    ModuleReprocessingConfig/scope "module_reprocessing" - see its docstring
    for the full reasoning (requires storage's tenant contextvar already set
    to tenant_id; use tenant_scope.enter_tenant, not this directly)."""
    cfg = load_module_reprocessing_config()
    overrides = storage.load_tenant_settings("module_reprocessing")
    if overrides:
        apply_config_overrides(cfg, overrides)
    return _module_reprocessing_config_var.set(cfg)


def reset_module_reprocessing_config(token: contextvars.Token) -> None:
    _module_reprocessing_config_var.reset(token)
