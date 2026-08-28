"""Configuration for the "Station Trading" tool - buying and selling on
Jita's own order book, profiting from the bid-ask spread. Loaded the same
way as TradingConfig/ProductionConfig/DoctrineConfig/RefiningConfig
(config.py's load_trading_config): built-in defaults, then config.yaml
overrides, then per-tenant Settings-page overrides on top (see
resolve_and_set_station_trading_config below).
"""
from __future__ import annotations

import contextvars
import copy
from dataclasses import dataclass

import yaml

from .. import storage
from ..config import (
    ConfigProxy, DEFAULT_CONFIG_PATH, apply_config_overrides, validate_config_overrides,
)


@dataclass
class StationTradingConfig:
    # Jita 4 - Moon 4 - Caldari Navy Assembly Plant - the real trade hub
    # station (confirmed against the local SDE's sde_stations table, not
    # assumed from memory). Region is deliberately not a separate field
    # here - reused directly from TRADING_CONFIG.jita_region_id wherever
    # needed (see production/pricing.py's jita_prices for the same
    # precedent), since it's a Trading-level concept this tool doesn't own.
    station_id: int = 60003760

    # Starting defaults for the base-game NPC-station rates (no standings/
    # skill reduction applied) - not asserted precise, meant to be checked
    # against your own in-game Market window and adjusted (same "manual
    # value, live hint alongside it" pattern as Production's cost-index
    # overrides - see the Settings page's Skills panel). Itemized
    # separately (unlike Trading's blended structure_sell_haircut) because
    # Station Trading needs broker fee charged per order leg (both buy and
    # sell) and sales tax charged once (sell leg only).
    broker_fee_rate: float = 0.05
    sales_tax_rate: float = 0.075

    # Candidate-discovery gates (candidate_discovery.discover_candidates).
    # min_daily_volume's real-world shape was checked live against Jita's
    # actual market (2026-08-28): items clearing an 8% spread are sharply
    # bimodal - thousands of genuinely dead items (avg_daily_volume in the
    # single-to-low-hundreds range, a wide "spread" on these is meaningless
    # noise from near-zero order-book depth) vs. a small cluster of real,
    # liquid commodities (minerals etc., volume in the tens of millions+) -
    # 1000 sits well inside the gap between those two clusters, not a
    # precisely-derived number, and already brings the real live candidate
    # count down from 10,000+ to ~1,300 (checked live 2026-08-29) - a real,
    # legitimately large opportunity set, not something to additionally cap
    # by default.
    min_spread_threshold: float = 0.08
    min_daily_volume: float = 1000.0

    # Same "off by default, user opts in" shape as TradingConfig's own
    # enforce_shortlist_cap/max_active_shortlist_items - confirmed with the
    # user 2026-08-29 that an always-on hard cap (this tool's initial 200)
    # was wrong: min_daily_volume above is the real noise filter, a cap on
    # top of that should be an explicit choice, not a silent default.
    enforce_shortlist_cap: bool = False
    max_active_shortlist_items: int = 300


_yaml_cache: dict = {}


def load_station_trading_config(path=DEFAULT_CONFIG_PATH) -> StationTradingConfig:
    """Cached per `path` after the first real disk read - same reasoning as
    eve_trader/config.py's load_trading_config (see its own docstring):
    config.yaml can't change without a process restart anyway. Returns a
    deep copy each call so a caller's in-place tenant-override mutation
    never leaks into what every other call/tenant sees."""
    if path not in _yaml_cache:
        cfg = StationTradingConfig()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            validate_config_overrides(cfg, overrides)
            apply_config_overrides(cfg, overrides)
        _yaml_cache[path] = cfg
    return copy.deepcopy(_yaml_cache[path])


# See eve_trader/config.py's ConfigProxy docstring for why this is a proxy,
# not a plain StationTradingConfig instance.
_station_trading_config_var: contextvars.ContextVar[StationTradingConfig] = contextvars.ContextVar(
    "station_trading_config", default=load_station_trading_config()
)
STATION_TRADING_CONFIG = ConfigProxy(_station_trading_config_var)


def resolve_and_set_station_trading_config(tenant_id: str) -> contextvars.Token:
    """Same idea as eve_trader/config.py's resolve_and_set_trading_config, for
    StationTradingConfig/scope "station_trading" - see its docstring for the
    full reasoning (requires storage's tenant contextvar already set to
    tenant_id; use tenant_scope.enter_tenant, not this directly)."""
    cfg = load_station_trading_config()
    overrides = storage.load_tenant_settings("station_trading")
    if overrides:
        apply_config_overrides(cfg, overrides)
    return _station_trading_config_var.set(cfg)


def reset_station_trading_config(token: contextvars.Token) -> None:
    _station_trading_config_var.reset(token)
