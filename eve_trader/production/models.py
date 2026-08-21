from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StockTarget:
    """One row of the Stock Targets editor."""
    type_id: int
    type_name: str
    backup_stock: float


@dataclass
class InventoryRow:
    """Target vs. current stock for one type.
    `current_stock` is manual entry (storage.manual_stock) plus ESI-derived assets/
    incoming industry jobs at the home location - see engine._current_stock."""
    type_id: int
    type_name: str
    activity: str          # "Reaction" | "Tech I" | "Tech II" | "Input" (no known blueprint)
    backup_stock: float
    current_stock: float
    total_missing: float


@dataclass
class BuildOrBuyDecision:
    type_id: int
    type_name: str
    decision: str           # "Build" | "Buy"
    quantity: float
    unit_buy_price: Optional[float]
    unit_build_cost: Optional[float]
    reason: str             # e.g. "manual override", "no blueprint", "build < buy"


@dataclass
class BuyListEntry:
    type_id: int
    type_name: str
    quantity: float  # already net of on-hand stock - see on_hand_pct
    unit_price: Optional[float]
    total_price: Optional[float]
    # % of this item's *total* demand (quantity + whatever's already on hand)
    # already covered by owned stock across every location (see
    # engine._current_stock) - 0-100. 0 means none of it is on hand yet (or
    # it's a brand-new demand this run); 100 would mean it's fully covered
    # and wouldn't appear on the Buy List at all.
    on_hand_pct: float = 0.0
    buy_from: Optional[str] = None  # "C-J" | "Jita" | None (no sell order anywhere)


@dataclass
class BuildJobEntry:
    type_id: int
    type_name: str
    blueprint_type_id: int
    activity: str           # "Manufacturing" | "Reaction"
    quantity: float
    job_runs: int
    job_time_seconds: float
    unit_build_cost: Optional[float]  # materials + facility fee, per unit - not the raw facility fee alone (see engine.py's own job_cost local var)
    decryptor: Optional[str] = None  # Tech II only: which decryptor's ME/TE was assumed
    job_category: Optional[str] = None  # "where do I start this" grouping - see engine.job_category


@dataclass
class InventionNeedRow:
    """One row of the Invention tab's 'what to invent' list: how many
    invention runs to queue for a Tech II item's T1 blueprint so the
    resulting BPCs cover the manufacturing runs the Bauliste needs - see
    engine.plan_production's invention_list."""
    type_id: int             # Tech II product's own (manufacturing) blueprint's product type
    type_name: str
    t1_blueprint_type_id: int
    t1_blueprint_name: str
    decryptor: str
    probability: float
    output_runs: float       # BPC runs produced per successful invention
    runs_needed: int         # total manufacturing runs the Bauliste requires
    bpcs_needed: int         # ceil(runs_needed / output_runs)
    recommended_invention_runs: int  # ceil(bpcs_needed / probability) - expected attempts


