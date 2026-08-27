"""Configuration for EVE Trader.

Values are loaded from (in order of increasing priority):
1. Built-in defaults
2. config.yaml in the project root (see config.example.yaml)
3. Environment variables / .env file (secrets: client id/secret, tokens path)
"""
from __future__ import annotations

import contextvars
import copy
import os
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

from . import storage

load_dotenv()


class ConfigProxy:
    """Forwards attribute reads *and* writes to whatever config instance a
    contextvars.ContextVar currently resolves to - lets TRADING_CONFIG/
    PRODUCTION_CONFIG stay the same importable name/object everywhere
    (every `cfg: TradingConfig = TRADING_CONFIG`-style default parameter
    across the app needs zero changes) while actually resolving to a
    different instance per tenant.

    `resolve_and_set_trading_config`/`resolve_and_set_production_config`
    (below) are what actually call the ContextVar's `.set()`, via
    `tenant_scope.enter_tenant` - used by `AccessGateMiddleware` (gate-
    enabled requests) and `scheduler.py` (per-tenant job ticks), plus a
    one-time boot-time load for `DEFAULT_TENANT_ID` (`api/app.py`'s
    `_load_default_tenant_config`, `cli.py`'s `main()`). Anywhere none of
    those has run yet (a bare test, an import-time read), every read/write
    still falls through to the ContextVar's `default=` (the single shared
    instance loaded at import time).

    Writes matter here, not just reads: apply_config_overrides does
    `setattr(cfg, key, value)` directly on whatever's passed to it - a
    read-only proxy would silently mutate a orphaned instance rather than
    the one every other read actually sees."""

    def __init__(self, var: contextvars.ContextVar):
        object.__setattr__(self, "_var", var)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._var.get(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._var.get(), name, value)

    def __repr__(self) -> str:
        return repr(self._var.get())


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
    elif expected is list:
        if not isinstance(value, list):
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
    "distribution_source_location_id": (1, None),
    "invention_location_id": (1, None),
    "component_system_id": (1, None),
    "manufacturing_system_id": (1, None),
    "reaction_cost_index_override": (0, 1),
    "component_cost_index_override": (0, 1),
    "manufacturing_cost_index_override": (0, 1),
    "trading_pipeline_interval_hours": (0, None),
    "production_sync_interval_hours": (0, None),
    "backup_interval_hours": (0, None),
    "doctrine_sync_interval_hours": (0, None),
    # -- Doctrine tool (see doctrine/config.py's DoctrineConfig) --
    "doctrine_structure_id": (1, None),
    "stockpile_location_id": (1, None),
    "cargo_tolerance_pct": (0, 1),
    # -- Ore & Minerals / "refining" tool (see refining/config.py's RefiningConfig) --
    "security_status": (-1, 1),
    "reprocessing_skill_level": (0, 5),
    "reprocessing_efficiency_skill_level": (0, 5),
    "scrapmetal_processing_skill_level": (0, 5),
    "refining_tax_rate": (0, 1),
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


