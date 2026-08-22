"""Configuration for the "Ore & Minerals" tool - GitHub issue #90. Loaded the
same way as TradingConfig/ProductionConfig/DoctrineConfig (config.py's
load_trading_config): built-in defaults, then config.yaml overrides, then
per-tenant Settings-page overrides on top (see resolve_and_set_refining_config
below).
"""
from __future__ import annotations

import contextvars
import copy
from dataclasses import dataclass, field

import yaml

from .. import storage
from ..config import (
    ConfigError, ConfigProxy, DEFAULT_CONFIG_PATH, apply_config_overrides, validate_config_overrides,
)
from .constants import (
    REPROCESSING_IMPLANT_BONUS, RIG_YIELD_BONUS_POINTS, STRUCTURE_YIELD_MODIFIER,
)


@dataclass
class RefiningConfig:
    # -- Ore/ice path: structure/rig/security/implant --
    # Settings-page dropdowns, not pulled from ESI (confirmed with the user
    # during planning: "stationsart, rig, skill und implant sollen als
    # settings per auswahl eingestellt werden können. skills nicht via esi
    # ziehen") - independent of TRADING_CONFIG.structure_id/production/
    # config.py's own structure_type fields, since the refining structure may
    # be a different one (a dedicated Refinery) than either tool's own.
    structure_type: str = "Citadel (no bonuses)"
    rig_tier: str = "No Rig"
    # Representative system security for the refining structure - -1.0 (deep
    # null-sec/wormhole) .. 1.0 (highsec), same raw scale as the SDE's own
    # solar_system security field. Defaults to 0.0 (mid null-sec) rather than
    # None: unlike Production's own system_id fields (resolved from ESI via a
    # named system), this is a plain manual number, and every "not set yet"
    # value should look like a real system, not a special-cased blank.
    security_status: float = 0.0
    implant: str = "None"

    # -- Ore/ice path: skills (manual, not ESI-synced - see structure_type above) --
    reprocessing_skill_level: int = 0
    reprocessing_efficiency_skill_level: int = 0
    # {ore/ice family name: skill level} - one skill per ore/ice *family*
    # (e.g. "Veldspar" covers Veldspar/Concentrated Veldspar/Dense Veldspar,
    # confirmed with the user during planning), not per exact type_id. A
    # family missing from this dict is treated as level 5 (maxed), not an
    # error and not unskilled - see refining/engine.py's ore_ice_yield's own
    # docstring for why (these are cheap skills almost every active player
    # has trained to 5; defaulting to 0 would understate every
    # not-yet-configured family's profit).
    ore_family_skill_levels: dict[str, int] = field(default_factory=dict)

    # -- Scrapmetal path (independent of everything above - see constants.py's
    # module docstring for why structure/rig/security/implant/the two skills
    # above don't apply here) --
    scrapmetal_processing_skill_level: int = 0

    # -- Economics --
    # Deducted from mineral value on both the ore/ice (#91) and Reprocessing
    # (#92) tabs' quotes - a separate field from Production's
    # facility_tax_rate/market_fees, since this tool's own refining structure
    # may not be the same one Production builds in.
    refining_tax_rate: float = 0.0


def validate_refining_overrides(overrides: dict) -> None:
    """Beyond the generic type checks (validate_config_overrides), structure_
    type/rig_tier/implant are only meaningful if they're one of the specific
    named options the yield engine actually knows how to price (constants.py's
    STRUCTURE_YIELD_MODIFIER/RIG_YIELD_BONUS_POINTS/REPROCESSING_IMPLANT_BONUS
    - the same dicts the Settings page's dropdowns will be built from) -
    mirrors production/config.py's validate_production_overrides for the same
    reasoning (a config.yaml hand-edit bypasses the dropdown, so a typo'd
    name would otherwise pass the plain string-type check and only fail
    later with a bare KeyError deep in the yield engine)."""
    if "structure_type" in overrides and overrides["structure_type"] not in STRUCTURE_YIELD_MODIFIER:
        raise ConfigError(f"structure_type: {overrides['structure_type']!r} is not a known structure type. "
                           f"Options: {', '.join(STRUCTURE_YIELD_MODIFIER)}")
    if "rig_tier" in overrides and overrides["rig_tier"] not in RIG_YIELD_BONUS_POINTS:
        raise ConfigError(f"rig_tier: {overrides['rig_tier']!r} is not a known rig tier. "
                           f"Options: {', '.join(RIG_YIELD_BONUS_POINTS)}")
    if "implant" in overrides and overrides["implant"] not in REPROCESSING_IMPLANT_BONUS:
        raise ConfigError(f"implant: {overrides['implant']!r} is not a known implant. "
                           f"Options: {', '.join(REPROCESSING_IMPLANT_BONUS)}")


_refining_config_yaml_cache: dict = {}


def load_refining_config(path=DEFAULT_CONFIG_PATH) -> RefiningConfig:
    """Cached per `path` after the first real disk read - same reasoning as
    eve_trader/config.py's load_trading_config (see its own docstring):
    config.yaml can't change without a process restart anyway. Returns a
    deep copy each call so a caller's in-place tenant-override mutation
    never leaks into what every other call/tenant sees."""
    if path not in _refining_config_yaml_cache:
        cfg = RefiningConfig()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            validate_config_overrides(cfg, overrides)
            validate_refining_overrides(overrides)
            apply_config_overrides(cfg, overrides)
        _refining_config_yaml_cache[path] = cfg
    return copy.deepcopy(_refining_config_yaml_cache[path])


# See eve_trader/config.py's ConfigProxy docstring for why this is a proxy,
# not a plain RefiningConfig instance.
_refining_config_var: contextvars.ContextVar[RefiningConfig] = contextvars.ContextVar(
    "refining_config", default=load_refining_config()
)
REFINING_CONFIG = ConfigProxy(_refining_config_var)


def resolve_and_set_refining_config(tenant_id: str) -> contextvars.Token:
    """Same idea as eve_trader/config.py's resolve_and_set_trading_config, for
    RefiningConfig/scope "refining" - see its docstring for the full
    reasoning (requires storage's tenant contextvar already set to
    tenant_id; use tenant_scope.enter_tenant, not this directly)."""
    cfg = load_refining_config()
    overrides = storage.load_tenant_settings("refining")
    if overrides:
        apply_config_overrides(cfg, overrides)
    return _refining_config_var.set(cfg)


def reset_refining_config(token: contextvars.Token) -> None:
    _refining_config_var.reset(token)