@dataclass
class AssetPlanJob:
    """One row of the asset-aware Bauliste (engine.plan_asset_optimized): like
    BuildJobEntry, but job_runs is netted against real owned stock at *every*
    level of the recursive bill of materials (not just the top-level stock
    target), and runs_ready_now says how many of those runs can start right
    now vs. are blocked waiting on a scarcer material."""
    type_id: int
    type_name: str
    blueprint_type_id: int
    activity: str            # "Manufacturing" | "Reaction"
    quantity: float
    job_runs: int             # runs actually needed after netting stock against pooled demand
    runs_ready_now: int       # of job_runs, how many the *scarcest* direct material covers right now
    job_time_seconds: float
    unit_build_cost: Optional[float]  # materials + facility fee, per unit - not the raw facility fee alone (see engine.py's own job_cost local var)
    decryptor: Optional[str] = None
    job_category: Optional[str] = None  # "where do I start this" grouping - see engine.job_category
    # How much of this item's own current demand is already covered by owned
    # stock - 0 (nothing on hand) to 1 (fully covered) - see
    # engine.plan_asset_optimized's stock_coverage_by_id docstring for the
    # two different denominators this can come from (a configured stock
    # target's backup/home/Jita goal, or - for a pure intermediate component
    # with no target of its own - the pooled demand of the round it was
    # queued in). None only if neither applies (shouldn't happen for an item
    # that made it into the jobs list at all). This is distinct from
    # runs_ready_now/job_runs ("Blocked"), which is about whether *this
    # item's own* materials are available to build it - stock_coverage is
    # about how much of *this item itself* is already sitting in inventory.
    # Deliberately *not* factored into sort order/priority here - see
    # engine.plan_asset_optimized's docstring for why.
    stock_coverage: Optional[float] = None
    # How many of your currently-free character job slots (see
    # engine._free_slots_by_category) to use for this job's ready runs. The
    # free-slot pool for a category (Manufacturing, shared by Advanced/
    # Capital Components; Reactions, its own pool) is split *across every
    # eligible ready job in that category at once* (engine.
    # _allocate_slots_proportionally, largest-remainder method) - so the
    # numbers across every job sharing a pool add up to (at most) that
    # pool's real total, not each job being told it can have the whole pool
    # to itself. Confirmed real user correction (2026-08-16) after an
    # earlier per-job-in-isolation version: with several ready jobs in the
    # same category, that version recommended the *same* full pool size for
    # every one of them simultaneously, which only makes sense if you're
    # going to work on exactly one of them - the goal here is "how should I
    # split my actual free slots so I can make progress on *all* of them at
    # once." The split is weighted by *time needed* (runs_ready_now x
    # per-run job time), not by raw runs_ready_now - a second, later user
    # correction (2026-08-16): a job with many quick runs shouldn't
    # out-rank one with fewer but much longer runs, since the point is for
    # every job sharing the pool to finish its ready runs at roughly the
    # same time - runs_ready_now still caps each job's own allocation
    # (never more slots than it has ready runs to put on them). Only set
    # for job_category in engine._SLOT_RECOMMENDATION_CATEGORIES (Reactions/
    # Advanced Components/Capital Components - user request 2026-08-15) and
    # only when runs_ready_now > 0 (nothing to split otherwise) - None
    # everywhere else.
    recommended_slots: Optional[int] = None


@dataclass
class LogisticsRow:
    """One row of the Logistik tab: how much of a material is needed *right
    at the structure* a job_category is assigned to, for the direct (one
    level, non-recursive - see engine.logistics_status) materials of every
    currently-planned job in that category, netted against what's actually
    sitting at that structure right now (ESI assets, that specific
    location_id only)."""
    category: str
    location_id: int
    type_id: int
    type_name: str
    needed: float
    available: float
    missing: float
    # Which other configured category-location has the most of this item
    # right now, if any (GitHub issue #4's "where to pull from" hint) - None
    # when nothing's missing, or no other configured location has any.
    pull_from_location_id: Optional[int] = None
    pull_from_available: Optional[float] = None


@dataclass
class DistributionRow:
    """One row of the Logistik tab's Distribution section (GitHub issue #4):
    a recommended move of `quantity` units of `type_id` from the configured
    distribution source (ProductionConfig.distribution_source_location_id,
    falls back to home_location_id) to a category's own assigned station,
    where that category is currently short. See engine.
    distribution_recommendations - when the source can't cover every
    shortfall for the same item, the largest one is covered first."""
    type_id: int
    type_name: str
    from_location_id: int
    to_category: str
    to_location_id: int
    quantity: float


@dataclass
class AssetLocationRow:
    """One row of the Asset Search tab: how much of a searched item sits at
    one specific station/structure, owned by one specific character or corp
    - see storage.search_item_stock_locations for the nested-container
    resolution and (location, owner) grouping."""
    location_id: int
    location_name: Optional[str]  # None if not yet resolved - see logistics/resolve-structure-name for player structures
    owner_name: str
    quantity: float


@dataclass
class MarketStatusRow:
    """One row of the Marktstatus tab: personal stock vs. backup target, and
    home/Jita *market-listed* stock (your own open sell order volume, not
    owned inventory) vs. their own targets."""
    type_id: int
    type_name: str
    backup_target: float
    backup_current: float
    home_target: Optional[float]
    home_listed: float
    jita_target: Optional[float]
    jita_listed: float


