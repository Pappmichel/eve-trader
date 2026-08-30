"""Core Production calculation: for every stock target, decide whether missing
units should be bought or built, and (recursively, since a built item's own
materials go through the same buy-vs-build decision) produce a flat buy list
and a build/job list.

ME/TE stacks in two independently-stacking layers (see constants.py):
1. The blueprint/BPC's own research level:
   - Tech I: prefers your actual owned BPO's real ME/TE (storage.
     get_owned_bpo_best_me_te via _owned_bpo_mods, best across every owned
     Original of that blueprint_id - a BPC's ME/TE doesn't count, see
     _owned_bpo_mods) when you own that BPO; falls back to a flat "perfect"
     BPO research (ME10/TE20, constants.ACTIVITY_MODS) baseline otherwise -
     a deliberate simplification, used only when you don't actually own the
     blueprint (or haven't ESI-synced it).
   - Reaction: flat base (constants.ACTIVITY_MODS), no research at all - a
     real EVE mechanic (reactions have no BPO research), not a simplification,
     so owned-BPO data is never consulted for Reaction even if present.
   - Tech II: the resulting BPC's ME/TE depends on which decryptor it was
     invented with (see production/invention.py) - resolved per item, either
     from a manual override (storage.selected_decryptors) or by picking
     whichever decryptor minimizes *net* cost per build run (invention cost
     amortized over runs, minus the ME material savings that run gives you).
     Falls back to the flat Tech II ACTIVITY_MODS entry if no invention
     recipe is found.
2. Your structure + installed rig (constants.STRUCTURE_TYPES / RIG_TIERS) -
   applied on top of #1, since EVE always stacks these multiplicatively
   regardless of blueprint/BPC research level. Which structure/rig setting
   applies is itself split by build profile (see _structure_profile), same
   split as job_cost_rate below but one step finer-grained: reactions use
   ProductionConfig.reaction_structure_type/reaction_rig_tier (typically a
   Refinery), the item groups covered by the two capital/advanced-component
   ME rigs (constants.COMPONENT_GROUP_IDS) use component_structure_type/
   component_rig_tier, everything else uses manufacturing_structure_type/
   manufacturing_rig_tier - you may well run three different structures.
   Rig bonuses additionally scale with the security status of the system the
   structure sits in (real EVE mechanic, confirmed via rig dogma attributes),
   but Engineering Complex and Refinery/reactor rigs use two *different*
   tables: Engineering Complex is 1x highsec, 1.9x lowsec, 2.1x null-sec/
   wormhole; Refinery/reactor has no highsec case at all (reactor rigs are
   hard-banned from highsec, since reactions themselves can't run there),
   1x lowsec, 1.1x null-sec/wormhole - see constants.rig_security_multiplier's
   is_reaction flag / _security_multiplier_for), read from the local SDE
   cache (static per-system data, not an ESI call). Reactor Time Efficiency
   rigs are a real EVE item (e.g. "Standup M-Set Composite Reactor Time
   Efficiency I/II", confirmed via dogma attributes - not the "no reactor TE
   rig exists" assumption an earlier version of this module made), so a
   reaction's rig te_bonus applies exactly like every other activity's, on
   top of the refinery's own te_multiplier (Tatara -25%, Athanor has no
   reaction-duration bonus of its own at all - confirmed against
   wiki.eveuniversity.org's Refinery page, re-checked 2026-08-18 after an
   earlier, wrong -3% Athanor figure was found here with no matching real
   EVE attribute).
   Likewise a refinery's "yield" stat (Athanor 2%, Tatara 4%) is a
   *reprocessing* bonus, confirmed not to apply to reactions - refineries
   get no ME bonus of their own beyond whatever reactor ME rig is fitted.

Once the material_multiplier from the two layers above is known, every actual
material-quantity computation (_unit_cost, _base_runs, _expand_all,
plan_asset_optimized, logistics_status) must go through _material_qty rather
than a plain `base_qty * material_mult * runs`: real EVE rounds a job's
material requirement UP once for the whole batch of runs, and never reduces
it below `runs` itself (confirmed via wiki.eveuniversity.org's Blueprint
research page) - a material with base_qty=1 (common in blueprints) is never
ME-reduced at all, no matter how large the % reduction, since 1 unit/run is
already the floor.

job_cost_rate (facility tax) is priced off a *live* system cost index, split by
*what's being built* - not by Tech I/II/Reaction: reactions and the item
groups covered by the two capital/advanced-component ME rigs
(constants.COMPONENT_GROUP_IDS) use ProductionConfig.component_system_id,
everything else uses manufacturing_system_id (see _job_cost_rate) - one
system per build category rather than one global system. Note this system
split is 2-way (component vs. manufacturing), one level coarser than the 3-way
structure/rig split above, since reactions and rig-covered components are
commonly hosted in the same low-cost-index system even when built in
different structures. Falls back to the flat ACTIVITY_MODS job_cost_rate if
the relevant system isn't configured or ESI can't be reached - note a
system's cost index is a real EVE mechanic that only affects job installation
*cost*, never ME/TE (which is purely blueprint/BPC research + structure/rig,
per the layers above). job_cost_rate itself is a unitless rate - the actual
job_cost ISK amount is job_cost_rate x EIV (Estimated Item Value), *not* a
percentage markup on the real material cost. Confirmed real EVE formula
(wiki.eveuniversity.org/Manufacturing): `job_cost_rate = system_index x
structure_cost_bonus + facility_tax_rate + SCC_SURCHARGE_RATE` - the tax/
surcharge terms are additive, not folded into the index multiplication (see
_job_cost_rate).
EIV sums each material's *base* (ME 0, unreduced) quantity times its
ESI-published adjusted_price (a CCP-regulated valuation, decoupled from real
market prices) - see _unit_cost. This mirrors the real (community
reverse-engineered, since CCP never published it) job-fee formula; using the
ME-reduced quantity/real market price for the fee itself, as an earlier
version of this module did, would both use the wrong quantity basis and the
wrong price basis.

Other known simplifications:
- Tech I vs. Tech II is classified by whether a real invention recipe exists
  for the blueprint, not by metaLevel (see classify_activity's docstring for
  why metaLevel alone is unreliable).
- Current stock is manual entry (storage.manual_stock) plus ESI-derived assets/
  incoming industry jobs, corp-wide unless ProductionConfig.home_location_id is
  set (see _current_stock). _expand nets this against demand at *every* level
  of the bill of materials (not just the top-level stock target), each level
  sized up by ProductionConfig.component_overbuild first so intermediate
  components keep a buffer instead of being built down to exactly zero - a
  shared stock_used ledger (threaded through the whole plan_production call)
  keeps the same physical stock from being counted against two different
  branches that both need it. plan_asset_optimized (a separate, second
  Bauliste) does something related but stricter: it also figures out, per
  job, how many of its runs are startable *right now* vs. still blocked on an
  upstream material shortfall - not just the net quantity still needed.
- Recursion is capped at MAX_DEPTH to guard against unexpected SDE cycles.

market_status() is a separate, simpler read: personal stock vs. backup target,
plus home/Jita *market-listed* stock (open sell order volume, not owned
inventory) vs. their own targets - same three targets/sources _total_missing()
uses to size Buy/Build demand, just presented as three independent rows
instead of summed into one number.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Iterable, Optional

from .. import storage
from ..config import TRADING_CONFIG
from . import invention, pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import (
    ACTIVITY_MODS, ACTIVITY_REACTION, ADVANCED_COMPONENT_GROUP_IDS, CAPITAL_COMPONENT_GROUP_IDS,
    CHARGE_CATEGORY_ID, COMPONENT_GROUP_IDS, DEADSPACE_META_GROUP_ID, DECRYPTORS, DRONE_CATEGORY_ID,
    FACTION_META_GROUP_ID, FIGHTER_CATEGORY_ID, MODULE_CATEGORY_ID, OFFICER_META_GROUP_ID,
    SCC_SURCHARGE_RATE, SHIP_CATEGORY_ID, SHIP_SIZE_GROUP_IDS, SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID,
    STORYLINE_META_GROUP_ID, SUBSYSTEM_GROUP_IDS,
    rig_security_multiplier, structure_rig_multiplier,
)
from .jobs import character_slot_overview
from .models import (
    AssetPlanJob, BuildJobEntry, BuyListEntry, DistributionRow, InventionNeedRow, InventoryRow, LogisticsRow,
    MarketStatusRow, T1BpcInventionNeedRow,
)

MAX_DEPTH = 10

CostIndices = dict[str, dict[str, float]]
# {"component"|"manufacturing": {"manufacturing"|"reaction": rate}} - plus,
# per job_category actually configured with a resolved-system structure in
# Logistik (GitHub issue #12), a "category:<name>" entry of the same shape -
# see _job_cost_rate/_PlanContext.


def _material_qty(base_qty: float, material_mult: float, runs: float) -> float:
    """Real EVE material-quantity rounding (wiki.eveuniversity.org Blueprint
    research page): a job's material requirement is rounded UP once for the
    whole batch of `runs`, and can never drop below `runs` itself - ME never
    reduces a material below 1 unit/run, no matter how large the reduction.
    Every other computation in this module that previously did a plain
    `base_qty * material_mult * runs` must go through this instead, or a
    base_qty=1 material (common in blueprints) gets silently under-counted by
    its full ME reduction instead of being floored at `runs`."""
    return max(runs, math.ceil(round(base_qty * material_mult * runs, 2)))


def classify_activity(type_id: int) -> tuple[str, Optional[tuple[int, int, float]]]:
    """Returns (activity_label, blueprint_info). blueprint_info is
    (blueprint_type_id, activity_id, product_qty_per_run), or None if `type_id`
    has no known Manufacturing/Reaction formula (must be bought).

    "Tech II" (meaning: decryptor-invented, routed through _tech_ii_mods) is
    decided *solely* by whether a real invention recipe exists for the
    blueprint (storage.find_invention_recipe_by_product_type_id) - not by
    metaLevel. This used to be "metaLevel>=2 OR has an invention recipe" to
    catch Tech III hulls/subsystems, whose metaLevel doesn't reliably track
    "genuinely invented" (confirmed against real SDE data: a Loki hull is
    metaLevel 5, but its "Loki Core - Augmented Nuclear Reactor" subsystem is
    metaLevel 1, even though it's genuinely invented via a real T1-equivalent
    blueprint) - but that metaLevel>=2 fallback turned out to be both
    unnecessary (find_invention_recipe_by_product_type_id already correctly
    catches the Loki Core case directly, no metaLevel needed) and actively
    wrong elsewhere: confirmed live (2026-07-16) that metaLevel>=2 also
    covers every Faction/Pirate ship (Machariel, Nestor: metaLevel 8),
    Officer/Deadspace module, and faction booster/drug - none of which are
    actually decryptor-invented (find_invention_recipe_by_product_type_id
    correctly returns None for all of them; they're built from their own
    real, directly-researchable blueprint, same as a Tech I item, just not
    obtainable from an NPC seller). The metaLevel branch alone misclassified
    854 of 4208 scanned manufacturable items as "Tech II" in a live scan -
    each one then got priced off the flat Tech II ACTIVITY_MODS ME/TE
    baseline (and shown as "Tech II" in the Bauliste/Build Candidates UI)
    instead of the correct Tech I-style baseline (owned BPO ME/TE if you
    have it, else the flat "perfect BPO" assumption). Tech III is NOT
    otherwise special-cased here: CCP removed the old relic-based "Reverse
    Engineering" mechanic years ago (confirmed empirically - the current SDE
    has zero rows for activity_id 7), so Tech III now uses the exact same
    activity_id=8 Invention this tool already models for Tech II, with no
    separate cost/probability machinery needed.

    Non-invented items get a second check against the SDE's real metaGroupID
    (storage.get_sde_type, populated from Fuzzwork's invMetaTypes.csv - see
    production/sde.py): "Faction" (confirmed with the user, 2026-07-16 -
    "Faction items are de facto T1 items, but add a separate category for
    them"), extended 2026-07-17 to "Storyline" and
    "Officer" the same way (both confirmed to have real, blueprint-backed,
    market-priceable products in this app's own cached SDE - not just SDE
    rows that happen to exist but are never reachable here) and "Deadspace"
    for completeness (currently unreachable - zero blueprint-backed
    Deadspace products exist in the SDE, deadspace modules are drop-only -
    but costs nothing to check for a future SDE that might add one). Falls
    back to plain "Tech I" if metaGroupID matches none of these. This is a
    *label* distinction for the user's own tracking/filtering, not a purely
    cosmetic one: all four of these blueprint types are fixed at ME0/TE0 and
    can never be researched or changed at all (Faction confirmed with the
    user 2026-07-16; Storyline/Officer share the same non-researchable
    treatment by the same reasoning - see constants.ACTIVITY_MODS) - unlike a
    genuine Tech I BPO, which CAN be researched up to ME10/TE20 - so none of
    them get Tech I's "assumes perfect research, or your real owned BPO's
    ME/TE if better" treatment (see _activity_mods)."""
    bp = storage.get_blueprint_for_product(type_id)
    if bp is None:
        return "Input", None
    blueprint_id, activity_id, _ = bp
    if activity_id == ACTIVITY_REACTION:
        return "Reaction", bp
    if storage.find_invention_recipe_by_product_type_id(blueprint_id) is not None:
        return "Tech II", bp
    sde_type = storage.get_sde_type(type_id)
    meta_group_id = sde_type[7] if sde_type else None
    meta_group_labels = {
        FACTION_META_GROUP_ID: "Faction",
        STORYLINE_META_GROUP_ID: "Storyline",
        OFFICER_META_GROUP_ID: "Officer",
        DEADSPACE_META_GROUP_ID: "Deadspace",
    }
    return meta_group_labels.get(meta_group_id, "Tech I"), bp


def _is_component(type_id: int) -> bool:
    """True if `type_id` is in one of the item groups covered by the two
    capital/advanced-component ME rigs (constants.COMPONENT_GROUP_IDS)."""
    sde_type = storage.get_sde_type(type_id)
    group_id = sde_type[1] if sde_type else None
    return group_id in COMPONENT_GROUP_IDS


def job_category(type_id: int) -> Optional[str]:
    """Which of the "where do I start this job" buckets `type_id` belongs to
    - Reactions / Advanced Components / Capital Components / Equipment /
    Drones & Ammunition / Small|Medium|Large Ships / Capital Ship - purely a
    Bauliste display grouping (see constants.py's "job category" section);
    doesn't feed ME/TE/job-cost, which stays the existing 3-way reaction/
    component/manufacturing split (_structure_profile). None for anything
    that doesn't have a blueprint (nothing to "start") or doesn't fall into
    any of the buckets (e.g. Blueprints themselves, Skillbooks, PI output)."""
    activity, bp = classify_activity(type_id)
    if activity == "Reaction":
        return "Reactions"
    if bp is None:
        return None
    sde_type = storage.get_sde_type(type_id)
    group_id = sde_type[1] if sde_type else None
    if group_id in CAPITAL_COMPONENT_GROUP_IDS:
        return "Capital Components"
    if group_id in ADVANCED_COMPONENT_GROUP_IDS:
        return "Advanced Components"
    if group_id in SUBSYSTEM_GROUP_IDS:
        return "Medium Ships"
    for size, group_ids in SHIP_SIZE_GROUP_IDS.items():
        if group_id in group_ids:
            return size
    category_id = storage.get_type_category(type_id)
    if category_id in (CHARGE_CATEGORY_ID, DRONE_CATEGORY_ID, FIGHTER_CATEGORY_ID):
        return "Drones & Ammunition"
    if category_id == MODULE_CATEGORY_ID:
        return "Equipment"
    return None


def _structure_profile(activity: str, type_id: int) -> str:
    """Which structure/rig setting applies: reactions use the reaction profile
    (typically a Refinery), rig-covered component groups use the component
    profile (an Engineering Complex with the component ME rigs), everything
    else uses the manufacturing profile - each independently configurable
    (ProductionConfig.reaction_/component_/manufacturing_structure_type +
    _rig_tier), since you may well build in three different structures."""
    if activity == "Reaction":
        return "reaction"
    return "component" if _is_component(type_id) else "manufacturing"


def _structure_rig(profile: str, cfg: ProductionConfig) -> tuple[str, str]:
    if profile == "reaction":
        return cfg.reaction_structure_type, cfg.reaction_rig_tier
    if profile == "component":
        return cfg.component_structure_type, cfg.component_rig_tier
    return cfg.manufacturing_structure_type, cfg.manufacturing_rig_tier


def _security_multiplier_for(structure_profile: str, cfg: ProductionConfig) -> float:
    """Rig bonuses scale with the security status of the system the structure
    sits in (see constants.rig_security_multiplier - reaction/refinery rigs use
    a different table than Engineering Complex rigs, hence is_reaction below).
    Reaction/component structures use the component system's security,
    manufacturing structures use the manufacturing system's - same system
    mapping as the cost index (see _job_cost_rate), since that's the only
    system info configured per build category. Read from the local SDE cache
    (static data), not ESI."""
    system_id = cfg.component_system_id if structure_profile in ("reaction", "component") else cfg.manufacturing_system_id
    return rig_security_multiplier(storage.get_system_security(system_id), is_reaction=(structure_profile == "reaction"))


def _job_cost_rate(activity: str, type_id: int, cfg: ProductionConfig, cost_indices: CostIndices) -> float:
    """Live system-cost-index-derived job cost rate, split by build profile
    (see module docstring). Falls back to the flat ACTIVITY_MODS rate if that
    profile's system isn't configured or its cost index is missing.

    Confirmed real EVE formula (wiki.eveuniversity.org/Manufacturing):
    `Total job cost = EIV * ((system cost index * structure bonus) +
    facility tax + SCC surcharge)` - facility tax and the SCC surcharge are
    flat *additive* terms, not folded into the cost-index multiplier the way
    this function used to compute it (index * cost_mult only, confirmed real
    bug: every job/build cost estimate understated the true ISK fee by the
    whole facility-tax + surcharge amount, at least ~4.25% of EIV at the NPC
    defaults). SCC surcharge is a fixed CCP-wide rate (not configurable, unlike
    facility_tax_rate which a player structure's owner can change).

    GitHub issue #12: prefers the cost index of the system `type_id`'s own
    job_category is actually configured to build in (Logistik's per-category
    structure assignment, resolved to a system by storage.
    load_category_system_ids and stashed into cost_indices under a
    "category:<name>" key by _PlanContext - see CostIndices' own comment)
    over the flat component/manufacturing 2-way split, since a real account
    can easily have build categories split across more than two systems.
    Falls back to that flat split when the category has no assigned
    structure, or its system hasn't been resolved yet - so this is a
    strict refinement, never a regression, for anyone not using Logistik at
    all. The job_category(type_id) lookup itself (re-derives classify_
    activity - a real storage/tenant-scoped call, unlike everything else in
    this function) is only made at all when cost_indices actually carries at
    least one "category:" entry - the overwhelming majority of call sites
    (every _activity_mods call whose job_cost_rate is immediately discarded,
    material_mult/time_mult-only formula tests included) pass a plain
    component/manufacturing dict or {} and must stay exactly as cheap and
    storage-independent as before.

    cfg.reaction_cost_index_override/component_cost_index_override/
    manufacturing_cost_index_override (confirmed with the user 2026-08-27)
    outrank everything above, including the per-category lookup - a manual
    value always wins when set, skipping the category/system lookup
    entirely (not just as a tiebreaker), so it works even with no system
    configured at all. Three fields, not two, because the "component"
    profile alone actually needs both a reaction rate and a manufacturing
    rate (see esi_activity below) - there's deliberately no override for
    the "manufacturing" profile's own reaction rate, since that combination
    is never read by any code path (Reaction jobs always resolve to the
    "component" profile, never "manufacturing")."""
    structure_type, rig_tier = _structure_rig(_structure_profile(activity, type_id), cfg)
    cost_mult, _, _ = structure_rig_multiplier(structure_type, rig_tier)
    esi_activity = "reaction" if activity == "Reaction" else "manufacturing"
    is_component_profile = activity == "Reaction" or _is_component(type_id)

    if activity == "Reaction":
        override = cfg.reaction_cost_index_override
    elif is_component_profile:
        override = cfg.component_cost_index_override
    else:
        override = cfg.manufacturing_cost_index_override

    if override is not None:
        index = override
    else:
        index = None
        if any(key.startswith("category:") for key in cost_indices):
            category = job_category(type_id)
            if category is not None:
                index = cost_indices.get(f"category:{category}", {}).get(esi_activity)
        if index is None:
            system_profile = "component" if is_component_profile else "manufacturing"
            index = cost_indices.get(system_profile, {}).get(esi_activity)

    index_rate = ACTIVITY_MODS[activity].job_cost_rate if index is None else index
    return index_rate * cost_mult + cfg.facility_tax_rate + SCC_SURCHARGE_RATE


def _owned_bpo_mods(blueprint_id: Optional[int]) -> Optional[tuple[float, float]]:
    """Real (material_multiplier, time_multiplier) from an owned BPO's actual
    ME/TE (storage.get_owned_bpo_best_me_te), if you own `blueprint_id` -
    None otherwise (falls back to the flat "perfect research" baseline, see
    _activity_mods). Only meaningful for Tech I - Reaction has no BPO
    research at all (real EVE mechanic) and Tech II/III use the decryptor's
    ME/TE instead (_tech_ii_mods), never a BPO's own stat."""
    if blueprint_id is None:
        return None
    best = storage.get_owned_bpo_best_me_te(blueprint_id)
    if best is None:
        return None
    me, te = best
    return (1 - me / 100, 1 - te / 100)


def _activity_mods(activity: str, type_id: int, cfg: ProductionConfig,
                    cost_indices: CostIndices, blueprint_id: Optional[int] = None) -> tuple[float, float, float]:
    """Returns (material_multiplier, time_multiplier, job_cost_rate) for Tech I
    / Reaction with your structure/rig bonus applied on top, and a live
    system-cost-index-derived job_cost_rate. Tech II doesn't use this for
    ME/TE - see _tech_ii_mods, which applies the same structure/rig
    multiplier to its per-item decryptor result.

    For Tech I, prefers your actual owned BPO's real ME/TE (_owned_bpo_mods,
    requires `blueprint_id`) over the flat "perfect research" (ME10/TE20)
    baseline (constants.ACTIVITY_MODS) - callers that have `blueprint_id`
    handy should always pass it; it's optional only because a couple of
    call sites (e.g. logistics' _direct_material_mult) don't have easy access
    to it. Reaction never uses owned-BPO data (real EVE mechanic: reactions
    have no BPO research at all) - neither does Faction/Storyline/Officer/
    Deadspace: their blueprints are always ME0/TE0 and can't be researched at
    all (Faction confirmed with the user 2026-07-16; same treatment applied
    to the other three, see classify_activity), so their flat
    constants.ACTIVITY_MODS entry (1.00/1.00) is the one and only correct
    value, not a fallback to override with owned data the way Tech I's
    "assumes perfect research" baseline is."""
    base = ACTIVITY_MODS[activity]
    structure_profile = _structure_profile(activity, type_id)
    structure_type, rig_tier = _structure_rig(structure_profile, cfg)
    security_multiplier = _security_multiplier_for(structure_profile, cfg)
    _, me_mult, te_mult = structure_rig_multiplier(structure_type, rig_tier, security_multiplier)
    job_cost_rate = _job_cost_rate(activity, type_id, cfg, cost_indices)

    base_material_mult, base_time_mult = base.material_multiplier, base.time_multiplier
    if activity == "Tech I":
        owned = _owned_bpo_mods(blueprint_id)
        if owned is not None:
            base_material_mult, base_time_mult = owned

    return base_material_mult * me_mult, base_time_mult * te_mult, job_cost_rate


def _tech_ii_mods(type_id: int, blueprint_id: int, activity_id: int, cfg: ProductionConfig,
                   home: dict, jita: dict, selected_decryptors: dict[int, str],
                   memo: dict[int, tuple[float, float, Optional[str]]]) -> tuple[float, float, Optional[str]]:
    """Returns (material_multiplier, time_multiplier, decryptor_name) for a
    Tech II *or* Tech III item: the resulting BPC's own ME/TE (per the module
    docstring), with your structure/rig bonus applied on top - same as every
    other activity. Tech III hulls/subsystems use the exact same activity_id=8
    Invention as Tech II (CCP removed the old relic-based "Reverse
    Engineering" mechanic years ago - see classify_activity's docstring), so
    for the overwhelming majority of Tech III items `t1_blueprint_id` below
    resolves normally and this function's automatic "best decryptor" cost
    optimization applies exactly as it does for Tech II - no special-casing
    needed. The `elif override and override in DECRYPTORS` branch below is
    only a defensive fallback for the rare case where no invention recipe can
    be found at all (e.g. missing/stale SDE data) but the user has still
    manually picked a decryptor for the item (storage.selected_decryptors) -
    that decryptor's ME/TE still applies rather than silently ignoring it.
    Falls back further to the flat ACTIVITY_MODS["Tech II"] entry
    (decryptor_name=None) if neither an invention recipe nor a manual
    decryptor override exists."""
    if type_id in memo:
        return memo[type_id]

    structure_profile = _structure_profile("Tech II", type_id)
    structure_type, rig_tier = _structure_rig(structure_profile, cfg)
    security_multiplier = _security_multiplier_for(structure_profile, cfg)
    _, me_mult, te_mult = structure_rig_multiplier(structure_type, rig_tier, security_multiplier)
    fallback = ACTIVITY_MODS["Tech II"]
    override = selected_decryptors.get(type_id)
    t1_blueprint_id = storage.find_invention_recipe_by_product_type_id(blueprint_id)

    chosen = None
    if t1_blueprint_id is not None:
        reducible_cost = invention.reducible_material_cost(blueprint_id, activity_id, home, jita, cfg)
        try:
            if override:
                chosen = invention.estimate(t1_blueprint_id, override, home, jita, cfg, reducible_cost)
            else:
                chosen = invention.best_decryptor_for_item(t1_blueprint_id, home, jita, cfg, reducible_cost)
        except ValueError:
            chosen = None
    elif override and override in DECRYPTORS:
        # No invention recipe found (rare - see docstring), but the
        # manually-selected decryptor's ME/TE still applies directly.
        decryptor = DECRYPTORS[override]
        result = ((1 - decryptor.me_bonus / 100) * me_mult, (1 - decryptor.te_bonus / 100) * te_mult, override)
        memo[type_id] = result
        return result

    if chosen is None:
        result = (fallback.material_multiplier * me_mult, fallback.time_multiplier * te_mult, None)
    else:
        result = ((1 - chosen.me / 100) * me_mult, (1 - chosen.te / 100) * te_mult, chosen.decryptor)
    memo[type_id] = result
    return result


def _unit_cost(type_id: int, cfg: ProductionConfig, home: dict, jita: dict,
               memo: dict[int, Optional[float]], selected_decryptors: dict[int, str],
               t2_memo: dict[int, tuple[float, float, Optional[str]]], cost_indices: CostIndices,
               adjusted_prices: dict[int, float], depth: int = 0) -> Optional[float]:
    """Pure (no side effects) recursive best-of-buy-or-build unit cost estimate,
    used only to decide whether building beats buying. Memoized per type_id.

    job_cost (the facility/installation fee) is *not* a percentage markup on
    the real material cost - real EVE computes it as EIV x job_cost_rate,
    where EIV ("Estimated Item Value") is the sum of each material's *base*
    (ME 0, unreduced) quantity times its ESI-published adjusted_price (a
    CCP-regulated valuation, decoupled from live market prices) - confirmed
    via community reverse-engineering (forums.eveonline.com/t/118718), since
    CCP has never published this formula directly. material_cost (what you
    actually spend to source materials) still correctly uses the ME-reduced
    quantity and real market price - EIV is a separate, parallel calculation
    solely to price the facility fee."""
    if type_id in memo:
        return memo[type_id]
    memo[type_id] = None  # break cycles defensively; refined below once computed
    volume = _haul_volume(type_id, cfg)
    buy = pricing.buy_price(type_id, home, jita, volume, cfg)

    if depth >= MAX_DEPTH:
        memo[type_id] = buy
        return buy

    activity, bp = classify_activity(type_id)
    if bp is None:
        memo[type_id] = buy
        return buy

    blueprint_id, activity_id, product_qty = bp
    if activity == "Tech II":
        material_mult, _, _ = _tech_ii_mods(type_id, blueprint_id, activity_id, cfg, home, jita,
                                             selected_decryptors, t2_memo)
        job_cost_rate = _job_cost_rate("Tech II", type_id, cfg, cost_indices)
    else:
        material_mult, _, job_cost_rate = _activity_mods(activity, type_id, cfg, cost_indices, blueprint_id)
    materials = storage.get_blueprint_materials(blueprint_id, activity_id)

    material_cost = 0.0
    eiv = 0.0
    for material_id, base_qty in materials:
        m_cost = _unit_cost(material_id, cfg, home, jita, memo, selected_decryptors, t2_memo,
                             cost_indices, adjusted_prices, depth + 1)
        if m_cost is None:
            memo[type_id] = buy
            return buy
        material_cost += _material_qty(base_qty, material_mult, 1) * m_cost
        eiv += base_qty * adjusted_prices.get(material_id, 0.0)

    job_cost = eiv * job_cost_rate
    # GitHub issue #40: some items can only be built from a blueprint *copy*
    # that must be bought outright (never owned as a BPO, not inventable) -
    # its purchase cost, amortized over however many runs it came with, is a
    # real per-run cost of building this item, on top of materials/job fee.
    bpc_cost_per_run = storage.get_manual_blueprint_copy_cost_per_run(type_id)
    bpc_cost_per_unit = (bpc_cost_per_run / product_qty) if bpc_cost_per_run is not None else 0.0
    build_cost = (material_cost + job_cost) / product_qty + bpc_cost_per_unit
    best = build_cost if buy is None else min(buy, build_cost)
    memo[type_id] = best
    return best


def unit_cost_detail(type_id: int, cfg: ProductionConfig, home: dict, jita: dict,
                      memo: dict[int, Optional[float]], selected_decryptors: dict[int, str],
                      t2_memo: dict[int, tuple[float, float, Optional[str]]], cost_indices: CostIndices,
                      adjusted_prices: dict[int, float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Like _unit_cost, but for callers (Doctrine's Shopping List) that need
    Buy vs Build shown side by side, not just the cheaper of the two -
    returns (best, build_cost, buy_price) instead of just `best`. Recurses
    into _unit_cost (not itself) for sub-materials, same as _unit_cost's own
    recursion - sub-material sourcing still picks whichever of buy/build is
    cheaper at each level, only this top-level type_id's own build_cost/
    buy_price are exposed separately here. Deliberately duplicates _unit_
    cost's own top-level formula rather than modifying it - this is only
    ever called once per top-level item (not recursively on itself), so the
    duplication is small and isolated, and _unit_cost's own recursive
    machinery (and its existing tests) stay completely unchanged."""
    volume = _haul_volume(type_id, cfg)
    buy = pricing.buy_price(type_id, home, jita, volume, cfg)
    activity, bp = classify_activity(type_id)
    if bp is None:
        return buy, None, buy
    blueprint_id, activity_id, product_qty = bp
    if activity == "Tech II":
        material_mult, _, _ = _tech_ii_mods(type_id, blueprint_id, activity_id, cfg, home, jita,
                                             selected_decryptors, t2_memo)
        job_cost_rate = _job_cost_rate("Tech II", type_id, cfg, cost_indices)
    else:
        material_mult, _, job_cost_rate = _activity_mods(activity, type_id, cfg, cost_indices, blueprint_id)
    materials = storage.get_blueprint_materials(blueprint_id, activity_id)
    material_cost = 0.0
    eiv = 0.0
    for material_id, base_qty in materials:
        m_cost = _unit_cost(material_id, cfg, home, jita, memo, selected_decryptors, t2_memo,
                             cost_indices, adjusted_prices, depth=1)
        if m_cost is None:
            return buy, None, buy
        material_cost += _material_qty(base_qty, material_mult, 1) * m_cost
        eiv += base_qty * adjusted_prices.get(material_id, 0.0)
    job_cost = eiv * job_cost_rate
    # GitHub issue #40 - see _unit_cost's own comment on this same line.
    bpc_cost_per_run = storage.get_manual_blueprint_copy_cost_per_run(type_id)
    bpc_cost_per_unit = (bpc_cost_per_run / product_qty) if bpc_cost_per_run is not None else 0.0
    build_cost = (material_cost + job_cost) / product_qty + bpc_cost_per_unit
    best = build_cost if buy is None else min(buy, build_cost)
    return best, build_cost, buy


def _sell_margin(sell_price: float, build_cost: Optional[float]) -> Optional[float]:
    """Shared core of margin_home/margin_jita - (sell_price - build_cost) /
    build_cost, or None if build_cost isn't a real positive cost (gate not
    applied, falls back to the plain cost comparison - a temporary
    market-data gap shouldn't block every build outright)."""
    if build_cost is None or build_cost <= 0:
        return None
    return (sell_price - build_cost) / build_cost


def margin_home(type_id: int, build_cost: Optional[float], home: dict, cfg: ProductionConfig) -> Optional[float]:
    """Margin if you built and sold one unit of `type_id` at the C-J (home)
    sell quote right now, net of cfg.market_fees - its real opportunity-cost
    value even if it's actually consumed internally rather than resold.
    Returns None if there's no home sell quote to check against."""
    home_quote = home.get(type_id)
    if not home_quote or home_quote.sell <= 0:
        return None
    return _sell_margin(home_quote.sell * (1 - cfg.market_fees), build_cost)


def margin_jita(type_id: int, build_cost: Optional[float], jita: dict, cfg: ProductionConfig) -> Optional[float]:
    """Margin if you built and shipped one unit of `type_id` to Jita and sold
    it there right now, net of cfg.market_fees and export haul cost (the same
    haul_cost_per_m3 rate used for the reverse Jita->C-J import haul, just
    subtracted instead of added). Returns None if there's no Jita sell quote
    to check against."""
    jita_quote = jita.get(type_id)
    if not jita_quote or jita_quote.sell <= 0:
        return None
    export_cost = cfg.haul_cost_per_m3 * (_haul_volume(type_id, cfg) or 0)
    return _sell_margin(jita_quote.sell * (1 - cfg.market_fees) - export_cost, build_cost)


def _build_margin(type_id: int, build_cost: Optional[float], jita_target: Optional[float],
                   home: dict, jita: dict, cfg: ProductionConfig) -> Optional[float]:
    """Margin if you built and sold one unit of `type_id` right now - gates
    whether a *stock target* is worth building at all, separate from
    _expand's plain build-cheaper-than-buy sourcing comparison (that's about
    minimizing cost for something you're building anyway; this is "should you
    even bother"). A stock target with a Jita-market target (sell there) uses
    margin_jita; everything else (home-market target or pure backup/component
    stock with no direct resale) uses margin_home - see those functions' own
    docstrings for the actual pricing. Thin dispatcher, kept for
    plan_production/discover_build_candidates (both single-margin, gated by
    whether *this specific stock target* has a jita_target) - the new
    Margin page (engine.discover_ship_margins/actions.do_get_item_margin)
    computes both unconditionally instead of picking one via this gate."""
    if jita_target and jita_target > 0:
        return margin_jita(type_id, build_cost, jita, cfg)
    return margin_home(type_id, build_cost, home, cfg)


def _buy_or_build_decision(type_id: int, cfg: ProductionConfig, home: dict, jita: dict,
                            manual_overrides: dict[int, str], cost_memo: dict[int, Optional[float]],
                            bp: Optional[tuple[int, int, float]], depth: int = 0) -> str:
    """Whether to source `type_id` by buying or building it: a manual override
    (storage.manual_build_buy) if set, else whichever the modeled unit cost
    (cost_memo, populated by _unit_cost) says is cheaper - "Buy" if there's no
    blueprint or MAX_DEPTH was hit. Factored out of _expand so asset_plan's
    per-level resolution makes the exact same call (differing only in *how
    much* needs sourcing, not *whether* to build it) - the two Baulisten
    should never disagree on build-vs-buy for the same item."""
    override = manual_overrides.get(type_id)
    if override is not None:
        return override
    if bp is None or depth >= MAX_DEPTH:
        return "Buy"
    volume = _haul_volume(type_id, cfg)
    buy = pricing.buy_price(type_id, home, jita, volume, cfg)
    build_cost = cost_memo.get(type_id)
    return "Build" if (build_cost is not None and (buy is None or build_cost < buy)) else "Buy"


def _base_runs(cfg: ProductionConfig, home: dict, jita: dict, manual_overrides: dict[int, str],
                cost_memo: dict[int, Optional[float]], selected_decryptors: dict[int, str],
                t2_memo: dict[int, tuple[float, float, Optional[str]]], cost_indices: CostIndices,
                adjusted_prices: dict[int, float], stock_targets: list[tuple]) -> dict[int, float]:
    """Pure structural run count per type_id, completely ignoring current
    stock and never buffered by overbuild: if every stock target's full
    backup+home+Jita total had to be built from absolute zero, how many runs
    of each item would that take. Used only as a *stable* sizing baseline for
    _expand_all's overbuild buffer - it must not shrink just because some
    stock happens to be on hand right now, and it must not itself compound
    level over level (see _expand_all's docstring for why a naive
    multiplicative buffer would)."""
    base_runs: dict[int, float] = {}

    def expand(type_id: int, quantity: float, depth: int = 0) -> None:
        """Recurses down the BOM, accumulating each type_id's total run
        count into the enclosing `base_runs` (pooled across every branch
        that needs it, same "pool demand, don't double count" shape
        _expand_all uses for the real plan) - stops at a bought leaf, a
        manual Buy override, or MAX_DEPTH."""
        if quantity <= 0 or depth >= MAX_DEPTH:
            return
        activity, bp = classify_activity(type_id)
        _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors, t2_memo, cost_indices, adjusted_prices)
        decision = _buy_or_build_decision(type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth)
        if decision == "Buy" or bp is None:
            return
        blueprint_id, activity_id, product_qty = bp
        runs = math.ceil(quantity / product_qty)
        base_runs[type_id] = base_runs.get(type_id, 0.0) + runs
        if activity == "Tech II":
            material_mult, _, _ = _tech_ii_mods(type_id, blueprint_id, activity_id, cfg, home, jita,
                                                 selected_decryptors, t2_memo)
        else:
            material_mult, _, _ = _activity_mods(activity, type_id, cfg, {}, blueprint_id)
        for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
            expand(material_id, _material_qty(base_qty, material_mult, runs), depth + 1)

    for type_id, type_name, backup_stock, home_market_stock, jita_market_stock in stock_targets:
        demand = backup_stock + (home_market_stock or 0.0) + (jita_market_stock or 0.0)
        expand(type_id, demand)
    return base_runs


def _parent_base_runs_for_buffer(type_id: int, base_runs: dict[int, float], buffered_parents: set[int]) -> float:
    """The overbuild-buffer baseline to use for `type_id`'s materials *this*
    round: base_runs[type_id] (its whole-tree, stock-oblivious aggregate run
    count - see _base_runs) the *first* time type_id is processed as a
    parent across the whole call, 0.0 every later time. Mutates
    `buffered_parents` as a side effect (marks type_id buffered).

    Prevents exactly the double-counting bug _expand_all's own docstring
    describes: a widely-shared component can legitimately reappear as a
    parent in a *later* round (reached at a different BOM depth from a
    different stock target) - without this guard, that reappearance would
    read the same whole-tree aggregate fresh again and add a second full
    buffer on top of the first.

    Shared by _expand_all and plan_asset_optimized's Phase A specifically so
    the two Baulisten can never disagree on how much overbuild buffer a
    given parent contributes - confirmed real gap (2026-08-01): Asset-
    Optimized used to apply *no* buffer at all (bare gross demand only),
    which only became visible once stock was counted correctly everywhere -
    it showed far fewer Reaction jobs than the regular Bauliste for the same
    underlying data, because the regular one was still trying to build up
    cfg.component_overbuild's stock cushion and the asset-optimized one
    wasn't accounting for that goal at all."""
    if type_id in buffered_parents:
        return 0.0
    buffered_parents.add(type_id)
    return base_runs.get(type_id, 0.0)


def _expand_all(seed_missing: dict[int, float], cfg: ProductionConfig, home: dict, jita: dict,
                 manual_overrides: dict[int, str], cost_memo: dict[int, Optional[float]],
                 selected_decryptors: dict[int, str], t2_memo: dict[int, tuple[float, float, Optional[str]]],
                 manual_stock: dict[int, float], stock_used: dict[int, float],
                 base_runs: dict[int, float],
                 gross_demand: Optional[dict[int, float]] = None,
                 ) -> tuple[dict[int, float], dict[tuple[int, int, int], int]]:
    """Resolves every stock target's `seed_missing` quantity into buy_totals
    ({type_id: qty}) and build_runs ({(blueprint_id, activity_id, product_type_id): runs}),
    using the buy-vs-build decisions already computed in cost_memo.

    `gross_demand`, if given, is mutated in place with each material's total
    *pre-stock-netting* pooled demand (this round's `target`, before
    `available`/`consumed` are subtracted), accumulated across every round -
    same shared-across-rounds accumulation shape as `buy_totals`/`build_runs`
    themselves. Used by plan_production to show what fraction of an item's
    total demand is already covered by stock (Buy List "on hand %" column) -
    seed it with each top-level stock target's own gross backup+home+Jita
    target before calling this, so a type_id that's *both* a stock target
    and a shared material downstream gets one correctly-combined total, not
    two disconnected numbers.

    Processed breadth-first, level by level (one dict of pooled per-type_id
    quantities per round) rather than depth-first per stock target/edge -
    this is required, not just a style choice: a shared material (e.g. a
    reaction input feeding a dozen different Tech II components) must be
    decided on and expanded *once*, using its *total* pooled demand, not once
    per parent edge that happens to reach it. An earlier depth-first version
    of this function got that wrong in a way that only shows up with a
    widely-shared material: it read base_runs[type_id] - `type_id`'s
    *whole-tree* aggregate base run count (from the separate, stock-oblivious
    _base_runs pass, see its docstring) - to size the overbuild buffer, but
    did so *inside* the per-edge material loop, so a material reached via N
    different parent edges got that same whole-tree buffer counted N times
    over instead of once. Pooling first (jobs_this_level) means base_runs is
    only ever read once per type_id per round.

    Each material's target = gross pooled demand (base_qty x material_mult x
    runs, summed across every parent claiming it this round) + an overbuild
    buffer (component_overbuild x base_qty x material_mult x base_runs[parent],
    summed the same way, deliberately from parents' *base* runs rather than
    their final total runs) - this avoids what a naive
    target = gross_demand * (1 + overbuild) would do: since gross_demand
    already carries the previous level's buffer, multiplying it *again* by
    (1 + overbuild) would compound the buffer exponentially with recursion
    depth ((1+overbuild)^depth) - a capital-ship BOM 4 levels deep would then
    get an 8x+ buffer instead of a flat 70%. Skipped (overbuild=0) for a
    material with a manual Build/Buy override - a manually forced Buy needs
    no build-chain buffer at all, since it's never actually built.

    `buffered_parents` guards the *other* half of that same double-counting
    risk: pooling within a round (see above) only dedupes N parent *edges*
    reaching a material in the *same* round - a widely-shared component (e.g.
    a common Tech I part several different Tech II stock targets all need,
    at different tree depths) can still legitimately show up as a parent
    again in a *later* round once its own upstream demand has been pooled
    and resolved. Without this guard, that reappearance would read
    base_runs[type_id] - the *whole-tree* aggregate - fresh again and add a
    second full buffer on top of the first (confirmed real bug: reproduced
    with a 2-stock-target/shared-component BOM where a raw material ended up
    ~20% too high from exactly one duplicate buffer application; with
    hundreds of real stock targets sharing common minerals/components across
    many depths, this compounds far worse - the reported case landed in the
    billions of units for a base mineral). Each type_id's buffer is
    contributed at most once, the *first* round it's processed as a parent -
    every later round still pools its (correctly cascading) gross demand,
    just with buffer=0.

    `stock_used` is a running ledger (seeded by the caller for
    top-level stock targets, mutated here for everything below them) so a
    component needed by two different branches doesn't have the same
    physical stock counted against both of them."""
    buy_totals: dict[int, float] = {}
    build_runs: dict[tuple[int, int, int], int] = {}
    buffered_parents: set[int] = set()

    jobs_this_level: dict[int, float] = {tid: q for tid, q in seed_missing.items() if q > 0}
    depth = 0
    while jobs_this_level and depth < MAX_DEPTH:
        next_level: dict[int, float] = {}

        for type_id, quantity in jobs_this_level.items():
            activity, bp = classify_activity(type_id)
            decision = _buy_or_build_decision(type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth)
            if decision == "Buy" or bp is None:
                buy_totals[type_id] = buy_totals.get(type_id, 0) + quantity
                continue

            blueprint_id, activity_id, product_qty = bp
            runs = math.ceil(quantity / product_qty)
            key = (blueprint_id, activity_id, type_id)
            build_runs[key] = build_runs.get(key, 0) + runs

            if activity == "Tech II":
                material_mult, _, _ = _tech_ii_mods(type_id, blueprint_id, activity_id, cfg, home, jita,
                                                     selected_decryptors, t2_memo)
            else:
                # job_cost_rate isn't needed here (only material_mult drives sub-material
                # quantities) - {} makes _activity_mods fall back cleanly without a live lookup.
                material_mult, _, _ = _activity_mods(activity, type_id, cfg, {}, blueprint_id)
            parent_base_runs = _parent_base_runs_for_buffer(type_id, base_runs, buffered_parents)
            for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
                gross_needed = _material_qty(base_qty, material_mult, runs)
                if gross_needed <= 0:
                    continue
                overbuild = 0.0 if material_id in manual_overrides else cfg.component_overbuild
                buffer = overbuild * base_qty * material_mult * parent_base_runs
                next_level[material_id] = next_level.get(material_id, 0.0) + gross_needed + buffer

        jobs_this_level = {}
        for material_id, target in next_level.items():
            if target <= 0:
                continue
            if gross_demand is not None:
                gross_demand[material_id] = gross_demand.get(material_id, 0.0) + target
            _, m_bp = classify_activity(material_id)
            available = max(0.0, _current_stock(material_id, manual_stock, cfg, m_bp) - stock_used.get(material_id, 0.0))
            consumed = min(target, available)
            stock_used[material_id] = stock_used.get(material_id, 0.0) + consumed
            net_needed = target - consumed
            if net_needed > 0:
                jobs_this_level[material_id] = jobs_this_level.get(material_id, 0.0) + net_needed

        depth += 1

    return buy_totals, build_runs


def invalidate_production_locations_cache() -> None:
    """No-op kept for callers still wired up to invalidate a per-location
    cache (Logistik tab category-location changes, home_location_id Settings
    changes) - _current_stock no longer caches a curated location list at
    all (see its docstring for why), so there's nothing left to invalidate.
    Safe/cheap to keep calling from those call sites rather than ripping the
    invalidation calls out too."""


def _current_stock(type_id: int, manual_stock: dict[int, float], cfg: ProductionConfig,
                    bp: Optional[tuple[int, int, float]]) -> float:
    """manual entry + ESI assets at *every* location (location_id=None -
    corp/character-wide, not filtered to cfg.home_location_id or any other
    single/curated structure) + industry jobs already in progress (their
    eventual output counts as "as good as in stock").

    Deliberately corp-wide rather than a specific structure or a maintained
    location list: an earlier version of this checked only
    cfg.home_location_id (missed 8 other production structures' worth of
    stock entirely), then a curated "configured production locations" set
    (home + Logistik tab's job_category_locations + Jita) - still wrong:
    confirmed live (2026-08-01) that a real 1,572,335-unit Silicates stash
    sat at a structure that was in *neither* the active category-location
    list *nor* its saved quick-switch options, invisible either way. Any
    maintained whitelist can silently miss a real structure the same way.
    "Do I own this material anywhere" is inherently a corp-wide question,
    not a location-scoped one - storage.esi_stock_at_location(type_id, None)
    already exists exactly for this (its own docstring: "None = all
    locations"), and already excludes NON_STOCK_LOCATION_FLAGS (AssetSafety/
    Deliveries/CorpMarket/...) regardless of location, so this doesn't trade
    away that filtering to get the wider scope."""
    total = manual_stock.get(type_id, 0)
    total += storage.esi_stock_at_location(type_id, None)
    incoming = storage.esi_incoming_industry_qty(type_id)
    if incoming["runs"] and bp is not None:
        _, _, product_qty = bp
        total += incoming["runs"] * product_qty
    return total


def _stock_on_hand(type_id: int, manual_stock: dict[int, float], cfg: ProductionConfig) -> float:
    """Physically-existing stock right now: manual entry + ESI assets, same
    scope as _current_stock above, but *without* its "industry jobs already
    in progress count as good as in stock" addition - an active/paused job's
    eventual output isn't real inventory yet, so it can't be handed to a
    *different* job you're trying to actually start in EVE right now.

    Confirmed real bug (2026-08-15, reported after the user physically tried
    to queue jobs off the Asset-Optimized list): plan_asset_optimized's
    readiness split (_allocate_scarce_stock/min_fraction/runs_ready_now) used
    to reuse the same incoming-inclusive _current_stock as its "available"
    pool - when several jobs shared a scarce material with a batch already
    cooking (e.g. Sylramic Fibers), every one of them could show as "Ready
    Now" against that same not-yet-delivered batch, so the Bauliste claimed
    more was startable right now than physically existed. _current_stock
    itself is *not* wrong for its other callers - job/shortfall *sizing*
    (how many total runs to plan for) legitimately should net against
    incoming production, so as not to recommend building a duplicate batch
    of something already queued. Only the "can I click start right now"
    readiness signal needs this on-hand-only number instead - see
    plan_asset_optimized's Phase B for the split ledger this requires
    (stock_used vs. stock_used_on_hand)."""
    return manual_stock.get(type_id, 0) + storage.esi_stock_at_location(type_id, None)


def _total_missing(type_id: int, backup_stock: float, home_market_stock: Optional[float],
                    jita_market_stock: Optional[float], current_stock: float, cfg: ProductionConfig) -> float:
    """Total shortfall to build/buy for a stock target: backup_stock (an
    internal reserve, netted against owned current_stock - _current_stock)
    *plus* whatever's still short of the home/Jita market-*listing* targets,
    each netted against what's actually listed for sale there right now
    (open sell order volume - see market_status()/storage.sell_order_qty_*)
    *and* against whatever's left of current_stock once the backup reserve
    is covered. These are three pools with one shared physical source (your
    actual owned units): a backup-stock unit was never meant to be listed,
    and a unit already listed for sale doesn't count toward backup, but
    owned stock beyond what backup needs is exactly the stock you'd list to
    cover a market target instead of buying/building more of it - real bug,
    confirmed via a live example (Sentinel: backup_stock=0, current_stock=20,
    home/jita targets=20 each): the 20 owned-but-unlisted hulls were being
    fully discarded against the (already-zero) backup shortfall instead of
    covering 20 of the 40 units of market-target demand, so the Buy List
    recommended buying/building units already sitting in the hangar.
    Allocated backup-first (whatever's covering the reserve isn't available
    to double as market stock), then home, then Jita, in that order -
    arbitrary between home/Jita since both draw from the same leftover pool,
    but backup must go first since it's the one pool a listed/sold unit
    can't retroactively refill.

    Previously only backup_stock fed the Buy/Build engine (market-listing
    targets were market_status()-only, display-only) - a stock target with
    backup_stock=0 but a market target would show zero demand even though it
    still needs to be built.

    Deliberate design choice, unrelated to the fix above: this function
    always counts the full home/Jita target regardless of margin - the
    *reported* shortfall (this return value, shown on InventoryRow) is
    always the real one, so an unprofitable item still shows real demand
    rather than silently zero. Whether that shortfall actually turns into a
    Buy List/Bauliste entry is a separate decision made by the caller
    (plan_production/plan_asset_optimized skip a stock target's demand
    entirely - build *and* buy - once its build margin is below
    cfg.min_margin, rather than falling back to buying it; confirmed with
    the user 2026-08-01: an item not worth building isn't assumed worth
    buying either, since both compete on the same C-J sell price)."""
    missing = max(0.0, backup_stock - current_stock)
    surplus_stock = max(0.0, current_stock - backup_stock)  # owned units left over once the backup reserve is covered
    if home_market_stock:
        home_listed = (
            storage.sell_order_qty_at_location(type_id, cfg.home_location_id)
            if cfg.home_location_id is not None else 0.0
        )
        home_short = max(0.0, home_market_stock - home_listed)
        applied = min(surplus_stock, home_short)
        missing += home_short - applied
        surplus_stock -= applied
    if jita_market_stock:
        jita_listed = storage.sell_order_qty_in_region(type_id, TRADING_CONFIG.jita_region_id)
        jita_short = max(0.0, jita_market_stock - jita_listed)
        applied = min(surplus_stock, jita_short)
        missing += jita_short - applied
        surplus_stock -= applied
    return missing


def _haul_volume(type_id: int, cfg: ProductionConfig) -> Optional[float]:
    """Volume to use for haul-cost math (cfg.haul_cost_per_m3 x this) - the
    *packaged* volume for ships and capital-sized modules (GitHub issue #11:
    e.g. Capital Shield Booster I lists volume=4000 in the SDE but
    packaged_volume=1000), which can be drastically smaller than the flight/
    assembled volume in sde_types.volume.

    Thin wrapper around esi_client.resolve_effective_volume - GitHub issue
    #73 moved the actual lookup+cache logic there so Trading's
    candidate_discovery could share the identical implementation instead of
    keeping its own copy that could drift out of sync; see that function's
    own docstring for the full ESI-lookup/cache/fallback behavior."""
    sde_type = storage.get_sde_type(type_id)
    if sde_type is None:
        return None
    from ..esi_client import resolve_effective_volume  # local import: keeps this a rare, lazy call
    return resolve_effective_volume(type_id, sde_type[3])


class _PlanContext:
    """Shared setup both plan_production and plan_asset_optimized need: live
    home/Jita prices, system cost indices, ESI adjusted prices, and the raw
    stock-target/manual-override/decryptor config - factored out so the two
    Baulisten can't drift apart on *how* they price or classify items, only
    on how they size jobs against available stock."""
    def __init__(self, cfg: ProductionConfig):
        from ..esi_client import ESIClient

        self.stock_targets = storage.load_stock_targets()
        self.manual_stock = storage.load_manual_stock()
        self.manual_overrides = storage.load_manual_build_buy()
        self.selected_decryptors = storage.load_selected_decryptors()

        # Bounded, price-agnostic universe to price - see pricing.home_prices/
        # jita_prices' own docstrings for why this can't just be "everything"
        # now that both are ESI-first (Jita has no bulk-region endpoint).
        priced_type_ids = list(_structural_material_closure(t[0] for t in self.stock_targets))
        self.home = pricing.home_prices(cfg, priced_type_ids)
        self.jita = pricing.jita_prices(priced_type_ids)

        esi_client = ESIClient()
        self.cost_indices: CostIndices = {
            "component": pricing.system_cost_indices_for(esi_client, cfg.component_system_id),
            "manufacturing": pricing.system_cost_indices_for(esi_client, cfg.manufacturing_system_id),
        }
        # GitHub issue #12: also fetch cost indices for whichever solar
        # systems the user's own Logistik (per-category structure)
        # assignments actually resolve to - _job_cost_rate prefers these
        # "category:<name>" entries over the flat component/manufacturing
        # split above whenever the specific category being built has one.
        # One ESI call per *distinct* system (categories sharing a system
        # share the fetch, via system_indices_by_id's cache-by-system-id).
        system_indices_by_id: dict[int, dict[str, float]] = {}
        for category, system_id in storage.load_category_system_ids().items():
            if system_id not in system_indices_by_id:
                system_indices_by_id[system_id] = pricing.system_cost_indices_for(esi_client, system_id)
            self.cost_indices[f"category:{category}"] = system_indices_by_id[system_id]
        try:
            self.adjusted_prices = esi_client.get_adjusted_prices()
        except Exception:  # noqa: BLE001 - best-effort; falls back to 0 (job_cost=0), not a guess
            self.adjusted_prices = {}


@storage.with_batch_session()
def plan_production(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Runs the full Stock Targets -> Inventory -> Buy/Build pipeline. Returns
    dict with 'inventory' (InventoryRow list), 'buy_list' (BuyListEntry list,
    sorted by total price desc), 'build_list' (BuildJobEntry list), and
    'invention_list' (InventionNeedRow list, sorted by recommended invention
    runs desc - one row per *configured Tech II stock target* that's actually
    invention-sourced (has a decryptor), regardless of whether it's currently
    missing or would be bought instead of built right now - runs_needed/
    bpcs_needed/recommended_invention_runs are 0 for a target that's already
    fully stocked)."""
    ctx = _PlanContext(cfg)
    stock_targets, manual_stock, manual_overrides, selected_decryptors = (
        ctx.stock_targets, ctx.manual_stock, ctx.manual_overrides, ctx.selected_decryptors)
    home, jita, cost_indices, adjusted_prices = ctx.home, ctx.jita, ctx.cost_indices, ctx.adjusted_prices

    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, tuple[float, float, Optional[str]]] = {}
    inventory: list[InventoryRow] = []
    invention_list: list[InventionNeedRow] = []
    # Ledger of how much of each type_id's *own* current stock has already
    # been counted toward some demand this run - threaded through _expand_all
    # (see its docstring) so a component needed by two different branches
    # doesn't have the same physical stock counted against both. Also seeded
    # below for top-level stock targets (their own missing-vs-target math
    # doesn't go through _expand_all, but they can *also* be a shared
    # material elsewhere in the tree).
    stock_used: dict[int, float] = {}
    # Stock-oblivious sizing baseline for _expand_all's overbuild buffer (see
    # _base_runs/_expand_all docstrings) - computed once, up front, over
    # *every* stock target's full backup+home+Jita total, not just what's
    # currently missing (the buffer must reflect the steady-state target, not
    # today's snapshot).
    base_runs = _base_runs(cfg, home, jita, manual_overrides, cost_memo, selected_decryptors, t2_memo,
                            cost_indices, adjusted_prices, stock_targets)
    seed_missing: dict[int, float] = {}
    # {type_id: total pre-stock/pre-listing target}, for the Buy List's "on
    # hand %" column (see _expand_all's gross_demand param) - seeded here
    # with each top-level stock target's own raw backup+home+Jita target
    # (missing's ancestor, before _total_missing's netting), since
    # _expand_all only tracks this for materials reached via recursion, not
    # for the initial seed_missing entries themselves.
    gross_demand: dict[int, float] = {}

    for type_id, type_name, backup_stock, home_market_stock, jita_market_stock in stock_targets:
        activity, bp = classify_activity(type_id)
        current_stock = _current_stock(type_id, manual_stock, cfg, bp)
        missing = _total_missing(type_id, backup_stock, home_market_stock, jita_market_stock, current_stock, cfg)
        stock_used[type_id] = stock_used.get(type_id, 0.0) + min(current_stock, backup_stock)
        inventory.append(InventoryRow(
            type_id=type_id, type_name=type_name, activity=activity,
            backup_stock=backup_stock, current_stock=current_stock, total_missing=missing,
        ))

        # Every configured Tech II stock target gets an invention-needs row,
        # independent of whether it's currently missing or whether the
        # buy-vs-build gate below would pick "Buy" for it right now - the
        # point of this list is forward planning ("what would it take to
        # invent enough BPCs for this stock target"), not just today's
        # Bauliste (see plan_production docstring).
        if activity == "Tech II" and bp is not None:
            blueprint_id, activity_id, product_qty = bp
            _, _, decryptor_name = _tech_ii_mods(type_id, blueprint_id, activity_id, cfg, home, jita,
                                                  selected_decryptors, t2_memo)
            if decryptor_name is not None:
                t1_blueprint_id = storage.find_invention_recipe_by_product_type_id(blueprint_id)
                if t1_blueprint_id is not None:
                    reducible_cost = invention.reducible_material_cost(blueprint_id, activity_id, home, jita, cfg)
                    try:
                        chosen = invention.estimate(t1_blueprint_id, decryptor_name, home, jita, cfg, reducible_cost)
                    except ValueError:
                        chosen = None
                    if chosen is not None and chosen.output_runs > 0 and chosen.probability > 0:
                        runs_needed = math.ceil(missing / product_qty) if missing > 0 else 0
                        bpcs_needed = math.ceil(runs_needed / chosen.output_runs) if runs_needed > 0 else 0
                        recommended_runs = math.ceil(bpcs_needed / chosen.probability) if bpcs_needed > 0 else 0
                        # blueprint_id here IS the invented T2 blueprint's own
                        # type_id (find_invention_recipe_by_product_type_id's
                        # own param name calls it product_blueprint_type_id) -
                        # not `type_id` above, which is the manufactured item.
                        t2_bpc_owned = int(storage.available_blueprint_copies(blueprint_id, None))
                        stockpile_pct = (max(0.0, min(100.0, current_stock / backup_stock * 100))
                                         if backup_stock > 0 else 0.0)
                        invention_list.append(InventionNeedRow(
                            type_id=type_id, type_name=type_name,
                            t1_blueprint_type_id=t1_blueprint_id, t1_blueprint_name=chosen.t1_blueprint_name,
                            decryptor=decryptor_name, probability=chosen.probability, output_runs=chosen.output_runs,
                            runs_needed=runs_needed, bpcs_needed=bpcs_needed,
                            recommended_invention_runs=recommended_runs,
                            t2_bpc_owned=t2_bpc_owned, stockpile_pct=stockpile_pct,
                        ))

        if missing > 0:
            _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors, t2_memo, cost_indices, adjusted_prices)

            # Gate: a stock target's demand only feeds the Bauliste at all if
            # building it clears cfg.min_margin - confirmed with the user
            # (2026-08-01): if building isn't profitable, buying isn't
            # assumed to be either (both paths ultimately compete on the
            # same C-J sell price), so an unprofitable item's demand is
            # dropped entirely rather than falling back to "buy it anyway" -
            # it simply isn't fulfilled until margin recovers. This used to
            # force a "Buy" override instead (margin_forced_buy) - real bug
            # reported live: Ishtar/Golem (margin 2.5%/10.7%, both below the
            # 15% default) showed up as Buy List entries costing ~10B ISK
            # each purely because building wasn't profitable enough, which
            # made no sense to the user ("wenn es sich nicht lohnt sie zu
            # bauen, wieso sollte ich sie kaufen?"). total_missing (above,
            # InventoryRow) still reports the real shortfall regardless of
            # margin - this gate only controls whether that shortfall turns
            # into an actual buy/build recommendation.
            skip_due_to_margin = False
            if bp is not None and type_id not in manual_overrides:
                margin = _build_margin(type_id, cost_memo.get(type_id), jita_market_stock, home, jita, cfg)
                if margin is not None and margin < cfg.min_margin:
                    skip_due_to_margin = True

            if not skip_due_to_margin:
                seed_missing[type_id] = missing
                gross_demand[type_id] = gross_demand.get(type_id, 0.0) + (
                    backup_stock + (home_market_stock or 0.0) + (jita_market_stock or 0.0))

    buy_totals, build_runs = _expand_all(seed_missing, cfg, home, jita, manual_overrides, cost_memo,
                                          selected_decryptors, t2_memo, manual_stock, stock_used, base_runs,
                                          gross_demand)

    buy_list = []
    for type_id, quantity in buy_totals.items():
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        volume = _haul_volume(type_id, cfg)
        unit_price = pricing.buy_price(type_id, home, jita, volume, cfg)
        gross = gross_demand.get(type_id, quantity)
        on_hand_pct = max(0.0, min(100.0, (gross - quantity) / gross * 100)) if gross > 0 else 0.0
        buy_list.append(BuyListEntry(
            type_id=type_id, type_name=name, quantity=quantity, unit_price=unit_price,
            on_hand_pct=on_hand_pct,
            total_price=(unit_price * quantity) if unit_price is not None else None,
            buy_from=pricing.buy_source(type_id, home, jita, volume, cfg),
        ))
    buy_list.sort(key=lambda e: e.total_price or 0, reverse=True)

    build_list = []
    for (blueprint_id, activity_id, product_type_id), runs in build_runs.items():
        sde_type = storage.get_sde_type(product_type_id)
        name = sde_type[2] if sde_type else str(product_type_id)
        activity_label = "Reaction" if activity_id == ACTIVITY_REACTION else "Manufacturing"
        base_time = storage.get_blueprint_time(blueprint_id, activity_id) or 0
        bp = storage.get_blueprint_for_product(product_type_id)
        product_qty = bp[2] if bp else 1
        product_activity = classify_activity(product_type_id)[0]
        if product_activity == "Tech II" and product_type_id in t2_memo:
            _, time_mult, decryptor_name = t2_memo[product_type_id]
        else:
            _, time_mult, _ = _activity_mods(product_activity, product_type_id, cfg, {}, blueprint_id)
            decryptor_name = None
        job_time = base_time * time_mult * runs
        unit_cost = cost_memo.get(product_type_id)
        build_list.append(BuildJobEntry(
            type_id=product_type_id, type_name=name, blueprint_type_id=blueprint_id,
            activity=activity_label, quantity=runs * product_qty, job_runs=runs,
            job_time_seconds=job_time, unit_build_cost=unit_cost, decryptor=decryptor_name,
            job_category=job_category(product_type_id),
            # GitHub issue #38: margin_home, not margin_jita - Production
            # sells only at C-J, never Jita (see CLAUDE.md), so the
            # Bauliste's own margin must be the real C-J one, not the
            # informational Jita comparison the standalone Margin page also
            # shows.
            margin=margin_home(product_type_id, unit_cost, home, cfg),
        ))
    build_list.sort(key=lambda e: e.job_time_seconds, reverse=True)

    invention_list.sort(key=lambda e: e.recommended_invention_runs, reverse=True)

    return {"inventory": inventory, "buy_list": buy_list, "build_list": build_list, "invention_list": invention_list}


# Job categories eligible for a slot-split recommendation (user request
# 2026-08-15: "only for components and reactions") - Equipment/Ships/Drones
# aren't included even though they're also Manufacturing-activity jobs.
_SLOT_RECOMMENDATION_CATEGORIES = frozenset({"Reactions", "Advanced Components", "Capital Components"})


def _free_slots_by_category() -> dict[str, int]:
    """Total free (total - currently used) industry job slots right now,
    summed across every registered producer character, keyed by
    constants.SLOT_CATEGORY_LABELS value ("Manufacturing"/"Reactions"/
    "Science") - feeds AssetPlanJob.recommended_slots below. A shared, finite
    pool across every job in that category - this only computes the pool
    size, doesn't partition it between competing jobs (see
    recommended_slots' own docstring for that simplification).

    GitHub issue #39: skips any character flagged excluded_from_planning
    (e.g. an alt kept registered for ESI sync/asset visibility but not
    actually meant to run production jobs) - their slots never enter this
    pool or the asset-optimized split below."""
    totals: dict[str, int] = {}
    for row in character_slot_overview():
        if row.excluded_from_planning:
            continue
        totals[row.job_type] = totals.get(row.job_type, 0) + row.free_slots
    return totals


def _allocate_scarce_stock(claims: list[tuple[int, float]], available: float) -> dict[int, float]:
    """Splits `available` units of a scarce shared material across competing
    claims (parent_job_type_id, quantity_needed): smallest claim first, each
    filled completely while stock lasts - maximizes the *count* of claims
    that end up fully covered, rather than spreading partial coverage across
    everything (confirmed allocation rule for plan_asset_optimized). Returns
    {parent_job_type_id: quantity_covered}, one entry per input claim (0.0
    once stock runs out)."""
    remaining = available
    covered: dict[int, float] = {}
    for parent_id, qty in sorted(claims, key=lambda c: c[1]):
        take = min(qty, remaining)
        covered[parent_id] = take
        remaining -= take
    return covered


def _allocate_slots_proportionally(claims: list[tuple[int, float, int]], available: int) -> dict[int, int]:
    """Splits `available` job slots (one shared pool - see
    _free_slots_by_category) across competing claims (job_type_id,
    time_needed, runs_ready_now) proportionally to each job's *time_needed*
    weight (not raw run count - see below), using the largest-remainder
    method (floor each job's ideal proportional share, then hand out the
    leftover slots one at a time to whichever job's *rounding* remainder is
    largest, repeating so capacity freed up by a job hitting its own
    runs_ready_now cap - it can't usefully take more slots than it has ready
    runs for, one run minimum per slot - gets redistributed to the rest
    instead of sitting unused). Returns {job_type_id: slots}, one entry per
    input claim, summing to at most `available`.

    Weighted by time_needed = runs_ready_now * per-run job time, not by
    runs_ready_now alone - confirmed real user correction (2026-08-16): a
    job with many quick runs and a job with few long runs used to get slots
    proportional to run *count* alone, so a short-running job could
    out-rank a much longer one that will actually occupy a slot far longer.
    The goal is for every job sharing the pool to finish its ready runs at
    roughly the same time, so a job needing more total job-time gets
    proportionally more parallel slots than one that would finish quickly
    on a single slot anyway - runs_ready_now is still used separately as
    each claim's own *cap* (a job can't usefully take more slots than it
    has ready runs to put on them, regardless of how much time weight it
    carries).

    Deliberately proportional, not _allocate_scarce_stock's own "smallest
    claim first, fully filled" rule - confirmed real user correction
    (2026-08-16): that rule is right for a material split among a handful of
    jobs each needing a *comparable, boundable* quantity, but here every
    job's own need (runs_ready_now) is typically orders of magnitude bigger
    than the whole slot pool combined (seen live: a single job with 391,902
    ready runs against 161 total free slots) - "smallest first, fully
    filled" would just hand the *entire* pool to whichever eligible job
    happens to have the fewest ready runs and leave every other job with
    zero, rather than splitting the shared pool across all of them so they
    can all actually be worked on."""
    if available <= 0 or not claims:
        return {type_id: 0 for type_id, _, _ in claims}
    weights = {type_id: weight for type_id, weight, _ in claims}
    caps = {type_id: cap for type_id, _, cap in claims}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {type_id: 0 for type_id in weights}

    ideal = {type_id: available * weight / total_weight for type_id, weight in weights.items()}
    allocated = {type_id: min(int(ideal[type_id]), caps[type_id]) for type_id in weights}

    remaining = available - sum(allocated.values())
    while remaining > 0:
        candidates = [
            (ideal[type_id] - allocated[type_id], type_id)
            for type_id in weights if allocated[type_id] < caps[type_id]
        ]
        if not candidates:
            break
        candidates.sort(reverse=True)
        _, winner = candidates[0]
        allocated[winner] += 1
        remaining -= 1
    return allocated


@storage.with_batch_session()
def plan_asset_optimized(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Asset-aware Bauliste: same buy-vs-build calls as plan_production (see
    _buy_or_build_decision - the two Baulisten never disagree on *whether* to
    build something), but nets real owned stock (_current_stock - ESI assets
    + manual stock + incoming jobs) against demand at *every* level of the
    recursive bill of materials, not just the top-level stock target the way
    plan_production's 'missing' does.

    Processed breadth-first, level by level (a "round" per depth), because a
    level's material demand is only known once the level above it has been
    netted against stock - a node's post-stock shortfall is what actually
    drives how much of *its* materials are needed, so materials can't be
    claimed before their parent's own shortfall is resolved.

    When several jobs need the same scarce intermediate component this round,
    the available stock is handed to the *smallest* claims first so as many
    whole jobs as possible become immediately startable, rather than
    spreading thin partial coverage across everything - maximizes the count
    of fully-covered claims against one shared pool (confirmed allocation
    rule). Stock consumption is tracked in a running ledger across rounds
    (stock_used) so a component needed at two different tree depths (a
    genuinely common EVE BOM shape - e.g. a capital component used both
    directly and inside a sub-assembly) doesn't get the same physical stock
    counted twice.

    Readiness (runs_ready_now) specifically uses _stock_on_hand, not
    _current_stock, with its own parallel ledger (stock_used_on_hand) - see
    _stock_on_hand's docstring for the real bug this fixes: _current_stock
    counts an in-progress industry job's eventual output as available (so
    job *sizing* doesn't over-plan against something already queued), but
    that output doesn't physically exist yet, so it can't make a *different*
    job "Ready Now" - confirmed live (2026-08-15) via Sylramic Fibers: with
    a batch already cooking, every job needing it showed ready, but only one
    could actually be started in EVE.

    Known simplification: if the same type_id is *both* a directly-configured
    stock target *and* a shared material of another job, its current stock is
    still netted separately in each role (once against its own backup_stock
    target, once via the stock_used ledger for the other job's claim) - a
    rare dual-role case, not worth a fully joint ledger across both.

    Each material's per-parent claim also carries the same overbuild buffer
    _expand_all applies (component_overbuild x base_qty x material_mult x
    that parent's whole-tree base_runs, via the shared
    _parent_base_runs_for_buffer helper - same buffered-once-per-parent
    guard against double-counting) - confirmed real gap (2026-08-01): this
    used to compute bare gross demand only, no buffer, which only became
    visible once stock was finally counted correctly everywhere: it showed
    far fewer Reaction jobs than plan_production for the same underlying
    data, since plan_production was still trying to grow cfg.
    component_overbuild's stock cushion and this wasn't accounting for that
    goal at all - the two Baulisten must agree on *how much* to build, not
    just *whether*.

    Returns {'jobs': list[AssetPlanJob]} sorted by job_runs desc. A job's
    runs_ready_now is bottlenecked by whichever of its own direct materials is
    scarcest right now (the rest of job_runs still needs something upstream
    built or bought first). For Reactions/Advanced Components/Capital
    Components jobs specifically (user request 2026-08-15), also fills in
    recommended_slots - your currently-free character job slots
    (_free_slots_by_category), split *by time needed* (runs_ready_now x
    per-run job time) *across every eligible ready job sharing that slot
    category at once* (_allocate_slots_proportionally) - not each job
    recommended the whole pool independently, and not split by raw run count
    either (user correction 2026-08-16) - see AssetPlanJob.recommended_slots
    for why that's the goal."""
    ctx = _PlanContext(cfg)
    manual_stock, manual_overrides, selected_decryptors = ctx.manual_stock, ctx.manual_overrides, ctx.selected_decryptors
    home, jita, cost_indices, adjusted_prices = ctx.home, ctx.jita, ctx.cost_indices, ctx.adjusted_prices

    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, tuple[float, float, Optional[str]]] = {}
    jobs: dict[int, AssetPlanJob] = {}
    stock_used: dict[int, float] = {}
    # Parallel ledger, on-hand-only (see _stock_on_hand) - tracks readiness
    # consumption separately from stock_used's incoming-inclusive planning
    # consumption, so the same physical unit can't be double-claimed as
    # "ready" by two different jobs across rounds either.
    stock_used_on_hand: dict[int, float] = {}
    # Stock-oblivious sizing baseline for the overbuild buffer (see
    # _base_runs/_parent_base_runs_for_buffer docstrings) - same call
    # plan_production makes, over the same stock_targets, so the two
    # Baulisten's buffers can never disagree.
    base_runs = _base_runs(cfg, home, jita, manual_overrides, cost_memo, selected_decryptors, t2_memo,
                            cost_indices, adjusted_prices, ctx.stock_targets)
    buffered_parents: set[int] = set()

    # Same margin gate as plan_production (see its skip_due_to_margin): a
    # stock target's demand is dropped entirely - no job here, and (in
    # plan_production) no Buy List entry either - if building it doesn't
    # clear cfg.min_margin, rather than falling back to "build it anyway" or
    # "buy it instead" (confirmed with the user 2026-08-01: an item not
    # worth building isn't assumed worth buying either). Accumulated across
    # *all* top-level targets first, then reused for the Phase B per-material
    # decision further down - confirmed real bug: this used to build a
    # throwaway per-item override only consulted for that item's own
    # top-level decision, never reaching the recursive material-sourcing
    # decision, so a low-margin stock target that's *also* a raw material of
    # another job (common - e.g. a T1 hull that's both a sell target and a
    # component of its T2 variant's blueprint) could be excluded in
    # plan_production but still "Build" here for the same type_id,
    # contradicting this function's own "never disagree" docstring.
    margin_excluded: set[int] = set()
    jobs_this_level: dict[int, float] = {}
    # How much of type_id's own current demand is already covered by owned
    # stock (0-1, None if there's nothing to compare against) - purely a
    # display signal (AssetPlanJob.stock_coverage, "how urgent is this one"),
    # not used anywhere in the sourcing/queueing math above - confirmed with
    # the user this should stay opt-in/sortable, not drive the jobs list's
    # own sort order (see plan_asset_optimized's docstring). Two different
    # denominators feed the same field, deliberately - both answer "how much
    # of what's wanted is already here", just relative to a different goal:
    # - stock targets (filled below, before the round loop): current_stock /
    #   configured backup+home+Jita target - a stable, user-set goal.
    # - pure intermediate components (filled in Phase B below, the first
    #   time each material_id turns into a job): available / buffered_total
    #   for the round it was queued in - there's no user-set target for a
    #   component only reached as another job's material, so its own
    #   current-round demand is the only meaningful "goal" to net against.
    #   Never overwrites a stock target's entry (see the dual-role case in
    #   this function's docstring) - the configured target is the more
    #   stable, meaningful number when both exist for the same type_id.
    stock_coverage_by_id: dict[int, Optional[float]] = {}
    for type_id, type_name, backup_stock, home_market_stock, jita_market_stock in ctx.stock_targets:
        activity, bp = classify_activity(type_id)
        if bp is None:
            continue
        current_stock = _current_stock(type_id, manual_stock, cfg, bp)
        missing = _total_missing(type_id, backup_stock, home_market_stock, jita_market_stock, current_stock, cfg)
        if missing <= 0:
            continue
        _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors, t2_memo, cost_indices, adjusted_prices)

        if type_id not in manual_overrides:
            margin = _build_margin(type_id, cost_memo.get(type_id), jita_market_stock, home, jita, cfg)
            if margin is not None and margin < cfg.min_margin:
                margin_excluded.add(type_id)

        if type_id in margin_excluded:
            continue
        if _buy_or_build_decision(type_id, cfg, home, jita, manual_overrides, cost_memo, bp) != "Build":
            continue
        jobs_this_level[type_id] = jobs_this_level.get(type_id, 0.0) + missing
        total_target = backup_stock + (home_market_stock or 0.0) + (jita_market_stock or 0.0)
        stock_coverage_by_id[type_id] = min(1.0, current_stock / total_target) if total_target > 0 else None

    depth = 0
    while jobs_this_level and depth < MAX_DEPTH:
        # Phase A: this round's net production quantity -> runs + raw
        # material claims (not yet netted against stock). Two parallel
        # totals per material, deliberately kept separate: next_level_claims
        # (bare, per-parent, unbuffered) drives runs_ready_now below - a
        # job's *readiness to start* should reflect whether its actual
        # material needs are covered, not whether there's also enough spare
        # stock to grow cfg.component_overbuild's cushion on top. buffered_demand
        # (bare + overbuild buffer, pooled) is what actually determines the
        # shortfall queued for the next round - the same total plan_production's
        # _expand_all would compute for the same material.
        runs_this_round: dict[int, int] = {}
        job_meta: dict[int, tuple] = {}
        next_level_claims: dict[int, list[tuple[int, float]]] = {}
        buffered_demand: dict[int, float] = {}

        for type_id, quantity in jobs_this_level.items():
            activity, bp = classify_activity(type_id)
            if bp is None:
                continue
            blueprint_id, activity_id, product_qty = bp
            runs = math.ceil(quantity / product_qty)
            runs_this_round[type_id] = runs

            if activity == "Tech II" and type_id in t2_memo:
                material_mult, time_mult, decryptor_name = t2_memo[type_id]
            else:
                material_mult, time_mult, _ = _activity_mods(activity, type_id, cfg, {}, blueprint_id)
                decryptor_name = None
            activity_label = "Reaction" if activity_id == ACTIVITY_REACTION else "Manufacturing"
            job_meta[type_id] = (blueprint_id, activity_id, product_qty, activity_label, decryptor_name, time_mult)

            parent_base_runs = _parent_base_runs_for_buffer(type_id, base_runs, buffered_parents)
            for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
                bare_needed = _material_qty(base_qty, material_mult, runs)
                if bare_needed <= 0:
                    continue
                next_level_claims.setdefault(material_id, []).append((type_id, bare_needed))
                overbuild = 0.0 if material_id in manual_overrides else cfg.component_overbuild
                buffer = overbuild * base_qty * material_mult * parent_base_runs
                buffered_demand[material_id] = buffered_demand.get(material_id, 0.0) + bare_needed + buffer

        # Phase B: readiness (min_fraction) is allocated across the *bare*
        # per-job claims, smallest first, against stock that's *physically on
        # hand right now* (_stock_on_hand - excludes still-in-progress
        # industry jobs, unlike _current_stock) - purely informational,
        # doesn't drive how much stock gets marked consumed for sizing.
        # Actual consumption/shortfall sizing uses the *buffered* pooled
        # total against _current_stock's incoming-inclusive availability
        # instead (a legitimate "don't plan to build even more of something
        # already queued" question, distinct from "can I click start now").
        min_fraction: dict[int, float] = {type_id: 1.0 for type_id in runs_this_round}
        jobs_this_level = {}
        for material_id, claims in next_level_claims.items():
            _, m_bp = classify_activity(material_id)
            available = max(0.0, _current_stock(material_id, manual_stock, cfg, m_bp) - stock_used.get(material_id, 0.0))
            available_on_hand = max(
                0.0, _stock_on_hand(material_id, manual_stock, cfg) - stock_used_on_hand.get(material_id, 0.0))
            covered_by_parent = _allocate_scarce_stock(claims, available_on_hand)
            stock_used_on_hand[material_id] = stock_used_on_hand.get(material_id, 0.0) + sum(covered_by_parent.values())

            for parent_type_id, qty in claims:
                fraction = covered_by_parent.get(parent_type_id, 0.0) / qty if qty > 0 else 1.0
                if fraction < min_fraction[parent_type_id]:
                    min_fraction[parent_type_id] = fraction

            buffered_total = buffered_demand.get(material_id, 0.0)
            consumed = min(buffered_total, available)
            stock_used[material_id] = stock_used.get(material_id, 0.0) + consumed
            shortfall = buffered_total - consumed
            if shortfall <= 0 or m_bp is None:
                continue
            if material_id not in margin_excluded and _buy_or_build_decision(
                    material_id, cfg, home, jita, manual_overrides, cost_memo, m_bp, depth + 1) == "Build":
                jobs_this_level[material_id] = jobs_this_level.get(material_id, 0.0) + shortfall
                if material_id not in stock_coverage_by_id:
                    stock_coverage_by_id[material_id] = available / buffered_total if buffered_total > 0 else None

        # Phase C: materialize/merge this round's AssetPlanJob entries now
        # that readiness is known.
        for type_id, runs in runs_this_round.items():
            blueprint_id, activity_id, product_qty, activity_label, decryptor_name, time_mult = job_meta[type_id]
            sde_type = storage.get_sde_type(type_id)
            name = sde_type[2] if sde_type else str(type_id)
            base_time = storage.get_blueprint_time(blueprint_id, activity_id) or 0
            job_time = base_time * time_mult * runs
            ready_increment = math.floor(runs * min_fraction[type_id])

            existing = jobs.get(type_id)
            if existing is None:
                jobs[type_id] = AssetPlanJob(
                    type_id=type_id, type_name=name, blueprint_type_id=blueprint_id,
                    activity=activity_label, quantity=runs * product_qty, job_runs=runs,
                    runs_ready_now=ready_increment, job_time_seconds=job_time,
                    unit_build_cost=cost_memo.get(type_id), decryptor=decryptor_name,
                    job_category=job_category(type_id),
                    stock_coverage=stock_coverage_by_id.get(type_id),
                    # GitHub issue #38: margin_home, not margin_jita - see
                    # plan_production's BuildJobEntry construction for why.
                    margin=margin_home(type_id, cost_memo.get(type_id), home, cfg),
                )
            else:
                # Same item is a job in more than one round (needed at two
                # different tree depths) - merge rather than overwrite, so an
                # earlier round's already-netted readiness isn't lost.
                existing.job_runs += runs
                existing.quantity += runs * product_qty
                existing.runs_ready_now += ready_increment
                existing.job_time_seconds += job_time

        depth += 1

    free_slots = _free_slots_by_category()
    eligible_by_pool: dict[str, list[AssetPlanJob]] = {}
    for job in jobs.values():
        if job.job_category not in _SLOT_RECOMMENDATION_CATEGORIES or job.runs_ready_now <= 0:
            continue
        pool_label = "Reactions" if job.activity == "Reaction" else "Manufacturing"
        eligible_by_pool.setdefault(pool_label, []).append(job)
    for pool_label, pool_jobs in eligible_by_pool.items():
        # Weight = time needed for this job's ready runs specifically
        # (runs_ready_now * per-run time), not runs_ready_now alone - see
        # _allocate_slots_proportionally's docstring. job_runs is always >=1
        # here (job.runs_ready_now <= job.job_runs, and the loop above
        # already skipped runs_ready_now <= 0), so per-run time is safe to
        # divide out.
        claims = [
            (j.type_id, j.runs_ready_now * (j.job_time_seconds / j.job_runs), j.runs_ready_now)
            for j in pool_jobs
        ]
        allocation = _allocate_slots_proportionally(claims, free_slots.get(pool_label, 0))
        for j in pool_jobs:
            j.recommended_slots = allocation[j.type_id]

    return {"jobs": sorted(jobs.values(), key=lambda j: j.job_runs, reverse=True)}


def market_status(cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[MarketStatusRow]:
    """Personal stock vs. backup target, and home/Jita market-listed stock
    (open sell order volume) vs. their own targets - see storage.stock_targets'
    home_market_stock/jita_market_stock columns and character_sell_orders.

    GitHub issue #33: a stock target with neither a home nor a Jita market
    target configured (both NULL/0 - pure backup/component stock, never
    meant to be listed anywhere) isn't a market-status concern at all and is
    skipped here, rather than showing up with two permanently-empty target
    columns. Treats 0 the same as NULL (confirmed live: Griffin had
    home_market_stock=0, jita_market_stock=NULL and still showed up when
    only NULL was checked - a 0 target is never a real, deliberately-set
    market target in practice)."""
    manual_stock = storage.load_manual_stock()
    rows = []
    for type_id, type_name, backup_target, home_target, jita_target in storage.load_stock_targets():
        if not home_target and not jita_target:
            continue
        _, bp = classify_activity(type_id)
        backup_current = _current_stock(type_id, manual_stock, cfg, bp)
        home_listed = (
            storage.sell_order_qty_at_location(type_id, cfg.home_location_id)
            if cfg.home_location_id is not None else 0.0
        )
        jita_listed = storage.sell_order_qty_in_region(type_id, TRADING_CONFIG.jita_region_id)
        rows.append(MarketStatusRow(
            type_id=type_id, type_name=type_name,
            backup_target=backup_target, backup_current=backup_current,
            home_target=home_target, home_listed=home_listed,
            jita_target=jita_target, jita_listed=jita_listed,
        ))
    return rows


def stock_value(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Total ISK value of current stock (manual + ESI assets + incoming jobs,
    same _current_stock every stock target uses), priced at each item's C-J
    sell quote - its real opportunity-cost value even for stock that's
    actually consumed internally rather than resold, same sourcing
    _build_margin uses for a home-market target - falling back to the Jita
    sell quote if there's no C-J listing. Returns {'total_value': ISK,
    'priced_items': N, 'unpriced_items': N} - unpriced items (no sell quote
    anywhere) are excluded from total_value rather than counted as 0, so a
    temporary market-data gap doesn't silently understate the total."""
    manual_stock = storage.load_manual_stock()
    stock_targets = storage.load_stock_targets()
    # Only the stock targets' own type_ids need pricing here (this KPI never
    # recurses into materials) - see pricing.home_prices/jita_prices' own
    # docstrings for why callers must scope type_ids explicitly now.
    type_ids = [t[0] for t in stock_targets]
    home = pricing.home_prices(cfg, type_ids)
    jita = pricing.jita_prices(type_ids)

    total_value = 0.0
    priced_items = 0
    unpriced_items = 0
    for type_id, _type_name, _backup_target, _home_target, _jita_target in stock_targets:
        _, bp = classify_activity(type_id)
        current = _current_stock(type_id, manual_stock, cfg, bp)
        if current <= 0:
            continue
        home_quote = home.get(type_id)
        jita_quote = jita.get(type_id)
        if home_quote and home_quote.sell > 0:
            price = home_quote.sell
        elif jita_quote and jita_quote.sell > 0:
            price = jita_quote.sell
        else:
            unpriced_items += 1
            continue
        total_value += current * price
        priced_items += 1
    return {"total_value": total_value, "priced_items": priced_items, "unpriced_items": unpriced_items}


# Confirmed real CCP SDE / market data quirks (found via live
# discover_build_candidates output - see the two cases below), both of which
# produce a nonsensically huge margin that would otherwise sort straight to
# the top of every scan and mislead the user:
#
# 1. Reward/event ships (Gnosis, Praxis, Sunesis, Metamorphosis) DO have a
#    real, *published* blueprint entry in the SDE (e.g. "Praxis Blueprint",
#    type_id 47716), but its material list is a vestigial placeholder -
#    exactly one line, 1x Tritanium - since CCP never intended it to be
#    manufactured through normal industry.
# 2. Faction Warfare LP-store ships (e.g. Marshal) go further: home AND Jita
#    independently agreed on a real, mutually-consistent ~9.5B ISK sell
#    price (confirmed live, not a stale/troll order) for a "build cost" of
#    ~828M from a real, substantial (17-material) blueprint entry - but a
#    Marshal isn't actually obtained by manufacturing one from base
#    minerals, it's redeemed via Loyalty Points at an NPC corp store. The
#    SDE blueprint entry exists (presumably modeling the LP conversion
#    formula) but doesn't reflect a real, ISK-only industry path - this
#    tool has no LP-cost data at all, so it can't tell "genuinely
#    manufacturable" apart from "SDE has a formula for it anyway".
#
# No genuine, correctly-costed opportunity in this app's own data has ever
# approached these margins (cfg.min_margin defaults to 15%; a real excellent
# opportunity rarely clears a few hundred percent) - reject anything above
# this as almost certainly bad/inapplicable upstream data rather than a
# real find. Deliberately conservative (300%, not 1000%) after confirming
# a "real" blueprint with real, cross-market-consistent prices (Marshal)
# still produced ~990% - a looser cap wouldn't have caught it.
MAX_PLAUSIBLE_BUILD_MARGIN = 3.0  # 300%

# The scan below walks every published, market-listed SDE item (~19,400 rows)
# and prices each one - tens of seconds even on a fast connection, dominated by
# the per-candidate cost/margin computation, not I/O. Cached the same way
# esi_client.py already caches its own less-volatile ESI calls (adjusted
# prices/cost indices): a plain time.time()-based TTL, no key-based
# invalidation against every individual cfg field, since home/Jita prices
# themselves drift continuously regardless of cfg and a short TTL already
# bounds that staleness. What DOES make an already-cached scan wrong - a
# stock target added/removed (changes existing_target_ids), a Settings save
# (changes min_margin/min_daily_profit/build costs), a fresh SDE refresh - is
# invalidated explicitly (invalidate_discover_cache, called from actions.py)
# rather than left to expire on its own.
_DISCOVER_CACHE_TTL = 600  # seconds
# GitHub issue #54 (confirmed real cross-tenant data leak, found in a
# full-codebase audit 2026-08-21): this used to be a single scalar
# (Optional[list[dict]]) shared by every tenant in the process, even though
# every input that feeds the scan (ProductionConfig, stock targets, selected
# decryptors) is tenant-scoped. The first tenant to call this within the TTL
# window got their scan served to every other tenant hitting the same
# process afterwards - wrong build costs/margins, and a wrong "not already
# tracked" exclusion set. Now keyed by tenant_id (storage.get_current_tenant())
# so each tenant gets their own cache entry; a call with no ambient tenant
# set (shouldn't normally happen - see storage.py's own "fail closed on no
# tenant" philosophy) just skips caching entirely rather than risk sharing
# across an unknown boundary.
_discover_cache: dict[str, list[dict]] = {}
_discover_cache_at: dict[str, float] = {}
# Guards both the cache dict itself and the scan that fills it (see
# discover_build_candidates) - held for the whole scan, not just the cache
# read/write, so two callers racing on a cold cache (e.g. the background
# scheduler and a user's own browser request both landing at once) serialize
# instead of one thread's slow, redundant scan clobbering the other's -
# whichever thread loses the race just waits, then gets the winner's
# now-cached result for free instead of independently re-scanning ~19,400
# SDE items a second time. One lock shared across all tenants' entries is
# fine here (the scan itself, not lock contention, dominates cost) - two
# different tenants scanning concurrently just serialize on this lock too,
# not ideal but simple and correct; revisit only if that's ever a measured
# problem.
_discover_cache_lock = threading.Lock()


def invalidate_discover_cache(all_tenants: bool = False) -> None:
    """Forces the next discover_build_candidates call to re-scan instead of
    reusing a cached result. Defaults to the *current* tenant only (see the
    cache's own comment) - call after anything that changes just that
    tenant's own result set (stock target add/remove, Settings save,
    decryptor change). `all_tenants=True` clears every tenant's entry - the
    only correct choice for a genuinely global change, e.g. admin.
    do_refresh_sde()'s SDE refresh (blueprint/material data every tenant's
    cache was built from, not just the calling admin's own). Confirmed real
    gap (GitHub issue #54's own cross-tenant-cache-key fix): a per-tenant-
    only invalidation left every *other* tenant serving stale discover/
    margin results for up to the full TTL after a global SDE refresh, since
    only the calling tenant's own entry was ever cleared."""
    global _discover_cache, _discover_cache_at
    with _discover_cache_lock:
        if all_tenants:
            _discover_cache.clear()
            _discover_cache_at.clear()
            return
        tenant_id = storage.get_current_tenant()
        _discover_cache.pop(tenant_id, None)
        _discover_cache_at.pop(tenant_id, None)


def discover_build_candidates(cfg: ProductionConfig = PRODUCTION_CONFIG, top_n: int = 200,
                               client: Optional["GoonmetricsClient"] = None) -> list[dict]:
    """Production's equivalent of Trading's candidate discovery (candidate_
    discovery.py): scans every published, market-listed SDE item that has a
    real Manufacturing/Reaction/Invention recipe (classify_activity) and
    ISN'T already a configured stock target, and flags the ones where
    building clearly beats buying right now - reuses the exact same
    buy-vs-build margin gate (_unit_cost + _build_margin, cfg.min_margin)
    already used to decide whether a *configured* stock target is worth
    building (see plan_production), just run against everything you haven't
    added yet instead of only what you already track. Prices at the C-J
    (home) sell quote net of cfg.market_fees, same as a stock target with no
    Jita market target - "build it and sell it locally" is the default
    framing here, matching this tool's own C-J-centric design.

    Every candidate shares one _PlanContext (one home/Jita price fetch, one
    cost-index/adjusted-price fetch) and one cost_memo/t2_memo pair across the
    whole scan, same as plan_production's own single-pass walk - a shared
    material (e.g. a common mineral) is only ever priced once no matter how
    many candidates use it, not once per candidate.

    Confirmed with the user: margin alone isn't a useful ranking - a huge
    margin on an item nobody actually buys is worthless, same "profitability
    alone isn't enough" reasoning Trading's own min_avg_movement gate already
    encodes (see config.py). So every margin-qualifying candidate gets a
    batched Goonmetrics history lookup (TRADING_CONFIG.reference_region_id -
    the C-J structure's own region, already reused as-is elsewhere in this
    file for the same "Production borrows Trading's region constants for
    market-wide lookups" reason, see market_status) for its recent daily
    "movement" - confirmed live (2026-07-15) against ESI's own public
    /markets/{region_id}/history/ endpoint (two separate items, every field
    checked day-by-day) that this is literally ESI's raw `volume` figure -
    units traded that day, not an ISK value - Goonmetrics is evidently just
    re-serving ESI's own daily history verbatim under a differently-named
    field. (This corrects an earlier, wrong assumption in this codebase that
    "movement" was an ISK-value-traded liquidity proxy - see config.py's
    min_avg_movement, which predates this feature and inherited the same
    mislabeling; Trading's own history_backtest.py scoring only ever feeds
    movement through log(1+x) as a soft liquidity weight, so it was never
    literally treated as an ISK figure there and isn't affected the same
    way.) potential_daily_profit = daily_movement (units/day) x
    profit_per_unit, where profit_per_unit = margin x build_cost (both
    follow directly from margin's own definition: margin =
    profit_per_unit/build_cost). Results are ranked by this, not raw margin -
    a modest-margin, liquid item now correctly outranks a huge-margin item
    nobody's actually trading.

    potential_daily_profit is a theoretical ceiling - "if this item's entire
    day of C-J market turnover were captured", the same "(theoretical)"
    framing Trading's own Shortlist "Profit / Day" column already uses
    (profit_per_unit x total sell_volume, not a claim about what one seller
    could realistically move) - confirmed deliberate with the user
    (2026-07-15) after live-testing surfaced very large figures for rare,
    expensive items (capital hulls, faction modules) that trade only a
    handful of units/day: a tiny unit count times a huge per-unit profit is
    still mathematically the correct answer to "what's the total market
    worth", it's just a ceiling nobody could fully capture alone - not a bug
    to cap or filter out.

    Deliberately C-J (home) only, not Jita - confirmed with the user
    (2026-07-15): production here is for the local C-J market, freighting
    finished goods to Jita to sell isn't part of this tool's business model.
    A T2 module can legitimately show a low potential_daily_profit if C-J's
    own market simply doesn't move much of it, even though the same item
    turns over heavily in Jita - that's accurate for this tool's actual sales
    channel, not a bug to "fix" by pricing at Jita instead.

    Cached for _DISCOVER_CACHE_TTL seconds (see that constant's comment) -
    `top_n` only slices the cached, already-ranked list, so repeat calls with
    a different `top_n` (e.g. the Build Candidates page's "Results to fetch"
    field) reuse the same scan instead of re-triggering it. The scan itself
    runs under _discover_cache_lock (see its comment) - two callers racing on
    a cold cache serialize instead of redundantly scanning twice. Cached per
    tenant (GitHub issue #54) - see the cache's own module-level comment."""
    global _discover_cache, _discover_cache_at
    tenant_id = storage.get_current_tenant()
    with _discover_cache_lock:
        cached_at = _discover_cache_at.get(tenant_id, 0.0)
        if tenant_id is not None and tenant_id in _discover_cache \
                and (time.time() - cached_at) < _DISCOVER_CACHE_TTL:
            return _discover_cache[tenant_id][:top_n]
        results = _scan_build_candidates(cfg, client)
        if tenant_id is not None:
            _discover_cache[tenant_id] = results
            _discover_cache_at[tenant_id] = time.time()
        return results[:top_n]


@storage.with_batch_session()
def _scan_build_candidates(cfg: ProductionConfig, client: Optional["GoonmetricsClient"]) -> list[dict]:
    """The actual (slow - walks every SDE item) scan behind
    discover_build_candidates - split out so the public function can hold
    _discover_cache_lock across the whole scan without an awkwardly deep
    `with` indentation over this entire body. Wrapped in one shared DB
    connection (storage.batch_session) for the same reason plan_production/
    plan_asset_optimized are - this walks up to ~19,400 SDE items, each
    triggering several storage calls."""
    ctx = _PlanContext(cfg)
    existing_target_ids = {t[0] for t in ctx.stock_targets}

    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, tuple[float, float, Optional[str]]] = {}

    results = []
    for type_id, type_name, _volume, _market_group_id, meta_level, _category_id in storage.load_sde_types_with_market_group():
        if type_id in existing_target_ids:
            continue
        activity, bp = classify_activity(type_id)
        if bp is None:
            continue
        home_quote = ctx.home.get(type_id)
        if home_quote is None or home_quote.buy <= 0 or home_quote.buy == home_quote.sell:
            # buy == sell (exactly) is the signature of a market with no real
            # order book on one side - a synthetic/fallback price, not a
            # genuine live quote (confirmed live: 'Racket' Light Neutron
            # Blaster I showed buy=sell=689083.36 to the ISK, which a real
            # independent buy-side and sell-side order book essentially never
            # produces by coincidence).
            continue
        build_cost = _unit_cost(type_id, cfg, ctx.home, ctx.jita, cost_memo, ctx.selected_decryptors,
                                 t2_memo, ctx.cost_indices, ctx.adjusted_prices)
        margin = _build_margin(type_id, build_cost, None, ctx.home, ctx.jita, cfg)
        if margin is None or margin < cfg.min_margin or margin > MAX_PLAUSIBLE_BUILD_MARGIN:
            continue
        results.append({
            "type_id": type_id, "type_name": type_name, "activity": activity,
            "build_cost": build_cost, "margin": margin, "meta_level": meta_level,
        })

    if results:
        if client is None:
            # Lazy import, same "keeps this dependency lazy" reasoning as
            # _PlanContext/stock_value above - GoonmetricsClient is only ever
            # a type-hint (never evaluated at runtime, this file has `from
            # __future__ import annotations`) until we actually get here.
            from ..goonmetrics_client import GoonmetricsClient
            client = GoonmetricsClient()
        movement_by_type: dict[int, list[float]] = {}
        for point in client.price_history_chunked(TRADING_CONFIG.reference_region_id,
                                                    [r["type_id"] for r in results]):
            movement_by_type.setdefault(point.type_id, []).append(point.movement)
        for r in results:
            days = movement_by_type.get(r["type_id"])
            daily_movement = sum(days) / len(days) if days else 0.0
            r["daily_movement"] = daily_movement
            r["potential_daily_profit"] = daily_movement * r["margin"] * r["build_cost"]
        results = [r for r in results if r["potential_daily_profit"] >= cfg.min_daily_profit]

    results.sort(key=lambda r: r.get("potential_daily_profit", 0.0), reverse=True)
    return results


# Separate cache from _discover_cache above, same TTL/lock shape - a
# different result set (every ship, not just margin-qualifying non-tracked
# candidates) computed from the same underlying inputs, so it needs its own
# staleness window rather than sharing one cache key for two different
# questions. Invalidated at the exact same call sites as
# invalidate_discover_cache (see production/actions.py) - anything that
# changes build_cost/margin inputs invalidates both. Keyed per tenant, same
# GitHub issue #54 fix and same reasoning as _discover_cache above.
_SHIP_MARGIN_CACHE_TTL = 600  # seconds
_ship_margin_cache: dict[str, list[dict]] = {}
_ship_margin_cache_at: dict[str, float] = {}
_ship_margin_cache_lock = threading.Lock()


def invalidate_ship_margin_cache(all_tenants: bool = False) -> None:
    """Forces the next discover_ship_margins call to re-scan - call alongside
    invalidate_discover_cache (see that function's own call sites in
    production/actions.py and its own `all_tenants` docstring for when to
    pass it here too)."""
    global _ship_margin_cache, _ship_margin_cache_at
    with _ship_margin_cache_lock:
        if all_tenants:
            _ship_margin_cache.clear()
            _ship_margin_cache_at.clear()
            return
        tenant_id = storage.get_current_tenant()
        _ship_margin_cache.pop(tenant_id, None)
        _ship_margin_cache_at.pop(tenant_id, None)


def discover_ship_margins(cfg: ProductionConfig = PRODUCTION_CONFIG,
                           client: Optional["GoonmetricsClient"] = None) -> list[dict]:
    """Production's Margin page (list view): every ship with a real
    Manufacturing/Reaction/Invention recipe (classify_activity), with its
    current home/Jita sell price, build cost, and both margin_home/
    margin_jita (see those functions' own docstrings) - deliberately an
    information browser, not a candidate filter like discover_build_
    candidates: no existing_target_ids exclusion, no min_margin/
    min_daily_profit gate, no history/movement lookup. A ship with no real
    order book on one or both sides still appears, with None for whichever
    price/margin couldn't be computed (matches Doctrine Stockpile's own
    "gray = no data yet" convention rather than silently hiding rows).

    Cached the same way discover_build_candidates is (_SHIP_MARGIN_CACHE_TTL,
    _ship_margin_cache_lock), per tenant (GitHub issue #54) - `client` param
    only exists for tests to inject a fake Goonmetrics client the same way
    discover_build_candidates accepts one, though this function never
    actually calls it (no movement lookup needed here)."""
    global _ship_margin_cache, _ship_margin_cache_at
    tenant_id = storage.get_current_tenant()
    with _ship_margin_cache_lock:
        cached_at = _ship_margin_cache_at.get(tenant_id, 0.0)
        if tenant_id is not None and tenant_id in _ship_margin_cache \
                and (time.time() - cached_at) < _SHIP_MARGIN_CACHE_TTL:
            return _ship_margin_cache[tenant_id]
        results = _scan_ship_margins(cfg)
        if tenant_id is not None:
            _ship_margin_cache[tenant_id] = results
            _ship_margin_cache_at[tenant_id] = time.time()
        return results


def _descendant_market_group_ids(root_id: int) -> set[int]:
    """`root_id` plus every market_group_id nested under it (any depth) in
    sde_market_groups' parent_group_id tree - lets a caller exclude a whole
    named market-group subtree (e.g. "Special Edition Ships", GitHub issue
    #7) instead of an ad-hoc type_id/name list that would miss anything CCP
    adds to that subtree later."""
    children: dict[Optional[int], list[int]] = {}
    for market_group_id, parent_group_id, _name in storage.load_sde_market_groups():
        children.setdefault(parent_group_id, []).append(market_group_id)
    result = {root_id}
    frontier = [root_id]
    while frontier:
        frontier = [child for parent in frontier for child in children.get(parent, [])]
        result.update(frontier)
    return result


@storage.with_batch_session()
def _scan_ship_margins(cfg: ProductionConfig) -> list[dict]:
    """The actual scan behind discover_ship_margins - split out for the same
    reason _scan_build_candidates is (holds the cache lock across the whole
    scan, not just the read/write). Ships are a much smaller SDE slice than
    discover_build_candidates' full scan (~19,400 items), so this is cheap
    by comparison even though it's not gated by margin/daily-profit at all."""
    ctx = _PlanContext(cfg)
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, tuple[float, float, Optional[str]]] = {}
    excluded_market_groups = _descendant_market_group_ids(SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID)

    results = []
    for type_id, type_name, _volume, market_group_id, meta_level, category_id in storage.load_sde_types_with_market_group():
        if category_id != SHIP_CATEGORY_ID:
            continue
        if market_group_id in excluded_market_groups:
            continue
        activity, bp = classify_activity(type_id)
        if bp is None:
            continue
        build_cost = _unit_cost(type_id, cfg, ctx.home, ctx.jita, cost_memo, ctx.selected_decryptors,
                                 t2_memo, ctx.cost_indices, ctx.adjusted_prices)
        home_quote = ctx.home.get(type_id)
        jita_quote = ctx.jita.get(type_id)
        results.append({
            "type_id": type_id, "type_name": type_name, "activity": activity,
            "home_price": home_quote.sell if home_quote and home_quote.sell > 0 else None,
            "jita_price": jita_quote.sell if jita_quote and jita_quote.sell > 0 else None,
            "build_cost": build_cost,
            "margin_home": margin_home(type_id, build_cost, ctx.home, cfg),
            "margin_jita": margin_jita(type_id, build_cost, ctx.jita, cfg),
            "meta_level": meta_level,
        })

    results.sort(key=lambda r: r.get("margin_home") or 0.0, reverse=True)
    return results


