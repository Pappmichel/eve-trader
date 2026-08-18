"""Hand-curated business constants - small, curated multipliers (not SDE
data), same spirit as shortlist.py's `ALL_DECISIONS`.

ME/TE is modeled in three independently-stacking layers (all multiplicative,
matching how EVE actually combines them):

1. ACTIVITY_MODS - the blueprint/BPC's *own* research level, activity-specific:
   - Tech I: the entry here (0.90/0.80, i.e. "assumes perfect ME/TE" -> BPO
     researched to ME10/TE20, the real EVE research caps) is only the
     *fallback* baseline used when you don't own that specific BPO (or
     haven't ESI-synced it) - production/engine.py's _activity_mods prefers
     your actual owned BPO's real ME/TE (storage.get_owned_bpo_best_me_te)
     when available. Deliberately a pure research-level multiplier with no
     structure/rig bonus baked in, since STRUCTURE_TYPES/RIG_TIERS below are
     applied separately on top - baking a bonus in here too would double-
     count it.
   - Tech II *and* Tech III: both resolved per item from their BPC's decryptor
     (see production/invention.py/engine.py's _tech_ii_mods) - Tech III uses
     the exact same activity_id=8 Invention as Tech II (CCP removed the old
     relic-based "Reverse Engineering" mechanic years ago, confirmed against
     real SDE data), so classify_activity() routes both through this same
     "Tech II" label whenever an item is genuinely invented (metaLevel>=2 *or*
     a real invention recipe exists - metaLevel alone misses some Tech III
     subsystems, see classify_activity's docstring). There used to be a dead,
     wrong flat "Tech III" entry here that was never actually reachable. The
     entry here is only the shared fallback baseline (a "None"-decryptor BPC:
     ME2/TE4, i.e. 0.98/0.96) used when neither an invention recipe nor a
     manually-selected decryptor is found for an item.
   - Reaction: reaction formulas have no ME/TE research in EVE - base 1.0/1.0,
     all reduction comes from STRUCTURE_TYPES/RIG_TIERS instead.
   - Faction: confirmed with the user (2026-07-16) - Faction blueprints are
     always ME0/TE0 in real EVE and cannot be researched or changed at all
     (unlike Tech I, which can be researched up to ME10/TE20), so unlike
     Tech I, this is *not* a fallback "assumes perfect research" baseline -
     it's the one and only value a Faction BPO can ever have, no owned-BPO
     lookup needed or performed (see _activity_mods).
2. STRUCTURE_TYPES - the base bonus of the structure you build in.
3. RIG_TIERS - the *additional* bonus from whichever rig tier is installed.

job_cost_rate is untouched by any of this - it's a system-cost-index/structure-
tax question, unrelated to ME/TE (see the config.py docstring / Settings tab).
"""
from __future__ import annotations

from dataclasses import dataclass

# SDE industryActivity activity IDs used by this tool.
ACTIVITY_MANUFACTURING = 1
ACTIVITY_REACTION = 11
ACTIVITY_COPYING = 5
ACTIVITY_INVENTION = 8

# SDE category_id for the "Ship" category - the only category where packaged
# (repackaged/cargo) volume commonly differs from flight volume; see
# engine._haul_volume.
SHIP_CATEGORY_ID = 6

# SDE metaGroupID (Fuzzwork invMetaTypes.csv, see production/sde.py/storage.
# get_sde_type) - used by engine.classify_activity to label a non-invented
# item by its real SDE meta group instead of lumping everything non-Tech-II
# into plain "Tech I", purely for the user's own tracking/filtering (see that
# docstring: mechanically identical to Tech I either way - same ME0/TE0,
# non-researchable treatment as Faction, see ACTIVITY_MODS below).
# FACTION_META_GROUP_ID confirmed live 2026-07-16 (Machariel/Nestor both map
# to 4). OFFICER/STORYLINE confirmed live 2026-07-17 against this app's own
# cached SDE: both metaGroupIDs have real, blueprint-backed, market-priceable
# products (e.g. Officer: "Zorya's Light Entropic Disintegrator", a genuine
# 3-material Abyssal-tier blueprint, not a vestigial one; Storyline:
# "Purloined Sansha Data Analyzer" from a "Mangled Sansha Data Analyzer"
# conversion blueprint) - not just SDE rows that happen to exist but are
# never reachable. DEADSPACE_META_GROUP_ID has zero blueprint-backed products
# in the current SDE (deadspace modules are drop-only, no BPO ever existed
# for them) - added anyway for a future SDE that might add one, since
# classify_activity's `bp is None` gate already makes this branch a no-op
# until then, at zero cost. (1=Tech I, 2=Tech II are not split out here -
# those already have their own dedicated code paths, not a meta-group check.)
FACTION_META_GROUP_ID = 4
STORYLINE_META_GROUP_ID = 3
OFFICER_META_GROUP_ID = 5
DEADSPACE_META_GROUP_ID = 6


