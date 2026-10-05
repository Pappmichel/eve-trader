"""Plain dataclasses shared by the PI engine, economics and layout code.

No I/O here and no logic beyond small lookups - see static.py (building
StaticData from SDE rows) and engine.py (capacity/throughput/search).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import constants as C


@dataclass(frozen=True)
class Commodity:
    type_id: int
    name: str
    tier: int                 # 0 = P0 ... 4 = P4, derived from the schematic graph
    volume: float             # m3 per unit (SDE, rounded to 4 decimals - P-09)
    export_tax_base: float    # ISK per unit before the rate (dogma 1641)
    import_tax_base: float    # ISK per unit before the rate and the 0.5 pin factor (dogma 1640)


@dataclass(frozen=True)
class Schematic:
    schematic_id: int
    name: str
    cycle_seconds: int
    output_type_id: int
    output_qty: int
    inputs: tuple[tuple[int, int], ...]   # (type_id, qty per cycle)
    pin_type_ids: frozenset[int]

    @property
    def runs_per_hour(self) -> float:
        return 3600.0 / self.cycle_seconds


@dataclass(frozen=True)
class StructureSpec:
    type_id: int
    name: str
    kind: str                 # constants.KIND_*
    planet_type_id: Optional[int]
    cpu: float                # load (tf); for the Command Center: output
    power: float              # load (MW); for the Command Center: output
    capacity: float           # m3
    isk_cost: float           # invTypes basePrice


@dataclass(frozen=True)
class LinkSpec:
    cpu_base: float
    power_base: float
    cpu_per_km: float
    power_per_km: float
    cpu_level_modifier: float
    power_level_modifier: float
    capacity: float           # m3/h at level 0, doubles per level

    def cost(self, km: float, level: int = 0) -> tuple[int, int]:
        """(CPU tf, power MW) of one link. The game rounds each link up -
        confirmed in game 2026-10-04 (PI_TECHNICAL_DESIGN 3.2, V-5)."""
        import math
        cpu = self.cpu_base + self.cpu_per_km * km * (level + 1) ** self.cpu_level_modifier
        power = self.power_base + self.power_per_km * km * (level + 1) ** self.power_level_modifier
        return math.ceil(cpu - 1e-9), math.ceil(power - 1e-9)

    def capacity_at(self, level: int) -> float:
        return self.capacity * (2 ** level)


@dataclass(frozen=True)
class StaticData:
    commodities: dict[int, Commodity]
    schematics: dict[int, Schematic]
    schematic_by_output: dict[int, Schematic]
    structures: dict[int, StructureSpec]
    link: LinkSpec
    head_cpu: float
    head_power: float
    decay_factor: float
    noise_factor: float
    # (kind, planet_type_id) -> structure type id
    structure_ids: dict[tuple[str, int], int] = field(default_factory=dict)

    def structure(self, kind: str, planet_type_id: int) -> Optional[StructureSpec]:
        type_id = self.structure_ids.get((kind, planet_type_id))
        return self.structures.get(type_id) if type_id is not None else None

    def tier(self, type_id: int) -> Optional[int]:
        c = self.commodities.get(type_id)
        return c.tier if c else None

    def name(self, type_id: int) -> str:
        c = self.commodities.get(type_id)
        if c:
            return c.name
        s = self.structures.get(type_id)
        return s.name if s else str(type_id)

    def products_of_tier(self, tier: int) -> list[int]:
        return sorted(t for t, c in self.commodities.items() if c.tier == tier)

    def has_kind(self, kind: str, planet_type_id: int) -> bool:
        return (kind, planet_type_id) in self.structure_ids


# Chains: (source tier, target tier). Source 0 = the planet extracts its own
# P0; otherwise inputs of the source tier are hauled in through launchpads.
CHAIN_P0_P1 = "P0-P1"
CHAIN_P0_P2 = "P0-P2"
CHAIN_P1_P2 = "P1-P2"
CHAIN_P2_P3 = "P2-P3"
CHAIN_P1_P3 = "P1-P3"
CHAIN_P3_P4 = "P3-P4"
CHAIN_P2_P4 = "P2-P4"
CHAIN_P1_P4 = "P1-P4"
CHAINS: dict[str, tuple[int, int]] = {
    CHAIN_P0_P1: (0, 1),
    CHAIN_P0_P2: (0, 2),
    CHAIN_P1_P2: (1, 2),
    CHAIN_P2_P3: (2, 3),
    CHAIN_P1_P3: (1, 3),
    CHAIN_P3_P4: (3, 4),
    CHAIN_P2_P4: (2, 4),
    CHAIN_P1_P4: (1, 4),
}
EXTRACTION_CHAINS = frozenset({CHAIN_P0_P1, CHAIN_P0_P2})


def factory_kind_for_tier(tier: int) -> str:
    if tier == 1:
        return C.KIND_BASIC
    if tier in (2, 3):
        return C.KIND_ADVANCED
    return C.KIND_HIGH_TECH


@dataclass(frozen=True)
class Planet:
    """What a design is computed for: a real planet or a free type + radius."""
    planet_type_id: int
    radius_km: float
    planet_id: Optional[int] = None
    name: Optional[str] = None
    solar_system_id: Optional[int] = None


@dataclass(frozen=True)
class Design:
    """Structure counts of one colony. `factories` maps each product made on
    the planet to its number of factories; `ecus` lists (P0 type id, heads)
    per Extractor Control Unit."""
    chain: str
    product_type_id: int
    planet_type_id: int
    cc_level: int
    factories: tuple[tuple[int, int], ...]
    ecus: tuple[tuple[int, int], ...] = ()
    launchpads: int = 1
    storages: int = 0

    def factory_count(self, type_id: int) -> int:
        return dict(self.factories).get(type_id, 0)

    @property
    def total_factories(self) -> int:
        return sum(n for _t, n in self.factories)

    @property
    def total_heads(self) -> int:
        return sum(h for _p, h in self.ecus)

    @property
    def pin_count(self) -> int:
        return self.total_factories + len(self.ecus) + self.launchpads + self.storages

    def to_dict(self) -> dict:
        return {
            "chain": self.chain,
            "product_type_id": self.product_type_id,
            "planet_type_id": self.planet_type_id,
            "cc_level": self.cc_level,
            "factories": [[t, n] for t, n in self.factories],
            "ecus": [[p, h] for p, h in self.ecus],
            "launchpads": self.launchpads,
            "storages": self.storages,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Design":
        return cls(
            chain=str(d["chain"]),
            product_type_id=int(d["product_type_id"]),
            planet_type_id=int(d["planet_type_id"]),
            cc_level=int(d["cc_level"]),
            factories=tuple((int(t), int(n)) for t, n in d.get("factories") or ()),
            ecus=tuple((int(p), int(h)) for p, h in d.get("ecus") or ()),
            launchpads=int(d.get("launchpads", 1)),
            storages=int(d.get("storages", 0)),
        )


@dataclass(frozen=True)
class Evaluation:
    """Everything the engine knows about one design on one planet."""
    design: Design
    fits: bool
    cpu_used: int
    power_used: int
    cpu_capacity: int
    power_capacity: int
    link_count: int
    link_km: float
    link_level: int
    link_cpu: int
    link_power: int
    # per-hour steady-state rates (type_id -> units/h), before the
    # collection-interval factor
    extracted: dict[int, float]
    produced: dict[int, float]
    consumed: dict[int, float]
    imports: dict[int, float]
    exports: dict[int, float]
    product_per_hour: float          # raw, before the interval factor
    effective_factor: float          # min(1, buffer_hours / interval)
    buffer_hours: float              # inf when nothing has to be stored
    import_m3_per_hour: float
    export_m3_per_hour: float
    utilization: dict[int, float]    # per made product, 0..1
    idle_factories: float            # factory-equivalents not running
    setup_isk: float                 # CC upgrades + structures (+ CC itself)
    notes: tuple[str, ...] = ()

    @property
    def effective_product_per_hour(self) -> float:
        return self.product_per_hour * self.effective_factor
