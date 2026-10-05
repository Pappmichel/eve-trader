"""Game facts for PI that are not in the SDE, each with its source.

Everything that *is* in the SDE (recipes, cycle times, structure CPU/power,
capacities, link attributes, tax bases, volumes) is read from the sde_pi_*
tables instead - see static.py. docs/PI_PLAN.md section 1 has the research
behind every number here.
"""
from __future__ import annotations

# The 8 planet types that can hold a colony, by SDE type id. An explicit
# whitelist on purpose: mapDenormalize also has Shattered (30889) and
# Scorched Barren (73911) planets, and invTypes lists duplicate planet types
# 56018-56024 that have no planets at all (docs/PI_TECHNICAL_DESIGN.md P-06).
PLANET_TYPES: dict[int, str] = {
    11: "Temperate",
    12: "Ice",
    13: "Gas",
    2014: "Oceanic",
    2015: "Lava",
    2016: "Barren",
    2017: "Storm",
    2063: "Plasma",
}
PLANET_TYPE_IDS = frozenset(PLANET_TYPES)

# P0 resources per planet type, by P0 type id. Not in the SDE (planet types
# carry no dogma attributes). Eve-PI and jwebbdev agree independently;
# Barren and Gas checked in game by the user on 2026-10-04 (PI_PLAN 1.3).
AQUEOUS_LIQUIDS = 2268
AUTOTROPHS = 2305
BASE_METALS = 2267
CARBON_COMPOUNDS = 2288
COMPLEX_ORGANISMS = 2287
FELSIC_MAGMA = 2307
HEAVY_METALS = 2272
IONIC_SOLUTIONS = 2309
MICRO_ORGANISMS = 2073
NOBLE_GAS = 2310
NOBLE_METALS = 2270
NON_CS_CRYSTALS = 2306
PLANKTIC_COLONIES = 2286
REACTIVE_GAS = 2311
SUSPENDED_PLASMA = 2308

PLANET_RESOURCES: dict[int, frozenset[int]] = {
    2016: frozenset({AQUEOUS_LIQUIDS, BASE_METALS, CARBON_COMPOUNDS, MICRO_ORGANISMS, NOBLE_METALS}),
    13: frozenset({AQUEOUS_LIQUIDS, BASE_METALS, IONIC_SOLUTIONS, NOBLE_GAS, REACTIVE_GAS}),
    12: frozenset({AQUEOUS_LIQUIDS, HEAVY_METALS, MICRO_ORGANISMS, NOBLE_GAS, PLANKTIC_COLONIES}),
    2015: frozenset({BASE_METALS, FELSIC_MAGMA, HEAVY_METALS, NON_CS_CRYSTALS, SUSPENDED_PLASMA}),
    2014: frozenset({AQUEOUS_LIQUIDS, CARBON_COMPOUNDS, COMPLEX_ORGANISMS, MICRO_ORGANISMS, PLANKTIC_COLONIES}),
    2063: frozenset({BASE_METALS, HEAVY_METALS, NOBLE_METALS, NON_CS_CRYSTALS, SUSPENDED_PLASMA}),
    2017: frozenset({AQUEOUS_LIQUIDS, BASE_METALS, IONIC_SOLUTIONS, NOBLE_GAS, SUSPENDED_PLASMA}),
    11: frozenset({AQUEOUS_LIQUIDS, AUTOTROPHS, CARBON_COMPOUNDS, COMPLEX_ORGANISMS, MICRO_ORGANISMS}),
}

# Command Center CPU (tf) / power (MW) / upgrade ISK per level. Only level 0
# is in the SDE; all levels confirmed in game by the user on 2026-10-04
# (PI_PLAN 1.5). Upgrade cost is the price of reaching that level from the
# one below.
CC_LEVELS: dict[int, tuple[int, int, int]] = {
    0: (1675, 6000, 0),
    1: (7057, 9000, 580_000),
    2: (12136, 12000, 930_000),
    3: (17215, 15000, 1_200_000),
    4: (21315, 17000, 1_500_000),
    5: (25415, 19000, 2_100_000),
}
MAX_CC_LEVEL = 5

# Template/layout rules measured in game by the Eve-PI author (PI_PLAN 1.4).
MIN_PIN_SEPARATION_RAD = 0.012
# In-game exports place structures at 0.01199x rad: 5-decimal coordinates
# cannot hit 0.012 exactly (64 of 410 public community templates). The
# validator accepts that much; the generator keeps its own 5% margin.
PIN_SEPARATION_TOLERANCE_RAD = 2e-5
MAX_ROUTE_STRUCTURES = 7
MAX_EXTRACTOR_HEADS = 10

# Extractor program cycle time by program length (hours): 15 min below 25 h,
# then doubling at 25/50/100/200 h, 4 h up to the 14-day maximum. EVE Uni,
# confirmed in game 2026-10-04 (PI_TECHNICAL_DESIGN 3.5, V-1).
MAX_PROGRAM_HOURS = 14 * 24
PROGRAM_CYCLE_STEPS: tuple[tuple[float, int], ...] = (
    (200.0, 4 * 3600),
    (100.0, 2 * 3600),
    (50.0, 3600),
    (25.0, 1800),
    (0.0, 900),
)
# The program length the per-zone yield defaults refer to (D2).
REFERENCE_PROGRAM_HOURS = 72.0