@dataclass(frozen=True)
class ActivityMods:
    material_multiplier: float   # fraction of base material quantity needed (blueprint/BPC's own ME)
    time_multiplier: float       # fraction of base job time (blueprint/BPC's own TE)
    job_cost_rate: float         # job/facility tax as a fraction of the modeled material value


ACTIVITY_MODS: dict[str, ActivityMods] = {
    "Reaction": ActivityMods(material_multiplier=1.00, time_multiplier=1.00, job_cost_rate=0.1025),
    "Tech I": ActivityMods(material_multiplier=0.90, time_multiplier=0.80, job_cost_rate=0.077742),
    # "Faction" (classify_activity - Machariel/Nestor and similar): confirmed
    # with the user (2026-07-16) - Faction blueprints are always ME0/TE0 in
    # real EVE, not researchable at all, unlike Tech I's ME10/TE20 cap - 1.00/
    # 1.00 (no reduction) is the one and only correct value, not a fallback
    # assumption. job_cost_rate stays the same shared flat-fallback rate as
    # Tech I - job cost is a facility/system-index question, unrelated to
    # ME/TE research (see module docstring).
    "Faction": ActivityMods(material_multiplier=1.00, time_multiplier=1.00, job_cost_rate=0.077742),
    # Same reasoning/treatment as "Faction" above (ME0/TE0, non-researchable,
    # shared flat job_cost_rate) - see FACTION_META_GROUP_ID's comment for why
    # these are label-only distinctions, mechanically identical to Faction.
    "Storyline": ActivityMods(material_multiplier=1.00, time_multiplier=1.00, job_cost_rate=0.077742),
    "Officer": ActivityMods(material_multiplier=1.00, time_multiplier=1.00, job_cost_rate=0.077742),
    "Deadspace": ActivityMods(material_multiplier=1.00, time_multiplier=1.00, job_cost_rate=0.077742),
    "Tech II": ActivityMods(material_multiplier=0.98, time_multiplier=0.96, job_cost_rate=0.07813),
}


@dataclass(frozen=True)
class StructureType:
    cost_multiplier: float
    me_multiplier: float
    te_multiplier: float


# Confirmed against wiki.eveuniversity.org "Refinery" (job fee/material/
# duration bonuses) - job fee only applies to Engineering Complexes, never to
# Refineries (Athanor/Tatara get no cost bonus at all, hence cost_multiplier=1.0).
# Athanor/Tatara's own material_multiplier stays 1.0 too - their "refinery
# yield" stat (2%/4%) is a *reprocessing* bonus, confirmed not to apply to
# reactions; reactions only get a structure te_multiplier ("reaction job
# duration reduction") plus, if fitted, reactor-specific ME *and* TE rigs
# (see RIG_TIERS/structure_rig_multiplier - both exist in EVE, e.g. "Standup
# M-Set Composite Reactor Time Efficiency I/II"). Re-confirmed 2026-08-18
# (wiki.eveuniversity.org/Refinery's own role-bonus table): only the Tatara
# has a reaction-duration bonus (-25%) - the Athanor has none at all, a
# previous -3% entry here didn't correspond to any real EVE attribute.
STRUCTURE_TYPES: dict[str, StructureType] = {
    "Citadel (no bonuses)": StructureType(1.00, 1.00, 1.00),
    "Raitaru (M Engineering Complex)": StructureType(0.97, 0.99, 0.85),
    "Azbel (L Engineering Complex)": StructureType(0.96, 0.99, 0.80),
    "Sotiyo (XL Engineering Complex)": StructureType(0.95, 0.99, 0.70),
    "Athanor (M Refinery)": StructureType(1.00, 1.00, 1.00),
    "Tatara (L Refinery)": StructureType(1.00, 1.00, 0.75),
}