@storage.with_batch_session()
def item_margin_detail(type_id: int, type_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Production Margin page's search: the same row shape discover_ship_
    margins produces, for one arbitrary already-resolved item (any category,
    not just ships - see production/actions.py's do_get_item_margin, which
    resolves `type_name` -> `type_id` the same way do_build_material_tree
    does before calling this). No caching needed (one item per call, same
    cheap cost profile as the existing Material Tree feature) - a fresh
    _PlanContext every call, unlike discover_ship_margins' cached whole-
    catalog scan."""
    ctx = _PlanContext(cfg)
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, tuple[float, float, Optional[str]]] = {}
    activity, _bp = classify_activity(type_id)
    build_cost = _unit_cost(type_id, cfg, ctx.home, ctx.jita, cost_memo, ctx.selected_decryptors,
                             t2_memo, ctx.cost_indices, ctx.adjusted_prices)
    home_quote = ctx.home.get(type_id)
    jita_quote = ctx.jita.get(type_id)
    return {
        "type_id": type_id, "type_name": type_name, "activity": activity,
        "home_price": home_quote.sell if home_quote and home_quote.sell > 0 else None,
        "jita_price": jita_quote.sell if jita_quote and jita_quote.sell > 0 else None,
        "build_cost": build_cost,
        "margin_home": margin_home(type_id, build_cost, ctx.home, cfg),
        "margin_jita": margin_jita(type_id, build_cost, ctx.jita, cfg),
    }


def _structural_material_closure(seed_type_ids: Iterable[int]) -> set[int]:
    """Every type_id reachable from `seed_type_ids` through blueprint
    materials, ignoring buy-vs-build economics entirely - pricing isn't
    known yet at this point, that's the whole reason this exists: a
    bounded, price-agnostic universe to bulk-price (see pricing.home_prices/
    jita_prices), rather than needing pricing for "everything" up front the
    way the old Goonmetrics-whole-market-dump approach did.

    Necessarily a superset of what the real priced _expand_all walk would
    visit (a node whose real price makes "Buy" cheaper still gets recursed
    into here) - that's correct, not wasteful: better to have a live price
    ready and unused than to hit an un-priced node mid-recursion. Capped at
    MAX_DEPTH, same as every other recursive walk in this file (build_
    material_tree's single-root version of this same walk). Visited-set
    de-duplication means a shared base material (T1 minerals, common
    components) is only ever expanded once regardless of how many parents
    reach it - the walk is breadth-first specifically so a type_id already
    visited at a shallower depth is never re-queued at a deeper one."""
    visited: set[int] = set()
    frontier: set[int] = set(seed_type_ids)
    depth = 0
    while frontier and depth < MAX_DEPTH:
        visited |= frontier
        next_frontier: set[int] = set()
        for type_id in frontier:
            _activity, bp = classify_activity(type_id)
            if bp is None:
                continue
            blueprint_id, activity_id, _product_qty = bp
            for material_id, _base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
                if material_id not in visited:
                    next_frontier.add(material_id)
        frontier = next_frontier
        depth += 1
    return visited


def build_material_tree(type_id: int, quantity: float, cfg: ProductionConfig, home: dict, jita: dict,
                         selected_decryptors: dict[int, str], t2_memo: dict[int, tuple[float, float, Optional[str]]],
                         depth: int = 0) -> dict:
    """Recursive, hierarchical material tree for one root item - shows the
    FULL recipe (Invention -> Components -> ... -> base minerals) exactly as
    the blueprint defines it, unlike the flat Bauliste/Buy List (which pools
    demand across every stock target and only expands materials for items
    the buy-vs-build economics actually decided to build - see _expand_all's
    docstring for why pooling matters there). This is deliberately the
    opposite shape: one item, its own tree, regardless of whether building it
    would currently be economical - the point is *exploring* a recipe, not
    planning a shopping list.

    Same ME/decryptor logic as everywhere else (_activity_mods/_tech_ii_mods)
    so the quantities shown here can't drift from what the Bauliste itself
    would compute for the same item. Stops at a bought (no blueprint) leaf,
    or at MAX_DEPTH (matches _unit_cost's own recursion cap - a real BOM
    never legitimately goes this deep, so hitting it means a bad blueprint
    reference, not a genuinely deeper tree)."""
    sde_type = storage.get_sde_type(type_id)
    type_name = sde_type[2] if sde_type else str(type_id)
    activity, bp = classify_activity(type_id)

    node = {
        "type_id": type_id, "type_name": type_name, "quantity": quantity,
        "activity": activity if bp else "Buy", "decryptor": None, "children": [],
    }
    if bp is None or depth >= MAX_DEPTH:
        return node

    blueprint_id, activity_id, product_qty = bp
    if activity == "Tech II":
        material_mult, _, decryptor_name = _tech_ii_mods(type_id, blueprint_id, activity_id, cfg, home, jita,
                                                           selected_decryptors, t2_memo)
        node["decryptor"] = decryptor_name
    else:
        material_mult, _, _ = _activity_mods(activity, type_id, cfg, {}, blueprint_id)

    materials = storage.get_blueprint_materials(blueprint_id, activity_id)
    runs = math.ceil(quantity / product_qty) if product_qty > 0 else 0
    for material_id, base_qty in materials:
        material_qty = _material_qty(base_qty, material_mult, runs)
        node["children"].append(
            build_material_tree(material_id, material_qty, cfg, home, jita, selected_decryptors, t2_memo, depth + 1)
        )
    return node


def _direct_material_mult(type_id: int, activity: str, decryptor_name: Optional[str],
                           cfg: ProductionConfig, blueprint_id: Optional[int] = None) -> float:
    """Material multiplier (ME) for `type_id`'s own blueprint, for logistics
    purposes - independent of live prices, unlike _tech_ii_mods (which also
    has to pick the best decryptor *economically*): here the decryptor is
    already decided (BuildJobEntry.decryptor, from the last computed
    Bauliste), so this just looks up its ME bonus directly
    (constants.DECRYPTORS) instead of re-running the pricing pipeline for a
    value that never actually depended on it. Falls back to the flat
    activity baseline (_activity_mods, which itself prefers your owned BPO's
    real ME if `blueprint_id` is given and you own it) for Tech I/Reaction,
    or Tech II with no decryptor decided (no invention recipe found - mirrors
    _tech_ii_mods' own fallback)."""
    if activity == "Tech II" and decryptor_name is not None and decryptor_name in DECRYPTORS:
        structure_profile = _structure_profile("Tech II", type_id)
        structure_type, rig_tier = _structure_rig(structure_profile, cfg)
        security_multiplier = _security_multiplier_for(structure_profile, cfg)
        _, me_mult, _ = structure_rig_multiplier(structure_type, rig_tier, security_multiplier)
        return (1 - DECRYPTORS[decryptor_name].me_bonus / 100) * me_mult
    material_mult, _, _ = _activity_mods(activity, type_id, cfg, {}, blueprint_id)
    return material_mult


def _category_material_demand(build_list: list[BuildJobEntry], category_locations: dict[str, int],
                               cfg: ProductionConfig) -> dict[tuple[str, int], float]:
    """Shared by logistics_status and distribution_recommendations: how much
    of each *direct* material (one level only, not the full recursive chain
    _expand_all walks) is needed for every currently-planned job in each
    category that has an assigned location. Categories with no assigned
    location are skipped entirely (nothing to net against either way)."""
    demand: dict[tuple[str, int], float] = {}
    for entry in build_list:
        category = entry.job_category
        if category is None or category not in category_locations:
            continue
        activity, bp = classify_activity(entry.type_id)
        if bp is None:
            continue
        blueprint_id, activity_id, _ = bp
        material_mult = _direct_material_mult(entry.type_id, activity, entry.decryptor, cfg, blueprint_id)
        for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
            qty = _material_qty(base_qty, material_mult, entry.job_runs)
            if qty > 0:
                key = (category, material_id)
                demand[key] = demand.get(key, 0.0) + qty
    return demand


def logistics_status(build_list: list[BuildJobEntry], cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[LogisticsRow]:
    """Per job_category, how much of each direct material is needed for every
    currently-planned job in that category, netted against what's actually
    sitting at the structure the user assigned that category to (Logistik
    tab, storage.job_category_locations) - not stock *anywhere* the way
    _current_stock's corp-wide default works, since the whole point here is
    "is it at *this specific* structure". Reads `build_list` from an already-
    computed plan rather than recomputing one - the Logistik tab doesn't need
    its own ESI/pricing round-trip, just the already-synced asset snapshot
    (storage.esi_stock_at_location, a plain DB read) against whatever
    Bauliste is already on screen.

    A row with missing > 0 also gets a "pull from" hint (GitHub issue #4) -
    the configured warehouse (cfg.distribution_source_location_id, falling
    back to home_location_id - "the home market acts as the central
    warehouse" by default) if it has any stock, else whichever *other*
    configured category location currently has surplus (stock beyond its
    own demand), largest surplus first. Purely informational/independent
    per row - unlike distribution_recommendations below, this doesn't need
    to track stock already "claimed" by an earlier row, since it's just a
    hint for the user to read, not a set of recommendations that must sum
    to no more than what's actually there."""
    category_locations = storage.load_category_locations()
    demand = _category_material_demand(build_list, category_locations, cfg)
    warehouse_location_id = cfg.distribution_source_location_id or cfg.home_location_id
    other_locations_by_category = {
        category: sorted({loc for cat, loc in category_locations.items() if cat != category})
        for category in category_locations
    }

    rows = []
    for (category, material_id), needed in demand.items():
        location_id = category_locations[category]
        available = storage.esi_stock_at_location(material_id, location_id)
        missing = max(0.0, needed - available)
        sde_type = storage.get_sde_type(material_id)
        name = sde_type[2] if sde_type else str(material_id)

        pull_from_location_id = None
        pull_from_available = None
        if missing > 0:
            if warehouse_location_id is not None and warehouse_location_id != location_id:
                warehouse_stock = storage.esi_stock_at_location(material_id, warehouse_location_id)
                if warehouse_stock > 0:
                    pull_from_location_id, pull_from_available = warehouse_location_id, warehouse_stock
            if pull_from_location_id is None:
                for other_location_id in other_locations_by_category[category]:
                    if other_location_id == warehouse_location_id:
                        continue
                    other_category = next(c for c, loc in category_locations.items() if loc == other_location_id)
                    other_demand = demand.get((other_category, material_id), 0.0)
                    stock = storage.esi_stock_at_location(material_id, other_location_id)
                    surplus = max(0.0, stock - other_demand)
                    if surplus > 0 and (pull_from_available is None or surplus > pull_from_available):
                        pull_from_location_id, pull_from_available = other_location_id, surplus

        rows.append(LogisticsRow(
            category=category, location_id=location_id, type_id=material_id, type_name=name,
            needed=needed, available=available, missing=missing,
            pull_from_location_id=pull_from_location_id, pull_from_available=pull_from_available,
        ))
    rows.sort(key=lambda r: (r.category, -r.missing))
    return rows


def distribution_recommendations(build_list: list[BuildJobEntry],
                                  cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[DistributionRow]:
    """What to move from the configured distribution source (cfg.
    distribution_source_location_id, falling back to home_location_id - "the
    home market acts as the central warehouse" by default, GitHub issue #4)
    to whichever category stations are currently short of it. When several
    categories are short on the same material and the source can't cover all
    of them, the largest shortfall is covered first (source stock is
    limited) - consistent with logistics_status' own -missing sort.

    Whatever the warehouse still can't cover after that is filled from other
    category locations' surplus (stock beyond their own demand), largest
    surplus first, same "warehouse first, then surplus" priority as
    logistics_status' own pull-from hint. Unlike that hint (informational,
    independent per row), the recommendations here are a commitment - two
    categories short of the same material can't both be recommended to pull
    more than a shared surplus location actually has, so surplus_remaining
    tracks what's already been committed to an earlier category in this same
    material's loop and decrements as it's consumed (confirmed real bug in
    an earlier attempt at this: recomputing each category's candidate
    surplus independently, straight off the unmodified DB stock figure,
    let two categories each get recommended the same units).

    Returns [] if no distribution source is configured at all."""
    source_location_id = cfg.distribution_source_location_id or cfg.home_location_id
    if source_location_id is None:
        return []

    category_locations = storage.load_category_locations()
    demand = _category_material_demand(build_list, category_locations, cfg)

    shortfalls_by_material: dict[int, list[tuple[str, float]]] = {}
    for (category, material_id), needed in demand.items():
        location_id = category_locations[category]
        if location_id == source_location_id:
            continue  # already sourced locally, nothing to move
        available = storage.esi_stock_at_location(material_id, location_id)
        missing = max(0.0, needed - available)
        if missing > 0:
            shortfalls_by_material.setdefault(material_id, []).append((category, missing))

    rows = []
    for material_id, shortfalls in shortfalls_by_material.items():
        remaining_from_warehouse = storage.esi_stock_at_location(material_id, source_location_id)
        sde_type = storage.get_sde_type(material_id)
        name = sde_type[2] if sde_type else str(material_id)

        # Phase 1: cover as much as possible from the warehouse, largest
        # shortfall first. Whatever's left per category (0 if the warehouse
        # fully covered it) carries into phase 2.
        still_needed: dict[str, float] = {}
        for category, missing in sorted(shortfalls, key=lambda x: -x[1]):
            move_qty = min(missing, remaining_from_warehouse) if remaining_from_warehouse > 0 else 0.0
            if move_qty > 0:
                remaining_from_warehouse -= move_qty
                rows.append(DistributionRow(
                    type_id=material_id, type_name=name, from_location_id=source_location_id,
                    to_category=category, to_location_id=category_locations[category], quantity=move_qty,
                ))
            left = missing - move_qty
            if left > 0:
                still_needed[category] = left

        # Phase 2: whatever the warehouse couldn't cover, pull from other
        # category locations' surplus - largest remaining shortfall first,
        # largest remaining surplus first within that.
        surplus_remaining: dict[int, float] = {}
        for category, needed_qty in sorted(still_needed.items(), key=lambda x: -x[1]):
            candidate_ids = []
            for other_category, other_location_id in category_locations.items():
                if other_location_id == source_location_id or other_location_id == category_locations[category]:
                    continue
                if other_location_id not in surplus_remaining:
                    other_demand = demand.get((other_category, material_id), 0.0)
                    other_stock = storage.esi_stock_at_location(material_id, other_location_id)
                    surplus_remaining[other_location_id] = max(0.0, other_stock - other_demand)
                candidate_ids.append(other_location_id)
            for loc_id in sorted(candidate_ids, key=lambda l: -surplus_remaining[l]):
                if needed_qty <= 0:
                    break
                avail = surplus_remaining[loc_id]
                if avail <= 0:
                    continue
                move_qty = min(needed_qty, avail)
                surplus_remaining[loc_id] -= move_qty
                needed_qty -= move_qty
                rows.append(DistributionRow(
                    type_id=material_id, type_name=name, from_location_id=loc_id,
                    to_category=category, to_location_id=category_locations[category], quantity=move_qty,
                ))

    rows.sort(key=lambda r: r.quantity, reverse=True)
    return rows


def invention_logistics(invention_list: list[InventionNeedRow],
                         cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[LogisticsRow]:
    """Datacores/decryptors/T1 BPC runs needed vs. what's sitting at the
    configured invention station (cfg.invention_location_id - GitHub issue
    #9), reusing LogisticsRow's existing "needed vs available at one
    location" shape. Needs recommended_invention_runs (not bpcs_needed)
    *runs* of the T1 blueprint itself (GitHub issue #14, previously
    reversed): one invention attempt consumes exactly one *run* from a T1
    BPC, whether that attempt succeeds or fails (confirmed against
    wiki.eveuniversity.org/Invention: "this BPC will be returned to you
    with one fewer run remaining on it. If it only has one run remaining,
    it will be consumed.") - a max-run copy is worth many attempts, a 1-run
    copy is worth exactly one, which is exactly why players don't always
    keep max-run copies on hand for this (the report that caught the
    original GitHub issue #14 confusion: "not always are max run copies
    used"). bpcs_needed (ceil(runs_needed / output_runs), the number of
    *successful* inventions needed) undercounts real T1-run consumption,
    since every failed attempt burns a run too without producing anything -
    recommended_invention_runs (ceil(bpcs_needed / probability), the full
    attempt count) is the number already shown on the Invention tab as
    "recommended attempts", so this now matches that number exactly (plus
    whatever overbuild the user has already baked into it upstream).

    Availability for the T1 blueprint itself goes through
    storage.available_blueprint_copies, not the generic esi_stock_at_location
    every other row here uses - a T1 blueprint's BPO and BPC share the exact
    same type_id in EVE's data model (only ever distinguished by which
    ESI/asset field they come from), so a plain esi_stock_at_location call
    would count an owned BPO as if it were a usable invention input too.
    available_blueprint_copies filters to actual copies only (character_
    blueprints/corp_blueprints' own quantity == -2 sentinel) and - despite
    its own name - sums their remaining *runs*, not the number of copies
    (confirmed real bug, 2026-08-30: it used to COUNT(*) copies instead,
    reporting a single 300-run T1 copy as "1 available" instead of "300" -
    see that function's own docstring for the full fix).

    Decryptor/datacore demand was already right - each attempt consumes one
    of each regardless of outcome, so it already scaled with
    recommended_invention_runs. Those stay on esi_stock_at_location - plain
    items, no BPO/BPC ambiguity to worry about."""
    if cfg.invention_location_id is None:
        return []

    demand: dict[int, float] = {}
    t1_blueprint_type_ids: set[int] = set()
    for need in invention_list:
        if need.recommended_invention_runs <= 0:
            continue
        demand[need.t1_blueprint_type_id] = demand.get(need.t1_blueprint_type_id, 0.0) + need.recommended_invention_runs
        t1_blueprint_type_ids.add(need.t1_blueprint_type_id)

        decryptor = DECRYPTORS.get(need.decryptor)
        # decryptor.type_id == 0 is the "None" entry's own sentinel (no
        # decryptor used) - not a real EVE item. SDE type_id 0 happens to be
        # named "#System", so without this guard it showed up as a bogus
        # demand row (GitHub issue #13).
        if decryptor is not None and decryptor.type_id != 0:
            demand[decryptor.type_id] = demand.get(decryptor.type_id, 0.0) + need.recommended_invention_runs

        recipe = storage.get_invention_recipe(need.t1_blueprint_type_id)
        for datacore_id, datacore_qty in (recipe.get("datacores", []) if recipe else []):
            demand[datacore_id] = demand.get(datacore_id, 0.0) + datacore_qty * need.recommended_invention_runs

    rows = []
    for type_id, needed in demand.items():
        if type_id in t1_blueprint_type_ids:
            available = storage.available_blueprint_copies(type_id, cfg.invention_location_id)
        else:
            available = storage.esi_stock_at_location(type_id, cfg.invention_location_id)
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        rows.append(LogisticsRow(
            category="Invention", location_id=cfg.invention_location_id, type_id=type_id, type_name=name,
            needed=needed, available=available, missing=max(0.0, needed - available),
        ))
    rows.sort(key=lambda r: -r.missing)
    return rows


def t1_bpc_invention_needs(invention_list: list[InventionNeedRow],
                            cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[T1BpcInventionNeedRow]:
    """GitHub issue #114: invention_logistics above mixes T1 BPCs, decryptors
    and datacores into one flat LogisticsRow list, which makes "how many BPC
    runs am I short, and do I even own the BPO to print more" hard to see
    for the thing that actually matters most - the T1 BPC itself. This is
    the T1-blueprint-only slice of the exact same demand accumulation
    invention_logistics already does (same recommended_invention_runs-per-
    attempt reasoning - see that function's own docstring), plus a
    BPO-presence check invention_logistics has no reason to compute (it
    only cares about copies, the actual invention input)."""
    if cfg.invention_location_id is None:
        return []

    needed_by_t1: dict[int, int] = {}
    for need in invention_list:
        if need.recommended_invention_runs <= 0:
            continue
        needed_by_t1[need.t1_blueprint_type_id] = (
            needed_by_t1.get(need.t1_blueprint_type_id, 0) + need.recommended_invention_runs)

    rows = []
    for type_id, needed in needed_by_t1.items():
        available = int(storage.available_blueprint_copies(type_id, cfg.invention_location_id))
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        rows.append(T1BpcInventionNeedRow(
            type_id=type_id, name=name, needed=needed, available=available,
            missing=max(0, needed - available),
            bpo_present=storage.has_bpo_at_location(type_id, cfg.invention_location_id),
        ))
    rows.sort(key=lambda r: -r.missing)
    return rows