# Security zones for the yield defaults (D2) and the NPC customs part.
ZONE_HIGHSEC = "highsec"
ZONE_LOWSEC = "lowsec"
ZONE_NULLSEC = "nullsec"
ZONE_WORMHOLE = "wormhole"
ZONES = (ZONE_HIGHSEC, ZONE_LOWSEC, ZONE_NULLSEC, ZONE_WORMHOLE)
WORMHOLE_REGION_MIN, WORMHOLE_REGION_MAX = 11000001, 11000033
POCHVEN_REGION_ID = 10000070
# Jove regions: in the SDE with PI planets, but unreachable (P-63).
UNREACHABLE_REGION_IDS = frozenset({10000004, 10000017, 10000019})

# High-sec NPC customs rate and the Customs Code Expertise reduction per level.
HIGHSEC_NPC_TAX = 0.10
CUSTOMS_CODE_EXPERTISE_PER_LEVEL = 0.01

# SDE categories of PI types (checked against invGroups 2026-10-04):
# 41 Planetary Industry (structures, links), 42 Planetary Resources (P0),
# 43 Planetary Commodities (P1-P4).
PI_CATEGORY_IDS = frozenset({41, 42, 43})

# SDE group ids of PI structures -> kind. Kind is decided by group, never by
# planet type: real templates mix planet types (P-19).
GROUP_EXTRACTOR_HEAD = 1026
GROUP_COMMAND_CENTER = 1027
GROUP_PROCESSOR = 1028
GROUP_STORAGE = 1029
GROUP_SPACEPORT = 1030
GROUP_LINK = 1036
GROUP_ECU = 1063

KIND_COMMAND_CENTER = "command_center"
KIND_ECU = "ecu"
KIND_BASIC = "basic"
KIND_ADVANCED = "advanced"
KIND_HIGH_TECH = "high_tech"
KIND_STORAGE = "storage"
KIND_LAUNCHPAD = "launchpad"
KIND_LINK = "link"
FACTORY_KINDS = (KIND_BASIC, KIND_ADVANCED, KIND_HIGH_TECH)
HUB_KINDS = (KIND_LAUNCHPAD, KIND_STORAGE)

# Dogma attribute ids used by the PI engine.
ATTR_POWER_OUTPUT = 11
ATTR_POWER_LOAD = 15
ATTR_CAPACITY = 38
ATTR_CPU_OUTPUT = 48
ATTR_CPU_LOAD = 49
ATTR_LOGISTICAL_CAPACITY = 1631
ATTR_PLANET_RESTRICTION = 1632
ATTR_POWER_PER_KM = 1633
ATTR_CPU_PER_KM = 1634
ATTR_CPU_LEVEL_MODIFIER = 1635
ATTR_POWER_LEVEL_MODIFIER = 1636
ATTR_IMPORT_TAX = 1638
ATTR_EXPORT_TAX = 1639
ATTR_IMPORT_TAX_BASE = 1640
ATTR_EXPORT_TAX_BASE = 1641
ATTR_EXTRACTION_QUANTITY = 1642
ATTR_PIN_CYCLE_TIME = 1643
ATTR_ECU_DECAY_FACTOR = 1683
ATTR_ECU_NOISE_FACTOR = 1687
ATTR_HEAD_CPU = 1690
ATTR_HEAD_POWER = 1691
# Pseudo attribute: invTypes.basePrice, stored next to the dogma attributes
# because sde_types has no base price column (P-10).
ATTR_BASE_PRICE = -1
# Pseudo attribute: invTypes.capacity (m3). Launchpad/storage capacity is an
# invTypes column in the Fuzzwork dump, not a dogma attribute row.
ATTR_TYPE_CAPACITY = -2

# Attributes kept from dgmTypeAttributes.csv for PI types (sde.py filter).
PI_ATTRIBUTE_IDS = frozenset({
    ATTR_POWER_OUTPUT, ATTR_POWER_LOAD, ATTR_CAPACITY, ATTR_CPU_OUTPUT, ATTR_CPU_LOAD,
    ATTR_LOGISTICAL_CAPACITY, ATTR_PLANET_RESTRICTION, ATTR_POWER_PER_KM, ATTR_CPU_PER_KM,
    ATTR_CPU_LEVEL_MODIFIER, ATTR_POWER_LEVEL_MODIFIER, ATTR_IMPORT_TAX, ATTR_EXPORT_TAX,
    ATTR_IMPORT_TAX_BASE, ATTR_EXPORT_TAX_BASE, ATTR_EXTRACTION_QUANTITY, ATTR_PIN_CYCLE_TIME,
    1644, 1645, ATTR_ECU_DECAY_FACTOR, ATTR_ECU_NOISE_FACTOR, ATTR_HEAD_CPU, ATTR_HEAD_POWER,
})

# Interplanetary Consolidation gives one extra planet per level.
BASE_PLANETS_PER_CHARACTER = 1

# Skill type ids (SDE), for D3 (planets/CC level/customs from ESI skills).
SKILL_INTERPLANETARY_CONSOLIDATION = 2495
SKILL_COMMAND_CENTER_UPGRADES = 2505
SKILL_CUSTOMS_CODE_EXPERTISE = 33467
