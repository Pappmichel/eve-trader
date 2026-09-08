"""Configuration for the Doctrine tool - loaded the same way as
TradingConfig/ProductionConfig (config.py's load_trading_config): built-in
defaults, then config.yaml overrides, then per-tenant Settings-page
overrides on top (see resolve_and_set_doctrine_config below).
"""
from __future__ import annotations

import contextvars
import copy
from dataclasses import dataclass
from typing import Optional

import yaml

from .. import storage
from ..config import (
    ConfigError, ConfigProxy, DEFAULT_CONFIG_PATH, TRADING_CONFIG, apply_config_overrides,
    validate_config_overrides,
)
from ..production.constants import HANGAR_DIVISION_FLAGS


@dataclass
class DoctrineConfig:
    # No sane default across installs - falls back to Trading's own
    # structure_id (the C-J structure) at read time if unset, since almost
    # every operator running Doctrine also runs Trading against the same
    # structure (Phase 2 E.3) - see effective_structure_id below.
    doctrine_structure_id: Optional[int] = None
    # Where stockpile Ist is counted from (storage.esi_stock_at_location) -
    # falls back to effective_structure_id if unset, same reasoning.
    stockpile_location_id: Optional[int] = None
    # Consumables (drones/cargo/charges) only need to clear this fraction of
    # their Soll to count as "tolerable" rather than "critical" (Phase 3
    # spec B.4/C.3) - exact-class positions (hull/modules/rigs/subsystems)
    # never get this leniency. Per-fitting override: Fitting.
    # cargo_tolerance_pct (None = use this default).
    cargo_tolerance_pct: float = 0.9
    # Off by default: a contract item the fitting doesn't call for at all
    # normally counts as "info" (a free bonus, not a defect - Phase 3 B.4).
    # Turning this on treats it as "tolerable" instead, for an operator who
    # wants to enforce clean, exact contracts.
    strict_extras: bool = False
    # ISK/m3 to haul a Shopping List item bought at Jita back to C-J -
    # deliberately a separate setting from ProductionConfig.haul_cost_per_m3
    # (production/config.py), not a read of that same value: fitted modules/
    # ships bought for Doctrine plausibly have different freight logistics
    # than raw production materials, so the user asked for independent
    # control here. Seeded from Production's own 900.0 default at
    # first-setup time only (see engine._shopping_prices) - the two don't
    # stay in sync after that, editing one doesn't touch the other.
    import_cost_per_m3: float = 900.0
    # Which hangar/office division(s) at effective_stockpile_location_id count
    # as Doctrine's own stockpile Ist (storage.esi_stock_at_location's
    # allowed_flags - see production/constants.py HANGAR_DIVISION_FLAGS).
    # Same hangar-sorting reasoning as ProductionConfig.stock_hangar_flags
    # (see that field's own comment): Jita imports for every tool land in one
    # shared corp Wareneingang division first, and stockpile_rows_for_doctrine
    # used to count that whole division (and every other one) as Doctrine's
    # own available stockpile regardless of what it was actually bought for.
    # Empty tuple (the default) = no filter, today's whole-hangar-counts
    # behaviour, unchanged.
    stockpile_hangar_flags: tuple[str, ...] = ()

    @property
    def effective_structure_id(self) -> Optional[int]:
        return self.doctrine_structure_id or TRADING_CONFIG.structure_id

    @property
    def effective_stockpile_location_id(self) -> Optional[int]:
        return self.stockpile_location_id or self.effective_structure_id


def validate_doctrine_overrides(overrides: dict) -> None:
    """Beyond the generic type checks (validate_config_overrides), same enum-
    check pattern as production/config.py's validate_production_overrides -
    stockpile_hangar_flags entries must be real ESI hangar-division flags
    (production/constants.py HANGAR_DIVISION_FLAGS), not just any string, so
    a typo doesn't silently pass here and only fail later as "counts
    everything except a division that doesn't exist" (esi_stock_at_location's
    allowed_flags is a plain IN-list filter with no validation of its own)."""
    if "stockpile_hangar_flags" in overrides:
        bad = [f for f in overrides["stockpile_hangar_flags"] if f not in HANGAR_DIVISION_FLAGS]
        if bad:
            raise ConfigError(f"stockpile_hangar_flags: {bad!r} are not known hangar divisions. "
                               f"Options: {', '.join(HANGAR_DIVISION_FLAGS)}")


_doctrine_config_yaml_cache: dict = {}


def load_doctrine_config(path=DEFAULT_CONFIG_PATH) -> DoctrineConfig:
    """Cached per `path` after the first real disk read - same reasoning as
    eve_trader/config.py's load_trading_config (see its own docstring):
    config.yaml can't change without a process restart anyway. Returns a
    deep copy each call so a caller's in-place tenant-override mutation
    never leaks into what every other call/tenant sees."""
    if path not in _doctrine_config_yaml_cache:
        cfg = DoctrineConfig()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            validate_config_overrides(cfg, overrides)
            validate_doctrine_overrides(overrides)
            apply_config_overrides(cfg, overrides)
        _doctrine_config_yaml_cache[path] = cfg
    return copy.deepcopy(_doctrine_config_yaml_cache[path])


_doctrine_config_var: contextvars.ContextVar[DoctrineConfig] = contextvars.ContextVar(
    "doctrine_config", default=load_doctrine_config()
)
DOCTRINE_CONFIG = ConfigProxy(_doctrine_config_var)


def resolve_and_set_doctrine_config(tenant_id: str) -> contextvars.Token:
    """Same idea as eve_trader/config.py's resolve_and_set_trading_config,
    for DoctrineConfig/scope "doctrine" - see its docstring for the full
    reasoning (requires storage's tenant contextvar already set to
    tenant_id; use tenant_scope.enter_tenant, not this directly)."""
    cfg = load_doctrine_config()
    overrides = storage.load_tenant_settings("doctrine")
    if overrides:
        apply_config_overrides(cfg, overrides)
    return _doctrine_config_var.set(cfg)


def reset_doctrine_config(token: contextvars.Token) -> None:
    _doctrine_config_var.reset(token)