@dataclass(frozen=True)
class RigTier:
    me_bonus: float   # additional ME% reduction (before security scaling), stacks with structure's me_multiplier
    te_bonus: float   # additional TE% reduction (before security scaling), stacks with structure's te_multiplier


# Base (highsec, 1x) rig bonuses - confirmed via rig dogma attributes (e.g.
# ESI/everef type 43867 "Standup M-Set Advanced Component Manufacturing
# Material Efficiency I" = -2%, type 43866 (T2) = -2.4%; type 43876 "Standup
# M-Set Structure Manufacturing Time Efficiency I" = -20%, its T2 counterpart
# -24%). Real EVE mechanic: these bonuses scale with the security status of
# the system the structure sits in - see rig_security_multiplier below.
RIG_TIERS: dict[str, RigTier] = {
    "No Rig": RigTier(0.0, 0.0),
    "T1-Rig": RigTier(0.02, 0.20),
    "T2-Rig": RigTier(0.024, 0.24),
}


def rig_security_multiplier(security_status: float | None, is_reaction: bool = False) -> float:
    """Real EVE mechanic (confirmed via rig dogma attributes): rig ME/TE
    bonuses scale with the security status of the system the structure sits
    in - but Engineering Complex rigs and Refinery/reactor rigs use two
    *different* tables (confirmed via EVE Ref dogma attributes on e.g. type
    46485 "Standup M-Set Composite Reactor Time Efficiency II": "Low Security
    Bonus Multiplier: 1x", "Nullsec and Wormhole Bonus Multiplier: 1.1x", and
    "Banned in High Sec Space: true" - reactor rigs cannot be fitted in
    highsec at all, since reactions themselves can't run there):
    - Engineering Complex (`is_reaction=False`): 1x highsec (>=0.5), 1.9x
      lowsec (0.0-0.45), 2.1x null-sec/wormhole (<=0.0).
    - Refinery/reactor (`is_reaction=True`): no highsec case (hard-banned in
      game); 1x lowsec, 1.1x null-sec/wormhole - a much smaller nullsec bonus
      than the Engineering Complex table.
    Unknown security (e.g. system not yet resolved) assumes the lowest-bonus
    case for the relevant table (1x either way) rather than over-crediting an
    unverified location."""
    if is_reaction:
        return 1.1 if (security_status is not None and security_status <= 0.0) else 1.0
    if security_status is None:
        return 1.0
    if security_status >= 0.5:
        return 1.0
    if security_status > 0.0:
        return 1.9
    return 2.1


def structure_rig_multiplier(structure_type: str, rig_tier: str, security_multiplier: float = 1.0) -> tuple[float, float, float]:
    """Combines STRUCTURE_TYPES + RIG_TIERS into one (cost, ME, TE) multiplier
    triple, applied on top of ACTIVITY_MODS/decryptor-derived base values.
    `security_multiplier` scales the rig's bonus (see rig_security_multiplier -
    pass its is_reaction=True result for reactions). Reactor Time Efficiency
    rigs are a real EVE item (e.g. "Standup M-Set Composite Reactor Time
    Efficiency I/II", confirmed via dogma attributes) - both me_bonus and
    te_bonus always apply here regardless of activity; there is no reaction
    special-case."""
    structure = STRUCTURE_TYPES[structure_type]
    rig = RIG_TIERS[rig_tier]
    me_rig_bonus = rig.me_bonus * security_multiplier
    te_rig_bonus = rig.te_bonus * security_multiplier
    return (
        structure.cost_multiplier,
        structure.me_multiplier * (1 - me_rig_bonus),
        structure.te_multiplier * (1 - te_rig_bonus),
    )


@dataclass(frozen=True)
class Decryptor:
    type_id: int              # 0 = "no decryptor" (not a real EVE item)
    probability_multiplier: float
    run_bonus: int
    me_bonus: int              # the resulting BPC's *absolute* ME stat (not a delta) - material
                                # multiplier = 1 - me_bonus/100. "None" = 2, matching EVE's real
                                # no-decryptor invention baseline (ME2/TE4).
    te_bonus: int               # same, for time: multiplier = 1 - te_bonus/100