def validate_config_overrides(cfg: Any, overrides: dict[str, Any], cfg_type: Optional[type] = None) -> None:
    """Type- and range-checks every `overrides` value against its field's
    declared type on `cfg` (a TradingConfig or ProductionConfig instance) -
    raises ConfigError on the *first* mismatch, before anything is applied,
    so a bad value never lands half-applied (some fields written, others
    not). Unknown keys (not a real field on `cfg`) are silently skipped
    here, same as apply_config_overrides - this is about catching a wrong
    *type*/*range* for a real field, not about rejecting unrecognized keys.

    `cfg_type` defaults to `type(cfg)` - correct whenever `cfg` is a plain
    TradingConfig/ProductionConfig instance (every test in this repo, and
    load_trading_config/load_production_config below), and *only* wrong
    when `cfg` is a ConfigProxy (type(cfg) would return ConfigProxy itself,
    not the real dataclass it forwards to) - callers passing a proxy
    (do_update_settings) pass the concrete type explicitly instead."""
    hints = typing.get_type_hints(cfg_type or type(cfg))
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
    # "The Forge" - main trade hub used for imports. Every "Jita price" this
    # app computes (esi_client.region_order_stats(jita_region_id, ...)) is
    # really a *region*-wide order-book stat - The Forge includes Perimeter
    # and other stations besides Jita 4-4 itself, and ESI has no
    # station-level order filter to narrow it further. Confirmed as
    # low-impact in practice (2026-08-18): the 5th-percentile pricing (see
    # README's "Notes/limitations") plus Jita 4-4's own overwhelming share of
    # Forge trade volume means a non-Jita-4-4 order rarely drives the
    # percentile price - not "fixed", just not worth the added complexity of
    # a location_id filter unless real evidence shows otherwise.
    jita_region_id: int = 10000002
    reference_region_id: int = 10000009      # "Insmother" - used as price-history reference region
    # Destination player structure ("C-J" in this project's own docs) - no
    # sane default across installs, must be set in config.yaml. own_orders.py/
    # trade_reconciliation.py compare location_id == structure_id, which is
    # safely a no-op (never matches) while this is unset.
    structure_id: Optional[int] = None
    # appraise.gnf.lt market slug for structure_id (case-sensitive) - same
    # idea as ProductionConfig.home_market. Optional failsafe (confirmed
    # with the user 2026-08-24): Shortlist refresh, Ore Shortlist refresh
    # and Reprocessing Quote normally price the structure leg from the real
    # ESI order book (needs a logged-in seller with docking access), but
    # fall back to a Goonmetrics current-price snapshot for it when no
    # seller is logged in or the ESI call itself fails - see
    # esi_client.ESIClient.structure_order_stats_bulk_or_goonmetrics.
    # Deliberately NOT used by own_orders.check_undercut (Undercut Check) -
    # that function's whole purpose is comparing against real competing
    # orders, which a Goonmetrics snapshot can't answer; a fallback there
    # could silently report "not undercut" when a real order says
    # otherwise, worse than a hard failure. Left unset, every one of those
    # three actions keeps today's exact behavior (hard ActionError).
    structure_market_slug: Optional[str] = None

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
    # shortlist item that's been continuously "No market data"/"Skip" - a
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
    doctrine_sync_interval_hours: float = 12.0          # doctrine do_sync_contracts


