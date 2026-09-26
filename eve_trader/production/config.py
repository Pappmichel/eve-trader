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
from .constants import HANGAR_DIVISION_FLAGS, RIG_TIERS, STRUCTURE_TYPES


@dataclass
class ProductionConfig:
    # -- Settings tab equivalents --
    component_overbuild: float = 0.7      # extra buffer kept for build-chain components
    bpc_inventory: float = 4.0            # multiplier: 4.0 = 400% BPC stockpile buffer (keep 4x needed BPC runs)
    market_fees: float = 0.0537           # sell-side broker fee + sales tax, subtracted from margins
                                            # (see engine.py _build_margin) - originally derived (like
                                            # TradingConfig.structure_sell_haircut, see that field's own
                                            # T1-01 comment) as SCC surcharge 0.5% + Broker's fee 1.5% +
                                            # Sales tax 3.37% + Safety Tax 0.00% = 5.37%. T1-01
                                            # (2026-09-25) confirmed with the user that SCC surcharge
                                            # applies only to industry jobs, never to a market sell - the
                                            # same mistake T1-01 fixed for structure_sell_haircut applies
                                            # here too (this field IS specifically a market-sell fee, per
                                            # its own docstring and every call site in engine.py). Kept at
                                            # its original 0.0537 default rather than silently
                                            # reinterpreted, same reasoning as structure_sell_haircut - a
                                            # live-configurable value a tenant may already have tuned;
                                            # the corrected value would be 1.5%+3.37% = 0.0487 (0.9513
                                            # retention) - revisit only with the user.
    jita_buy_broker_fee: float = 0.0147   # buy-side broker fee, confirmed against the in-game buy
                                            # screen - applied in pricing.py buy_price/_candidate_prices.
                                            # Duplicates TradingConfig's OWN jita_buy_broker_fee field -
                                            # both are meant to be the exact same real number ("same
                                            # buying character, same broker's-fee rate" - see pricing.py's
                                            # own _candidate_prices docstring), not two tools' independently
                                            # tunable settings. Reviewed 2026-09-26 (business-logic audit
                                            # follow-up) and deliberately NOT merged - see config.py's own
                                            # _FIELD_RANGES comment for why (real UX/test blast radius, a
                                            # decision for the user, not a silent cleanup). Until merged,
                                            # a Settings change to one does NOT propagate to the other.
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
    # Station to move materials *from* on the Logistik tab's Distribution
    # section (GitHub issue #4) - falls back to home_location_id when unset
    # ("the home market acts as the central warehouse" by default), but
    # independently overridable, since the user may want to distribute from
    # somewhere other than the home structure - see engine.
    # distribution_recommendations.
    distribution_source_location_id: Optional[int] = None
    # Dedicated station for invention materials (datacores/decryptors/T1
    # copies) - GitHub issue #9. Independent of job_category_locations
    # (invention isn't itself a job_category bucket) - see engine.
    # invention_logistics.
    invention_location_id: Optional[int] = None
    # Which hangar/office division(s) at home_location_id count as Production's
    # own physical stock (storage.esi_stock_at_location's allowed_flags - see
    # production/constants.py HANGAR_DIVISION_FLAGS) - GitHub issue #90-era
    # hangar-sorting work: Jita imports for every tool (Trading resale stock,
    # Doctrine contract materials, Ore & Minerals ore/ice, Production's own
    # build materials) physically land in one shared corp Wareneingang
    # division first, and _current_stock/_stock_on_hand used to count that
    # whole division (and every other one) as Production's own available
    # material regardless of what it was actually bought for. Empty tuple
    # (the default) = no filter, i.e. today's whole-hangar-counts-everywhere
    # behaviour, unchanged - only meaningful once the operator has actually
    # sorted Production's own stock into specific division(s) and configures
    # this to match.
    stock_hangar_flags: tuple[str, ...] = ()

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

    # Manual escape hatch, highest priority (see engine._job_cost_rate) -
    # overrides whatever the category/flat-system lookup above would
    # otherwise compute, as long as a value is set here. Split into 3, not
    # 2, because the "component" system slot actually feeds two genuinely
    # different rates (Reaction jobs use one, Tech I/II component-group
    # jobs use the other) - the "manufacturing" slot's own reaction rate is
    # never read anywhere, so there's deliberately no override field for it
    # (would be dead weight). None (the default) means "use the computed
    # value, same as today."
    reaction_cost_index_override: Optional[float] = None
    component_cost_index_override: Optional[float] = None
    manufacturing_cost_index_override: Optional[float] = None

    # -- Where you build, split by build profile just like the system cost index
    # above (see production/constants.py STRUCTURE_TYPES / RIG_TIERS /
    # engine.py _structure_profile): reactions typically run in a Refinery,
    # rig-covered component groups in an Engineering Complex with the
    # component ME rigs, Titan/Supercarrier hulls need a genuinely bigger
    # structure than the rest of "Capital Ship" (confirmed with the user
    # 2026-09-16 - a real EVE build-location restriction, e.g. only a Sotiyo,
    # not a smaller Engineering Complex), everything else in a separate
    # Engineering Complex - each can have its own structure + rig.
    reaction_structure_type: str = "Citadel (no bonuses)"
    reaction_rig_tier: str = "No Rig"
    component_structure_type: str = "Citadel (no bonuses)"
    component_rig_tier: str = "No Rig"
    supercapital_structure_type: str = "Citadel (no bonuses)"
    supercapital_rig_tier: str = "No Rig"
    manufacturing_structure_type: str = "Citadel (no bonuses)"
    manufacturing_rig_tier: str = "No Rig"

    # -- Invention (see production/invention.py) --
    encryption_skill_level: int = 4
    datacore_skill_1_level: int = 4
    datacore_skill_2_level: int = 4

    # -- Asset-optimized build list (AssetPlanList) --
    # Optional day-target for the slot-split recommendation: when set, each
    # eligible ready job's own *cap* (the most _allocate_slots_by_priority
    # can hand it) becomes how many slots it would need to finish its ready
    # runs within this many days (see engine._slots_needed_for_days_target),
    # instead of the uncapped runs_ready_now. None (the default) means no
    # day cap - each job's cap is runs_ready_now. Either way, jobs still
    # claim slots in the same priority order - unlock_time_seconds
    # descending (how much blocked job time elsewhere this job would newly
    # unblock), stock_coverage ascending as the tie-break (see
    # engine._allocate_slots_by_priority). Edited on the Asset-Optimized
    # Build List page, not the general Settings tab.
    asset_plan_slot_days_target: Optional[float] = None

    # -- Alchemy reaction alternatives --
    alchemy_reactions_enabled: bool = False   # off by default - see engine.py's
                                                # find_alchemy_alternative/_alchemy_unit_cost;
                                                # when True, a Reaction product is
                                                # sourced via its "Unrefined X"
                                                # formula whenever that is cheaper
                                                # (real Buy/Build list, plus the
                                                # informational compare_alchemy_
                                                # profitability column). When False,
                                                # alchemy formulas are never looked
                                                # up at all, behavior is identical
                                                # to before this feature existed

    # -- Manual tracking (docs/MANUAL_TRACKING_PLAN.md phase 2, question 1) --
    # Default Tenant only - do_resolve_structure_name reads this under
    # enter_tenant(DEFAULT_TENANT_ID) regardless of the caller's own tenant,
    # so no other tenant can see or set it (admin.do_get/set_structure_
    # resolution_fallback are the only read/write paths, Admin-tool-gated).
    # Off by default (operator opt-in): when true, a tenant whose own
    # structure_name_resolution characters can't resolve a location falls
    # back to trying the Default Tenant's own characters for it (the token
    # itself never leaves the server, only {name, solar_system_id} crosses
    # back out).
    global_structure_resolution_fallback: bool = False

    # -- Fuzzwork SDE --
    fuzzwork_csv_base: str = "https://www.fuzzwork.co.uk/dump/latest/csv/"


_STRUCTURE_TYPE_FIELDS = ("reaction_structure_type", "component_structure_type",
                          "supercapital_structure_type", "manufacturing_structure_type")
_RIG_TIER_FIELDS = ("reaction_rig_tier", "component_rig_tier", "supercapital_rig_tier", "manufacturing_rig_tier")


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
    if "stock_hangar_flags" in overrides:
        bad = [f for f in overrides["stock_hangar_flags"] if f not in HANGAR_DIVISION_FLAGS]
        if bad:
            raise ConfigError(f"stock_hangar_flags: {bad!r} are not known hangar divisions. "
                               f"Options: {', '.join(HANGAR_DIVISION_FLAGS)}")


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
