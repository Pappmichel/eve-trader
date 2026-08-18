"""Configuration for the Production tool - loaded the same way as TradingConfig
(config.py's load_trading_config): built-in defaults, then config.yaml overrides.
"""
from __future__ import annotations

import contextvars
import copy
from dataclasses import dataclass
from typing import Optional

import yaml

from .. import storage
from ..config import ConfigError, ConfigProxy, DEFAULT_CONFIG_PATH, apply_config_overrides, validate_config_overrides
from .constants import RIG_TIERS, STRUCTURE_TYPES


@dataclass
class ProductionConfig:
    # -- Settings tab equivalents --
    component_overbuild: float = 0.7      # extra buffer kept for build-chain components
    bpc_inventory: float = 4.0            # BPC copies kept in stock (used once Invention lands)
    market_fees: float = 0.0537           # sell-side broker fee + sales tax + SCC surcharge, subtracted
                                            # from margins (see engine.py _build_margin) - confirmed
                                            # against the in-game sell-order breakdown: SCC surcharge
                                            # 0.5% + Broker's fee 1.5% + Sales tax 3.37% + Safety Tax 0.00%
    jita_buy_broker_fee: float = 0.0147   # buy-side broker fee, confirmed against the in-game buy
                                            # screen - applied in pricing.py buy_price/_candidate_prices
    min_margin: float = 0.15              # gates the Bauliste: a stock target only builds if margin
                                            # (sell price minus build cost, over build cost) clears this
                                            # - see engine.py _build_margin/plan_production
    min_daily_profit: float = 0.0         # gates discover_build_candidates: a candidate also needs this
                                            # much *potential_daily_profit* (margin alone isn't a useful
                                            # ranking - a huge margin on an item nobody buys is worthless,
                                            # same reasoning as Trading's min_avg_movement). Defaults to
                                            # 0.0 (a true no-op - every margin-qualifying candidate is kept,
                                            # even ones with zero observed market movement) - raise it once
                                            # you've seen real potential_daily_profit values for good
                                            # candidates in the Build Candidates tab and know what "enough"
                                            # looks like; this field exists so that's a config change, not code.
    haul_cost_per_m3: float = 900.0       # ISK/m3 to move goods to the home structure
    facility_tax_rate: float = 0.0025     # job-fee facility tax, additive on top of the system cost index
                                            # (see engine.py _job_cost_rate) - EVE's real formula is
                                            # `EIV * (cost_index * structure_bonus + facility_tax + SCC_surcharge)`,
                                            # confirmed against wiki.eveuniversity.org/Manufacturing. Fixed at
                                            # 0.25% for NPC stations (this default); a player-owned Upwell
                                            # structure's owner can set their own rate (capped at 10% total
                                            # with the SCC surcharge) - override this if building somewhere
                                            # with a non-default facility tax.

    # -- Home market/structure --
    # No sane default across installs - must be set in config.yaml.
    # pricing.home_prices() guards against this being unset (returns {}
    # instead of requesting ".../market/None/prices.json").
    home_market: Optional[str] = None     # appraise.gnf.lt market slug (case-sensitive)
    home_location_id: Optional[int] = None  # structure/station ID to filter ESI assets to (None = all locations)

    # -- System cost index, split by *what's being built* (see engine.py /
    # constants.py COMPONENT_GROUP_IDS): reactions + certain component groups
    # use one system, everything else uses another. IDs are resolved from the
    # *_name fields via ESI (production/actions.py do_set_system) - don't
    # hand-edit the *_id fields, they're derived. No sane default across
    # installs; None is already a fully-supported "not set yet" state (see
    # pricing.system_cost_indices_for - falls back to the flat ACTIVITY_MODS
    # rate rather than erroring).
    component_system_name: Optional[str] = None
    component_system_id: Optional[int] = None
    manufacturing_system_name: Optional[str] = None
    manufacturing_system_id: Optional[int] = None

    # -- Where you build, split by build profile just like the system cost index
    # above (see production/constants.py STRUCTURE_TYPES / RIG_TIERS /
    # engine.py _structure_profile): reactions typically run in a Refinery,
    # rig-covered component groups in an Engineering Complex with the
    # component ME rigs, everything else in a separate Engineering Complex -
    # each can have its own structure + rig.
    reaction_structure_type: str = "Citadel (no bonuses)"
    reaction_rig_tier: str = "No Rig"
    component_structure_type: str = "Citadel (no bonuses)"
    component_rig_tier: str = "No Rig"
    manufacturing_structure_type: str = "Citadel (no bonuses)"
    manufacturing_rig_tier: str = "No Rig"

    # -- Invention (see production/invention.py) --
    encryption_skill_level: int = 4
    datacore_skill_1_level: int = 4
    datacore_skill_2_level: int = 4

    # -- Fuzzwork SDE --
    fuzzwork_csv_base: str = "https://www.fuzzwork.co.uk/dump/latest/csv/"