@dataclass
class InventionResult:
    t1_blueprint_type_id: int
    t1_blueprint_name: str
    product_type_id: int
    product_name: str
    decryptor: str
    probability: float
    output_runs: float
    datacore_cost: float
    decryptor_cost: float
    total_attempt_cost: float
    expected_cost_per_success: Optional[float]  # total_attempt_cost / probability
    expected_cost_per_run: Optional[float]       # expected_cost_per_success / output_runs
    me: int                                      # resulting BPC material efficiency
    te: int                                      # resulting BPC time efficiency
    material_savings_per_run: float              # (me/100) * reducible_material_cost_per_run, 0 if not supplied
    net_cost_per_run: Optional[float]             # expected_cost_per_run - material_savings_per_run


@dataclass
class IndustryJobRow:
    """One active industry job (character or corp), shown individually even
    if several jobs build the same item."""
    job_id: int
    type_name: str
    activity: str            # "Manufacturing" | "Reaction" | "Invention" | "TE/ME Research" | "Copying"
    runs: int
    quantity: Optional[float]  # runs * product qty/run, None if unknown (e.g. research jobs have no product)
    status: str
    start_date: Optional[str]
    end_date: Optional[str]
    remaining_seconds: Optional[float]
    installer_name: str
    # quantity x unit price, priced the same way engine.stock_value prices
    # owned stock (C-J sell quote, falling back to Jita sell quote) - None if
    # quantity itself is None (no product - research/copying jobs) or if
    # neither market has a sell quote right now.
    output_value: Optional[float] = None


@dataclass
class CharacterSlotRow:
    character_name: str
    job_type: str             # "Manufacturing" | "Reactions" | "Science"
    total_slots: int
    used_slots: int
    free_slots: int


@dataclass
class OwnedBlueprintRow:
    """One owned blueprint (character or corp), aggregated across every
    location - see production/actions.py do_list_owned_blueprints."""
    type_id: int
    type_name: str
    is_original: bool         # True = BPO (runs == -1 in ESI's model), False = BPC
    quantity: int              # count of copies at this (type_id, is_original, me, te, runs) grouping
    material_efficiency: int
    time_efficiency: int
    runs: Optional[int]        # None for a BPO (infinite), remaining run count for a BPC


@dataclass
class ManualBlueprintCopyCostRow:
    """One manually-registered blueprint-copy purchase cost (GitHub issue
    #40) - the Blueprints page's second table. `type_id` is the *product*
    built from the copy, not the blueprint's own type_id (see
    docs/phase1_schema.sql's manual_blueprint_copy_costs schema comment)."""
    type_id: int
    type_name: str
    purchase_cost: float
    runs: int
    cost_per_run: float  # purchase_cost / runs, computed for display convenience


@dataclass
class UnlistedStockRow:
    """A stock target with physical stock at cfg.home_location_id (C-J) but
    NO open sell order there at all - see production/actions.py do_unlisted_stock."""
    type_id: int
    type_name: str
    stock_quantity: float


@dataclass
class BuildCandidate:
    """A manufacturable item, not currently a configured stock target, where
    building clearly beats buying right now - see engine.discover_build_candidates.
    Ranked by potential_daily_profit, not margin - see that function's
    docstring for why margin alone isn't a useful ranking."""
    type_id: int
    type_name: str
    activity: str
    build_cost: float
    margin: float
    daily_movement: float
    potential_daily_profit: float
    meta_level: Optional[int]


@dataclass
class ShipMarginRow:
    """Production's Margin page - engine.discover_ship_margins (list of every
    ship) and engine.item_margin_detail (search, any item) both produce this
    same shape. Unlike BuildCandidate, not gated by min_margin/existing-
    stock-target status - a pure information view, see discover_ship_margins'
    own docstring."""
    type_id: int
    type_name: str
    activity: str
    home_price: Optional[float]
    jita_price: Optional[float]
    build_cost: Optional[float]
    margin_home: Optional[float]
    margin_jita: Optional[float]
    meta_level: Optional[int] = None
