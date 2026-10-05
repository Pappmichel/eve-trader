"""Configuration for the PI tool - loaded the same way as RefiningConfig:
built-in defaults, then config.yaml overrides, then per-tenant Settings-page
overrides (tenant_settings scope "pi").

Every field except `hub_region_id` carries a `pi_` prefix on purpose:
config.yaml is one flat mapping applied to *every* config dataclass, and
config._FIELD_RANGES is keyed by field name across all of them. A plain
`broker_fee_rate` would silently take Station Trading's value and bounds
(docs/PI_TECHNICAL_DESIGN.md P-59). `hub_region_id` stays unprefixed like in
the other tools' hub pickers (#222).
"""
from __future__ import annotations

import contextvars
import copy
from dataclasses import dataclass

import yaml

from .. import storage
from ..config import ConfigError, ConfigProxy, DEFAULT_CONFIG_PATH, apply_config_overrides, validate_config_overrides
from . import constants as C


@dataclass
class PiConfig:
    # Market hub (region id) PI prices come from; 0 = all hubs, best per item
    # (hubs.ALL_HUBS). Default Jita.
    hub_region_id: int = 10000002
    # Price at a player structure's market instead (e.g. the C-J home
    # structure): its structure id, 0 = use hub_region_id. Read through a
    # character with the "Structure market book" capability (docking access
    # needed), falling back to the Goonmetrics market `pi_price_structure_slug`
    # (empty = the slug Trading/Production already use for that structure).
    pi_price_structure_id: int = 0
    pi_price_structure_slug: str = ""

    # -- Market fees (the defaults are placeholders, check your own skills) --
    pi_broker_fee_rate: float = 0.03          # buying inputs / listing outputs
    pi_sales_tax_rate: float = 0.045          # selling outputs
    # How outputs are valued: "sell_orders" (list them, minus broker fee and
    # sales tax) or "buy_orders" (sell instantly into buy orders, minus sales
    # tax). Inputs are always bought from sell orders.
    pi_valuation: str = "sell_orders"

    # -- Logistics (O1: PI's own rate, hub <-> planets; the shared hub ->
    # home-structure freight table is not used) --
    pi_freight_per_m3: float = 0.0

    # -- Customs (PI_PLAN 3.3): owner part, entered per plan; the NPC part
    # (high-sec 10% minus Customs Code Expertise) is added automatically --
    pi_owner_tax_rate: float = 0.0

    # -- Extraction (D2): P0 per head per hour averaged over a 3-day program,
    # one default per security zone. Calibration from real colonies replaces
    # them once enough samples exist. --
    pi_yield_highsec: float = 1000.0
    pi_yield_lowsec: float = 2000.0
    pi_yield_nullsec: float = 4000.0
    pi_yield_wormhole: float = 4000.0
    # Security zone the Profitability table assumes (a concrete planet in the
    # Planner/System analysis uses its own system's zone instead).
    pi_zone: str = "highsec"
    pi_program_hours: float = 72.0
    pi_collection_interval_hours: float = 24.0

    # -- Verdict (O2) --
    pi_amortisation_days: float = 30.0
    pi_min_isk_per_planet_day: float = 1_000_000.0
    pi_market_share_warning: float = 0.10

    # -- Manual fallbacks when ESI skills are not shared (D3) --
    pi_planets_per_character: int = 6
    pi_characters: int = 1
    pi_cc_level: int = 5
    pi_customs_code_expertise_level: int = 0

    # Radius (km) used for factory chains in the Profitability table, where
    # no concrete planet is chosen. Extraction chains use each planet type's
    # median radius from the SDE.
    pi_reference_radius_km: float = 5000.0

    # Production demand view: colonies needed to cover the buy list within
    # this many days.
    pi_demand_days: float = 7.0

    def yield_for_zone(self, zone: str) -> float:
        return {
            C.ZONE_HIGHSEC: self.pi_yield_highsec,
            C.ZONE_LOWSEC: self.pi_yield_lowsec,
            C.ZONE_NULLSEC: self.pi_yield_nullsec,
            C.ZONE_WORMHOLE: self.pi_yield_wormhole,
        }[zone]


VALUATIONS = ("sell_orders", "buy_orders")


def validate_pi_overrides(overrides: dict) -> None:
    if "pi_valuation" in overrides and overrides["pi_valuation"] not in VALUATIONS:
        raise ConfigError(f"pi_valuation: {overrides['pi_valuation']!r} is not one of {', '.join(VALUATIONS)}")
    if "pi_zone" in overrides and overrides["pi_zone"] not in C.ZONES:
        raise ConfigError(f"pi_zone: {overrides['pi_zone']!r} is not one of {', '.join(C.ZONES)}")
    if "pi_cc_level" in overrides and overrides["pi_cc_level"] not in C.CC_LEVELS:
        raise ConfigError(f"pi_cc_level: {overrides['pi_cc_level']!r} must be 0-5")
    if "hub_region_id" in overrides and int(overrides["hub_region_id"]) < 0:
        raise ConfigError("hub_region_id: must be a region id or 0 (all hubs)")


_pi_config_yaml_cache: dict = {}


def load_pi_config(path=DEFAULT_CONFIG_PATH) -> PiConfig:
    """Cached per `path` after the first disk read (see refining/config.py's
    load_refining_config); returns a deep copy each call."""
    if path not in _pi_config_yaml_cache:
        cfg = PiConfig()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            validate_config_overrides(cfg, overrides)
            validate_pi_overrides({k: v for k, v in overrides.items() if hasattr(cfg, k)})
            apply_config_overrides(cfg, overrides)
        _pi_config_yaml_cache[path] = cfg
    return copy.deepcopy(_pi_config_yaml_cache[path])


_pi_config_var: contextvars.ContextVar[PiConfig] = contextvars.ContextVar(
    "pi_config", default=load_pi_config()
)
PI_CONFIG = ConfigProxy(_pi_config_var)


def resolve_and_set_pi_config(tenant_id: str) -> contextvars.Token:
    """Same idea as config.resolve_and_set_trading_config, for scope "pi"
    (needs storage's tenant contextvar already set - use
    tenant_scope.enter_tenant, not this directly)."""
    cfg = load_pi_config()
    overrides = storage.load_tenant_settings("pi")
    if overrides:
        apply_config_overrides(cfg, overrides)
    return _pi_config_var.set(cfg)


def reset_pi_config(token: contextvars.Token) -> None:
    _pi_config_var.reset(token)