# Official CCP decryptor stats (sourced from the SDE - kept as a hardcoded
# constant here since it's a fixed, tiny (10-row) table, same as ACTIVITY_MODS
# above).
DECRYPTORS: dict[str, Decryptor] = {
    "None": Decryptor(0, 1.0, 0, 2, 4),
    "Accelerant": Decryptor(34201, 1.2, 1, 4, 14),
    "Attainment": Decryptor(34202, 1.8, 4, 1, 8),
    "Augmentation": Decryptor(34203, 0.6, 9, 0, 6),
    "Parity": Decryptor(34204, 1.5, 3, 3, 2),
    "Process": Decryptor(34205, 1.1, 0, 5, 10),
    "Symmetry": Decryptor(34206, 1.0, 2, 3, 12),
    "Optimized Attainment": Decryptor(34207, 1.9, 2, 3, 2),
    "Optimized Augmentation": Decryptor(34208, 0.9, 7, 4, 4),
}


# Fixed CCP-wide job-fee surcharge (confirmed against wiki.eveuniversity.org/
# Manufacturing: "SCC surcharge: Fixed at 4%") - unlike facility_tax_rate
# (production/config.py), a player structure's owner cannot change this, so
# it's a constant here rather than a config field.
SCC_SURCHARGE_RATE = 0.04

# --------------------------------------------------------------------- job cost
# The system cost index used for job_cost_rate (see production/engine.py) is
# split by *what's being built*, not by Tech I/II/III/Reaction: reactions and
# these specific "component" item groups are typically built at a dedicated,
# cheap-cost-index null-sec system separate from everything else - see
# production/config.py component_system_*/manufacturing_system_*.
#
# COMPONENT_GROUP_IDS = every SDE invGroup covered by the two ME rigs "Standup
# M-Set Advanced Component Manufacturing Material Efficiency I" and "Standup
# M-Set Basic Capital Component Manufacturing Material Efficiency I" (per
# their ESI descriptions: Tech 2 components, Tech 2 capital components, Tools,
# Data Interfaces, Tech 3 components, and Basic Capital Components).
COMPONENT_GROUP_IDS: frozenset = frozenset({
    332,   # Tool
    334,   # Construction Components (Tech 2 components, e.g. Fernite Carbide Composite Armor Plate)
    716,   # Data Interfaces
    873,   # Capital Construction Components (Basic Capital Components)
    913,   # Advanced Capital Construction Components (Tech 2 capital components)
    964,   # Hybrid Tech Components (Tech 3 components)
})

# --------------------------------------------------------------- job category
# Purely a "where do I start this job" display grouping for the Bauliste (see
# engine.job_category) - does *not* feed ME/TE/job-cost (that stays the
# existing 3-way reaction/component/manufacturing split, see
# COMPONENT_GROUP_IDS/_structure_profile above). Confirmed with the user
# which SDE ship groups belong to which size tier and which item categories
# get their own bucket.

# The two COMPONENT_GROUP_IDS groups (873, 913) that are specifically Capital
# components - the rest of COMPONENT_GROUP_IDS is "Advanced Components".
CAPITAL_COMPONENT_GROUP_IDS: frozenset = frozenset({873, 913})
ADVANCED_COMPONENT_GROUP_IDS: frozenset = COMPONENT_GROUP_IDS - CAPITAL_COMPONENT_GROUP_IDS

# Every value engine.job_category() can return, in the Logistik tab's display
# order - the fixed set of "which structure do I assign this job category to"
# buckets a user can configure a location_id for.
JOB_CATEGORIES: tuple[str, ...] = (
    "Reactions",
    "Advanced Components",
    "Capital Components",
    "Equipment",
    "Drones & Ammunition",
    "Small Ships",
    "Medium Ships",
    "Large Ships",
    "Capital Ship",
)

# T3 Strategic Cruiser subsystems (Core/Defensive/Offensive/Propulsion) count
# as Medium Ships, not Equipment, per the user.
SUBSYSTEM_GROUP_IDS: frozenset = frozenset({958, 954, 956, 957})