@dataclass
class OAuthConfig:
    """EVE SSO (OAuth2 + PKCE) configuration. Secrets come from environment."""
    client_id: str = field(default_factory=lambda: os.getenv("EVE_SSO_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("EVE_SSO_CLIENT_SECRET", ""))
    callback_host: str = field(default_factory=lambda: os.getenv("EVE_SSO_CALLBACK_HOST", "localhost"))
    callback_port: int = field(default_factory=lambda: _int_env("EVE_SSO_CALLBACK_PORT", 8000))
    # Signs the access-gate session cookie (see access_gate.py) - a secret,
    # not a config.yaml value, same reasoning as client_id/client_secret.
    # Empty by default: access_gate.py refuses to issue/accept sessions
    # without one (see its own docstring) rather than silently signing with
    # an empty/predictable key.
    session_secret_key: str = field(default_factory=lambda: os.getenv("SESSION_SECRET_KEY", ""))
    authorize_url: str = "https://login.eveonline.com/v2/oauth/authorize"
    token_url: str = "https://login.eveonline.com/v2/oauth/token"
    jwks_url: str = "https://login.eveonline.com/oauth/jwks"
    issuer: str = "login.eveonline.com"
    token_store_path: Path = field(default_factory=lambda: DATA_DIR / "tokens.json")
    # Where api/routers/auth.py's callback() redirects the browser back to
    # after SSO - the React dev server's own origin in the usual two-process
    # local dev setup (see README's "Running the app"), a *different* origin
    # than the backend itself (hence needing to be a full origin, not a
    # relative path). In the single-process "real run" deployment (FastAPI
    # serves the built frontend on the same origin as the API - see app.py's
    # StaticFiles mount) this should be that same public origin instead, e.g.
    # https://eve-trader.example.com - see deploy/README.md.
    frontend_origin: str = field(default_factory=lambda: os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"))
    # Full override for redirect_uri below, e.g.
    # "https://eve-trader.example.com/api/auth/callback" - needed once
    # deployed behind HTTPS/a reverse proxy, since the default construction
    # below always builds a plain "http://host:port" URL. Must exactly match
    # whichever URL is registered for this app at
    # https://developers.eveonline.com/applications.
    redirect_uri_override: str = field(default_factory=lambda: os.getenv("EVE_SSO_REDIRECT_URI", ""))

    @property
    def redirect_uri(self) -> str:
        """Must match the FastAPI backend's mounted callback route
        (api/routers/auth.py's router is included at prefix "/api/auth") and
        the callback URL registered for this app at
        https://developers.eveonline.com/applications. Point
        EVE_SSO_CALLBACK_PORT at the backend's uvicorn port (default 8000) -
        or, once behind HTTPS/a reverse proxy where that plain "http://host:
        port" shape no longer matches the real public URL, set
        EVE_SSO_REDIRECT_URI directly instead (see redirect_uri_override)."""
        if self.redirect_uri_override:
            return self.redirect_uri_override
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


@dataclass
class AccessConfig:
    """The access-gate on/off switch - checked once, right after a
    scope-less EVE SSO login (see access_gate.py), before any other API
    route is reachable.

    Off by default (access_gate_enabled=False) - same "opt-in, never starts
    happening without the user explicitly asking" reasoning as
    TradingConfig.scheduler_enabled: this app has run for months as a
    trusted-localhost tool with zero login wall, and turning that on by
    default would break every existing local dev workflow (README's
    documented `uvicorn` + `npm run dev` flow) the moment this field shipped.
    Meant to be flipped on specifically when hosting this somewhere reachable
    beyond localhost.

    Used to hold the character/corp/alliance allowlist directly
    (allowed_character_ids/allowed_corporation_ids/allowed_alliance_ids) -
    that moved to the tenant_registry_entries table (see
    docs/phase3_schema.sql, storage.resolve_tenant_id) once a login needed
    to resolve *which tenant*, not just yes/no. Deliberately NOT editable
    via the Settings page/API (unlike every other *Config field) - see
    api/routers/*.py's Settings endpoints, none of which expose this field.
    An authenticated session being able to flip its own gate would defeat
    the point of it; that should require actual filesystem/SSH access to
    config.yaml."""
    access_gate_enabled: bool = False


_trading_config_yaml_cache: dict[Path, TradingConfig] = {}


def load_trading_config(path: Path = DEFAULT_CONFIG_PATH) -> TradingConfig:
    """Cached per `path` (module-level, no TTL/invalidation) after the first
    real disk read - confirmed real gap: resolve_and_set_trading_config below
    calls this on *every* gate-enabled request's tenant_scope.enter_tenant
    (see AccessGateMiddleware) and every scheduler tick, but config.yaml is
    hand-maintained and never rewritten by the running app (Settings-page
    saves go to Postgres's tenant_settings, not this file - see CLAUDE.md's
    Config section) - a manual edit to it already needs a process restart to
    take effect (nothing watches its mtime), so re-parsing the same
    unchanged file on every single request was pure waste with zero
    staleness risk from caching it. Returns a deep copy of the cached base
    config each call, never the cached instance itself, so a caller that
    mutates its result (e.g. resolve_and_set_trading_config's
    apply_config_overrides, applying one tenant's own overrides on top)
    can't corrupt what every other call/tenant sees."""
    if path not in _trading_config_yaml_cache:
        cfg = TradingConfig()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                overrides = yaml.safe_load(f) or {}
            validate_config_overrides(cfg, overrides)  # fail fast, at startup, with the specific bad field named
            apply_config_overrides(cfg, overrides)
        _trading_config_yaml_cache[path] = cfg
    return copy.deepcopy(_trading_config_yaml_cache[path])


def load_access_config(path: Path = DEFAULT_CONFIG_PATH) -> AccessConfig:
    """Same config.yaml, loaded the same way as TradingConfig - a separate
    small dataclass rather than extra fields bolted onto TradingConfig
    because this is a cross-cutting, app-wide concern (protects Production's
    routes too, not just Trading's), same reasoning that already gave
    OAuthConfig its own dataclass."""
    cfg = AccessConfig()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        validate_config_overrides(cfg, overrides)
        apply_config_overrides(cfg, overrides)
    return cfg


# TRADING_CONFIG is a ConfigProxy, not a plain TradingConfig instance - see
# ConfigProxy's docstring above. The ContextVar's `default=` is the same
# instance load_trading_config() would have returned directly before this
# phase - used whenever nothing has resolved a per-tenant instance (tests,
# any code path outside a request/scheduler-job/CLI-command context).
# resolve_and_set_trading_config (below) is what actually calls `.set()`,
# wired up via tenant_scope.enter_tenant (see AccessGateMiddleware/
# scheduler.py) - Phase 2 built this ContextVar but nothing called `.set()`
# on it until Phase 4 (multi-tenant migration).
_trading_config_var: contextvars.ContextVar[TradingConfig] = contextvars.ContextVar(
    "trading_config", default=load_trading_config()
)
TRADING_CONFIG = ConfigProxy(_trading_config_var)
OAUTH_CONFIG = OAuthConfig()
# AccessConfig deliberately stays a plain instance, never a ConfigProxy -
# per its own docstring, it's operator-only and becomes the tenant registry
# in Phase 3, never a per-tenant Settings-page value.
ACCESS_CONFIG = load_access_config()


def save_tenant_config_overrides(scope: str, updates: dict[str, Any], *live_configs, cfg_type: type) -> None:
    """Postgres-backed replacement for the old config.yaml-writing
    save_config_overrides - persists `updates` into tenant_settings for
    `scope` ('trading'/'production') and immediately applies them to every
    object in `live_configs` (the live ConfigProxy objects) so a
    Settings-page save takes effect without restarting the app.

    Validates `updates` against every live config's field types *before*
    persisting anything (see validate_config_overrides) - raises ConfigError
    on a bad value (e.g. a string where a number is expected) instead of
    writing a half-invalid row or letting it crash later, deep inside
    engine.py, the first time that field is actually used. Callers
    (do_update_settings) convert ConfigError to ActionError so it reaches
    the Settings page as a normal, clear error.

    `cfg_type` is passed explicitly rather than derived from `type(cfg)` -
    see validate_config_overrides's own docstring for why (`live_configs`
    are ConfigProxy objects here, not plain dataclass instances).
    """
    for cfg in live_configs:
        validate_config_overrides(cfg, updates, cfg_type)

    storage.save_tenant_settings(scope, updates)

    for cfg in live_configs:
        apply_config_overrides(cfg, updates)


def resolve_and_set_trading_config(tenant_id: str) -> contextvars.Token:
    """Loads `tenant_id`'s own TradingConfig (defaults + config.yaml as the
    base, same as load_trading_config(), then that tenant's tenant_settings
    overrides on top) and sets it as _trading_config_var's current value for
    the caller's scope - callers must reset via reset_trading_config(token).

    Requires storage's own tenant contextvar to already be set to this same
    tenant_id first (load_tenant_settings reads via storage.connect(),
    itself tenant-scoped) - see tenant_scope.enter_tenant, which sets both
    in the right order rather than calling this directly."""
    cfg = load_trading_config()
    overrides = storage.load_tenant_settings("trading")
    if overrides:
        apply_config_overrides(cfg, overrides)
    return _trading_config_var.set(cfg)


def reset_trading_config(token: contextvars.Token) -> None:
    _trading_config_var.reset(token)