_STRUCTURE_TYPE_FIELDS = ("reaction_structure_type", "component_structure_type", "manufacturing_structure_type")
_RIG_TIER_FIELDS = ("reaction_rig_tier", "component_rig_tier", "manufacturing_rig_tier")


def validate_production_overrides(overrides: dict) -> None:
    """Beyond the generic type checks (validate_config_overrides), the
    structure/rig fields are only meaningful if they're one of the specific
    named options structure_rig_multiplier actually knows how to price
    (constants.STRUCTURE_TYPES/RIG_TIERS - the same lists the Settings page's
    dropdowns are built from) - a config.yaml hand-edit bypasses that
    dropdown, so a typo'd structure name would otherwise pass the plain
    string-type check here and only fail later with a bare KeyError deep in
    engine.py's _structure_rig."""
    for key in _STRUCTURE_TYPE_FIELDS:
        if key in overrides and overrides[key] not in STRUCTURE_TYPES:
            raise ConfigError(f"{key}: '{overrides[key]}' is not a known structure type. "
                               f"Options: {', '.join(STRUCTURE_TYPES)}")
    for key in _RIG_TIER_FIELDS:
        if key in overrides and overrides[key] not in RIG_TIERS:
            raise ConfigError(f"{key}: '{overrides[key]}' is not a known rig tier. "
                               f"Options: {', '.join(RIG_TIERS)}")


_production_config_yaml_cache: dict = {}


def load_production_config(path=DEFAULT_CONFIG_PATH) -> ProductionConfig:
    """Cached per `path` after the first real disk read - same reasoning as
    eve_trader/config.py's load_trading_config (see its own docstring):
    resolve_and_set_production_config below calls this on every gate-enabled
    request/scheduler tick, but config.yaml can't change without a process
    restart anyway, so re-parsing it every time was pure waste. Returns a
    deep copy each call so a caller's in-place overrides (e.g.
    resolve_and_set_production_config's apply_config_overrides) never leak
    into what every other call/tenant sees."""
    if path not in _production_config_yaml_cache:
        cfg = ProductionConfig()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            # Fail fast, at startup, with the specific bad field named - not a
            # confusing crash later, deep inside engine.py, the first time that
            # field is actually used.
            validate_config_overrides(cfg, overrides)
            validate_production_overrides(overrides)
            apply_config_overrides(cfg, overrides)
        _production_config_yaml_cache[path] = cfg
    return copy.deepcopy(_production_config_yaml_cache[path])


# See eve_trader/config.py's ConfigProxy docstring for why this is a proxy,
# not a plain ProductionConfig instance. resolve_and_set_production_config
# (below) is what actually calls `.set()`, wired up via
# tenant_scope.enter_tenant (multi-tenant migration Phase 4).
_production_config_var: contextvars.ContextVar[ProductionConfig] = contextvars.ContextVar(
    "production_config", default=load_production_config()
)
PRODUCTION_CONFIG = ConfigProxy(_production_config_var)


def resolve_and_set_production_config(tenant_id: str) -> contextvars.Token:
    """Same idea as eve_trader/config.py's resolve_and_set_trading_config,
    for ProductionConfig/scope "production" - see its docstring for the
    full reasoning (requires storage's tenant contextvar already set to
    tenant_id; use tenant_scope.enter_tenant, not this directly)."""
    cfg = load_production_config()
    overrides = storage.load_tenant_settings("production")
    if overrides:
        apply_config_overrides(cfg, overrides)
    return _production_config_var.set(cfg)


def reset_production_config(token: contextvars.Token) -> None:
    _production_config_var.reset(token)