# Ship hull size tiers, by SDE invGroup - every *published* group under
# category_id=6 (Ship) as of this writing. A handful of rare/vanity/
# non-manufacturable groups (Capsule, Corvette, Shuttle, Special Edition
# Yachts) are included with a best-guess size since they're harmless even if
# never actually built; genuinely ambiguous groups (Orca/Industrial Command
# Ship) were confirmed with the user.
SHIP_SIZE_GROUP_IDS: dict[str, frozenset] = {
    "Small Ships": frozenset({
        324,   # Assault Frigate
        1534,  # Command Destroyer
        237,   # Corvette
        830,   # Covert Ops
        420,   # Destroyer
        893,   # Electronic Attack Ship
        1283,  # Expedition Frigate
        25,    # Frigate
        831,   # Interceptor
        541,   # Interdictor
        1527,  # Logistics Frigate
        1022,  # Prototype Exploration Ship
        31,    # Shuttle
        834,   # Stealth Bomber
        1305,  # Tactical Destroyer
        29,    # Capsule
    }),
    "Medium Ships": frozenset({
        1201,  # Attack Battlecruiser
        1202,  # Blockade Runner
        419,   # Combat Battlecruiser
        906,   # Combat Recon Ship
        540,   # Command Ship
        26,    # Cruiser
        380,   # Deep Space Transport
        543,   # Exhumer
        4902,  # Expedition Command Ship
        1972,  # Flag Cruiser
        833,   # Force Recon Ship
        28,    # Hauler
        358,   # Heavy Assault Cruiser
        894,   # Heavy Interdiction Cruiser
        832,   # Logistics
        463,   # Mining Barge
        963,   # Strategic Cruiser
    }),
    "Large Ships": frozenset({
        27,    # Battleship
        898,   # Black Ops
        513,   # Freighter
        941,   # Industrial Command Ship (Orca)
        902,   # Jump Freighter
        900,   # Marauder
    }),
    "Capital Ship": frozenset({
        883,   # Capital Industrial Ship (Rorqual)
        547,   # Carrier
        5120,  # Command Carrier (legacy name)
        485,   # Dreadnought
        1538,  # Force Auxiliary
        4594,  # Lancer Dreadnought
        659,   # Supercarrier
        30,    # Titan
    }),
}

# SDE category_ids for the "Drones & Ammunition" and "Equipment" buckets.
CHARGE_CATEGORY_ID = 8
DRONE_CATEGORY_ID = 18
FIGHTER_CATEGORY_ID = 87
MODULE_CATEGORY_ID = 7

# --------------------------------------------------------------- job slots
# Concurrent industry job slots are governed by skills, independently per job
# category (real EVE mechanic, since the Ascension expansion split them):
# base 1 slot + 1 per level of the base skill + 1 per level of its "Advanced"
# counterpart (max 5 each, so up to 11 slots). Type IDs confirmed against the
# local SDE cache (storage.search_sde_types).
SKILL_MASS_PRODUCTION = 3387
SKILL_ADVANCED_MASS_PRODUCTION = 24625
SKILL_MASS_REACTIONS = 45748
SKILL_ADVANCED_MASS_REACTIONS = 45749
SKILL_LABORATORY_OPERATION = 3406
SKILL_ADVANCED_LABORATORY_OPERATION = 24624

# ESI industryActivity activity_id -> which slot category it draws from.
# No activity_id 7 ("Reverse Engineering") entry - that Ancient-Relic-based
# Tech III mechanic was removed from EVE years ago (see sde.py/invention.py/
# engine.py's own comments on this), so no live ESI industry job can ever
# report it.
ACTIVITY_SLOT_CATEGORY: dict[int, str] = {
    1: "manufacturing",   # Manufacturing
    11: "reaction",       # Reaction
    3: "science",         # Time Efficiency Research
    4: "science",         # Material Efficiency Research
    5: "science",         # Copying
    8: "science",         # Invention
}

ACTIVITY_JOB_LABELS: dict[int, str] = {
    1: "Manufacturing",
    11: "Reaction",
    3: "TE Research",
    4: "ME Research",
    5: "Copying",
    8: "Invention",
}

SLOT_CATEGORY_LABELS: dict[str, str] = {
    "manufacturing": "Manufacturing",
    "reaction": "Reactions",
    "science": "Science",
}


def job_slots_from_skills(skill_levels: dict[int, int]) -> dict[str, int]:
    """skill_levels: {skill_type_id: active_skill_level}. Returns
    {"manufacturing"|"reaction"|"science": total_slot_count}."""
    def slots(base_id: int, advanced_id: int) -> int:
        return 1 + skill_levels.get(base_id, 0) + skill_levels.get(advanced_id, 0)

    return {
        "manufacturing": slots(SKILL_MASS_PRODUCTION, SKILL_ADVANCED_MASS_PRODUCTION),
        "reaction": slots(SKILL_MASS_REACTIONS, SKILL_ADVANCED_MASS_REACTIONS),
        "science": slots(SKILL_LABORATORY_OPERATION, SKILL_ADVANCED_LABORATORY_OPERATION),
    }
