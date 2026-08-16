"""Configuration for EVE Trader.

Values are loaded from (in order of increasing priority):
1. Built-in defaults
2. config.yaml in the project root (see config.example.yaml)
3. Environment variables / .env file (secrets: client id/secret, tokens path)
"""
from __future__ import annotations

import os
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from ruamel.yaml import YAML

load_dotenv()


class ConfigError(Exception):
    """Raised when a config.yaml value (or a Settings-page update) doesn't
    match its field's declared type - named at the specific field/value, so
    a typo (a quoted number, a misspelled true/false, a single value where a
    list was expected) fails immediately and clearly, either at startup
    (load_trading_config/load_production_config) or as a normal Settings-
    save error (actions.py do_update_settings converts this to ActionError),
    instead of surfacing later as a confusing failure deep inside engine.py
    the first time that field actually gets used."""


def _int_env(name: str, default: int) -> int:
    """Confirmed real gap: OAuthConfig.callback_port used to do a bare
    `int(os.getenv(...))` in its default_factory - a malformed env var (a
    typo, stray quotes/whitespace) raised an uncaught ValueError the moment
    OAuthConfig() is instantiated (module load time, OAUTH_CONFIG =
    OAuthConfig() below), crashing the whole app on startup with a raw
    traceback instead of ever reaching this file's own "fail fast with a
    clear message" ConfigError pattern."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a valid integer.") from None


def _check_type(key: str, value: Any, expected: type) -> None:
    """Permissive exactly where YAML's type model is coarser than Python's
    (a YAML sequence is always a list, never a tuple; YAML doesn't
    distinguish "int that happens to fit in a float field" from "float"),
    strict everywhere else. `bool` is deliberately never accepted for an
    int/float field even though Python's `bool` is technically an `int`
    subclass - a stray `true`/`false` in a numeric field is always a typo,
    never intentional, given none of this app's numeric fields are actually
    meant to hold a boolean."""
    origin = typing.get_origin(expected)
    if origin is typing.Union:  # covers Optional[X] == Union[X, None]
        if value is None:
            return
        real_types = [a for a in typing.get_args(expected) if a is not type(None)]
        if real_types:
            _check_type(key, value, real_types[0])
        return
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{key}: expected a number, got {value!r} ({type(value).__name__})")
    elif expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{key}: expected a whole number, got {value!r} ({type(value).__name__})")
    elif expected is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{key}: expected true/false, got {value!r} ({type(value).__name__})")
    elif expected is str:
        if not isinstance(value, str):
            raise ConfigError(f"{key}: expected text, got {value!r} ({type(value).__name__})")
    elif expected is tuple:
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{key}: expected a list, got {value!r} ({type(value).__name__})")
    # Any other/unresolvable annotation (e.g. Path) is skipped rather than
    # guessed at - not a field config.yaml ever actually sets.


# (min, max) inclusive bounds, either side optional - deliberately NOT
# exhaustive, only fields where an out-of-range value is unambiguously a
# typo/misconfiguration, never a legitimate choice: ESI region/structure/
# character/system IDs (always positive), day/count fields (never negative),
# and genuine fee/tax/rate *fractions* (broker fees, hit rate, facility tax -
# bounded 0-1 since these are literally "share of 1"). Shared across
# TradingConfig and ProductionConfig - both define jita_buy_broker_fee
# separately (see production/config.py's own copy), so this lives here
# rather than being duplicated per-module.
#
# Margin fields (min_margin, min_margin_threshold, min_profit_threshold) are
# deliberately bounded only at 0, NOT at 1 - margin can legitimately exceed
# 100% in this app's own domain (see production/engine.py's
# MAX_PLAUSIBLE_BUILD_MARGIN, capped at 300%, not 100%) - an upper bound of 1
# here would reject a real, intentional "require at least 150% margin"
# config value as if it were a typo.
#
# encryption_skill_level/datacore_skill_*_level are capped at 5 - EVE's own
# hard skill-level ceiling (skills only ever run 0-5), not an app-specific
# choice.
_FIELD_RANGES: dict[str, tuple[Optional[float], Optional[float]]] = {
    "jita_region_id": (1, None),
    "reference_region_id": (1, None),
    "structure_id": (1, None),
    "buyer_character_id": (1, None),
    "seller_character_id": (1, None),
    "import_cost_per_m3": (0, None),
    "structure_sell_haircut": (0, 1),
    "jita_buy_broker_fee": (0, 1),
    "min_profit_threshold": (0, None),
    "min_margin_threshold": (0, None),
    "skip_grace_period_days": (0, None),
    "max_active_shortlist_items": (1, None),
    "min_history_days": (0, None),
    "min_hit_rate": (0, 1),
    "safe_mode_max_ids": (1, None),
    "chunk_size": (1, None),
    "min_avg_movement": (0, None),
    "lookback_days": (0, None),
    "component_overbuild": (0, None),
    "bpc_inventory": (0, None),
    "market_fees": (0, 1),
    "min_margin": (0, None),
    "min_daily_profit": (0, None),
    "haul_cost_per_m3": (0, None),
    "facility_tax_rate": (0, 1),
    "encryption_skill_level": (0, 5),
    "datacore_skill_1_level": (0, 5),
    "datacore_skill_2_level": (0, 5),
    "home_location_id": (1, None),
    "component_system_id": (1, None),
    "manufacturing_system_id": (1, None),
    "trading_pipeline_interval_hours": (0, None),
    "production_sync_interval_hours": (0, None),
    "backup_interval_hours": (0, None),
}


def _check_range(key: str, value: Any) -> None:
    """Only fires for fields listed in _FIELD_RANGES - every other field is a
    no-op here, same "don't guess" stance as _check_type's fallthrough.
    `value is None` is always allowed through (an Optional field's None was
    already accepted by _check_type - a range check doesn't re-litigate
    that)."""
    bounds = _FIELD_RANGES.get(key)
    if bounds is None or value is None:
        return
    lo, hi = bounds
    if lo is not None and value < lo:
        raise ConfigError(f"{key}: {value!r} is below the minimum allowed value ({lo})")
    if hi is not None and value > hi:
        raise ConfigError(f"{key}: {value!r} is above the maximum allowed value ({hi})")


def validate_config_overrides(cfg: Any, overrides: dict[str, Any]) -> None:
    """Type- and range-checks every `overrides` value against its field's
    declared type on `cfg` (a TradingConfig or ProductionConfig instance) -
    raises ConfigError on the *first* mismatch, before anything is applied,
    so a bad value never lands half-applied (some fields written, others
    not). Unknown keys (not a real field on `cfg`) are silently skipped
    here, same as apply_config_overrides - this is about catching a wrong
    *type*/*range* for a real field, not about rejecting unrecognized keys."""
    hints = typing.get_type_hints(type(cfg))
    for key, value in overrides.items():
        if not hasattr(cfg, key):
            continue
        expected = hints.get(key)
        if expected is not None:
            _check_type(key, value, expected)
        _check_range(key, value)


def apply_config_overrides(cfg: Any, overrides: dict[str, Any]) -> None:
    """Applies already-validated `overrides` onto `cfg` - call
    validate_config_overrides first; this half never raises on a bad value,
    it assumes that's already been checked."""
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass
class TradingConfig:
    # -- Regions / structure --
    jita_region_id: int = 10000002          # "The Forge" - main trade hub used for imports
    reference_region_id: int = 10000009      # "Insmother" - used as price-history reference region
    # Destination player structure ("C-J" in this project's own docs) - no
    # sane default across installs, must be set in config.yaml. own_orders.py/
    # trade_reconciliation.py compare location_id == structure_id, which is
    # safely a no-op (never matches) while this is unset.
    structure_id: Optional[int] = None

    # -- Economics --
    import_cost_per_m3: float = 900.0        # ISK freight cost per m3 to move goods to the structure
    # Multiplier applied to the C-J structure sell price, confirmed against the
    # in-game sell-order breakdown: SCC surcharge 0.5% + Broker's fee 1.5% +
    # Sales tax 3.37% + Safety Tax 0.00% = 5.37% total -> 1 - 0.0537 = 0.9463.
    structure_sell_haircut: float = 0.9463
    # Broker's fee when buying (placing/filling a buy order) in Jita, confirmed
    # against the in-game buy screen (1.47%) - applied to landed_cost, since
    # buying was previously modeled as fee-free.
    jita_buy_broker_fee: float = 0.0147
    min_profit_threshold: float = 0.0        # Minimum absolute profit/unit to consider
    min_margin_threshold: float = 0.05       # Minimum margin to mark an item "Import"
    # Grace period before actions.do_refresh_and_prune_candidates deactivates a
    # shortlist item that's been continuously "No market data / Skip" - a
    # streak, tracked in storage.shortlist_skip_streak, resets the moment the
    # item is profitable again even once. Guards against a single bad/missing
    # market-data day knocking out an otherwise-fine item.
    skip_grace_period_days: int = 30
    # Optional hard cap on active shortlist size, enforced by
    # actions.do_refresh_and_prune_candidates in addition to (not instead of)
    # the Skip-grace-period prune above - off by default, toggled via a
    # checkbox on the Shortlist page. When on, active items beyond the
    # max_active_shortlist_items'th rank by max daily profit
    # (profit_per_unit x sell_volume, descending - confirmed with the user:
    # margin x liquidity, not margin alone) are deactivated, same as a
    # Skip-grace-period prune (not deleted, shortlist_snapshot history kept).
    enforce_shortlist_cap: bool = False
    max_active_shortlist_items: int = 300

    # -- Candidate discovery --
    min_history_days: int = 1
    min_hit_rate: float = 0.30                # Minimum share of profitable days to recommend a candidate
    safe_mode_max_ids: int = 500               # Cap on IDs evaluated per run (mirrors FindNewImportCandidatesSafe)
    chunk_size: int = 25                       # IDs per Goonmetrics request chunk (safe mode default)
    # Minimum average daily reference-region "movement" (Goonmetrics' daily
    # unit-quantity-traded liquidity figure - literally ESI's own /markets/
    # {region_id}/history/ `volume` field re-served verbatim, confirmed live
    # 2026-07-15 field-by-field against ESI directly; corrects an earlier,
    # wrong "ISK-value-traded" label here - see
    # history_backtest._score_candidate, which only ever feeds this through
    # log(1+x) as a soft weight, so the mislabeling never distorted its
    # actual scoring, just this comment) an item must clear to be
    # recommended, even if margin/hit-rate are fine -
    # confirmed with the user: profitability alone isn't enough, an item also
    # needs *some* real trading activity behind it. Defaults to 0.0 (a no-op
    # beyond the pre-existing implicit floor: score = avg_profit_m3 x
    # log(1+avg_move) x hit_rate is already 0, and so already excluded, for
    # exactly-zero movement) - raise it once you've seen real
    # avg_sell_movement values for good candidates in the New Candidates tab
    # and know what "enough" looks like; this field exists so that's a
    # config change, not a code change.
    min_avg_movement: float = 0.0

    # -- Characters (buyer imports in Jita, seller sells in the structure) --
    # Purely informational/display (Settings page) - the real character
    # identity for each role comes from whichever character you authorize via
    # "auth --role buyer"/"auth --role seller" (see TokenManager), not from
    # these fields. No sane default across installs.
    buyer_character_id: Optional[int] = None
    buyer_character_name: Optional[str] = None
    seller_character_name: Optional[str] = None
    seller_character_id: Optional[int] = None

    # -- Realized trade history --
    lookback_days: int = 30

    # -- API endpoints --
    esi_base: str = "https://esi.evetech.net/latest"
    goonmetrics_history_base: str = "https://goonmetrics.apps.gnf.lt/api/price_history/"

    # Market-group top-level path prefixes to hard-exclude from candidate
    # discovery entirely (mirrors IsWantedMarketPath) - confirmed one-by-one
    # with the user which categories structurally don't fit the Jita->C-J
    # import-arbitrage model (huge volume/thin markets/not a consumable) vs.
    # which should be let back in and judged purely on profitability + real
    # trading volume like everything else. "skills" was removed from this
    # list (used to be excluded, confirmed the user wants it included) -
    # nothing else is categorically pre-filtered anymore: no keyword
    # allowlist/denylist, no per-item volume(m3) cap (both removed, confirmed
    # with the user - "wenn sie sich lohnen ... sollen sie in betracht
    # kommen", not sorted out by category or size beforehand). See
    # min_avg_movement above for the liquidity gate that replaces the old
    # per-item-size cap's role of weeding out impractical candidates.
    excluded_path_prefixes: tuple = (
        "ships", "blueprints", "apparel",
        "personalization", "pilot's services", "structures",
    )

    # -- Background scheduler (see scheduler.py) --
    # Off by default: an in-process background thread that, once enabled,
    # periodically runs actions.do_pipeline (Trading) and
    # production.actions.do_sync_esi (Production) on their own intervals
    # while the backend process stays running - so routine maintenance
    # happens without either tool's "Refresh"/"Sync ESI" button being
    # clicked by hand. Previously nothing in this app ran on any kind of
    # schedule at all. Opt-in rather than on-by-default since this is a
    # credentials-handling tool making its own ESI calls in the background -
    # that should never start happening without the user explicitly asking.
    scheduler_enabled: bool = False
    trading_pipeline_interval_hours: float = 24.0     # do_pipeline(safe=True) - no universe rebuild
    production_sync_interval_hours: float = 6.0        # production do_sync_esi
    backup_interval_hours: float = 24.0                # backup.create_backup() - see backup.py


@dataclass
class OAuthConfig:
    """EVE SSO (OAuth2 + PKCE) configuration. Secrets come from environment."""
    client_id: str = field(default_factory=lambda: os.getenv("EVE_SSO_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("EVE_SSO_CLIENT_SECRET", ""))
    callback_host: str = field(default_factory=lambda: os.getenv("EVE_SSO_CALLBACK_HOST", "localhost"))
    callback_port: int = field(default_factory=lambda: _int_env("EVE_SSO_CALLBACK_PORT", 8000))
    authorize_url: str = "https://login.eveonline.com/v2/oauth/authorize"
    token_url: str = "https://login.eveonline.com/v2/oauth/token"
    jwks_url: str = "https://login.eveonline.com/oauth/jwks"
    issuer: str = "login.eveonline.com"
    token_store_path: Path = field(default_factory=lambda: DATA_DIR / "tokens.json")

    @property
    def redirect_uri(self) -> str:
        """Must match the FastAPI backend's mounted callback route
        (api/routers/auth.py's router is included at prefix "/api/auth") and
        the callback URL registered for this app at
        https://developers.eveonline.com/applications. Point
        EVE_SSO_CALLBACK_PORT at the backend's uvicorn port (default 8000)."""
        return f"http://{self.callback_host}:{self.callback_port}/api/auth/callback"

    # Scopes required across all Trading features - kept to exactly what the
    # code calls. Requesting scopes your EVE dev-portal app doesn't have
    # enabled (or that are misspelled) causes EVE SSO to reject the whole
    # login with "invalid_scope", so don't add more here unless you also
    # enable them for your app at https://developers.eveonline.com/applications
    # AND actually use the corresponding ESI endpoint in esi_client.py.
    scopes: tuple = (
        "esi-markets.read_character_orders.v1",   # ESIClient.character_orders
        "esi-markets.structure_markets.v1",        # ESIClient.structure_order_stats
        "esi-wallet.read_character_wallet.v1",     # ESIClient.character_wallet_transactions
        "esi-assets.read_assets.v1",                # ESIClient.character_assets (own_orders.fetch_buyer_already_covered,
                                                     # own_orders.fetch_seller_stock_without_order)
    )


def load_trading_config(path: Path = DEFAULT_CONFIG_PATH) -> TradingConfig:
    cfg = TradingConfig()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        validate_config_overrides(cfg, overrides)  # fail fast, at startup, with the specific bad field named
        apply_config_overrides(cfg, overrides)
    return cfg


TRADING_CONFIG = load_trading_config()
OAUTH_CONFIG = OAuthConfig()


def save_config_overrides(updates: dict[str, Any], *live_configs, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Persists `updates` into config.yaml and immediately applies them to
    every object in `live_configs` that has a matching attribute (e.g.
    TRADING_CONFIG, production.config.PRODUCTION_CONFIG - both dataclasses
    loaded from this same file) so a Settings-page save takes effect without
    restarting the app.

    Uses ruamel.yaml's round-trip mode instead of plain PyYAML: config.yaml is
    meant to be hand-edited too (see config.example.yaml's inline comments) -
    a plain load+dump would silently strip every comment on first save.

    Validates `updates` against every live config's field types *before*
    writing anything (see validate_config_overrides) - raises ConfigError on
    a bad value (e.g. a string where a number is expected) instead of
    writing a half-invalid config.yaml or letting it crash later, deep
    inside engine.py, the first time that field is actually used. Callers
    (actions.py do_update_settings) convert ConfigError to ActionError so it
    reaches the Settings page as a normal, clear error.
    """
    for cfg in live_configs:
        validate_config_overrides(cfg, updates)

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    data = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml_rt.load(f) or {}
    for key, value in updates.items():
        data[key] = value
    with open(path, "w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)

    for cfg in live_configs:
        apply_config_overrides(cfg, updates)
