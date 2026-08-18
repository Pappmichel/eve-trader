"""Invention cost/probability calculator: for a T1 blueprint, estimate the
datacore + decryptor cost, success probability, and resulting BPC run count
for each decryptor choice, and pick the "best" one by minimizing *net* cost
per BPC run after accounting for the material savings the resulting ME gives
you on every subsequent build:
`((datacore+decryptor cost)/probability)/output_runs - (ME/100)*reducible_material_cost`.

Probability formula is EVE's actual invention mechanic (per
https://wiki.eveuniversity.org/Invention) - the real game uses two separate
datacore/science skills:

    probability = base_probability
                  * (1 + (datacore_skill_1 + datacore_skill_2)/30 + encryption_skill/40)
                  * decryptor_probability_multiplier

Also covers Tech III hulls/subsystems, with zero extra code: CCP removed the
old relic-based "Reverse Engineering" mechanic years ago, so Tech III is
invented via this exact same T1-BP-plus-datacores mechanism (confirmed
against real SDE data - see engine.py classify_activity's docstring) - a
Tech III item just needs to actually be *recognized* as invented, which is
classify_activity's job, not this module's.

Known simplifications: ignores job installation cost (facility fee) for the
invention job itself, and ignores the T1 BPC's own copy cost (copying time +
copy job fee to produce the blueprint copy an invention attempt consumes) -
both real EVE costs, neither modeled here. Since both are omitted
consistently across every decryptor choice for the same item, they don't
change *which* decryptor `estimate` recommends unless two decryptors are
close enough that this gap could plausibly flip the ranking - worth keeping
in mind for genuinely marginal calls, confirmed real 2026-08-18.
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from . import pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import DECRYPTORS
from .models import InventionResult


def reducible_material_cost(manufacturing_blueprint_id: int, activity_id: int, home: dict, jita: dict,
                             cfg: ProductionConfig = PRODUCTION_CONFIG) -> float:
    """Cost of a blueprint's own build materials that actually scale with ME
    (base qty > 1/run - EVE never reduces a material below 1 unit/run, so
    qty=1 materials are excluded). Used to weigh a decryptor's ME bonus
    against its cost - see module docstring."""
    total = 0.0
    for material_id, base_qty in storage.get_blueprint_materials(manufacturing_blueprint_id, activity_id):
        if base_qty <= 1:
            continue
        sde_type = storage.get_sde_type(material_id)
        volume = sde_type[3] if sde_type else None
        price = pricing.buy_price(material_id, home, jita, volume, cfg)
        total += base_qty * (price or 0.0)
    return total


def skill_multiplier(cfg: ProductionConfig = PRODUCTION_CONFIG) -> float:
    """EVE's real invention formula: each of the two datacore/science skills
    contributes 1/30 (~3.33%) per level, encryption contributes 1/40 (2.5%)
    per level - additive."""
    return 1 + (cfg.datacore_skill_1_level + cfg.datacore_skill_2_level) / 30 + cfg.encryption_skill_level / 40


def estimate(t1_blueprint_type_id: int, decryptor_name: str, home: dict, jita: dict,
             cfg: ProductionConfig = PRODUCTION_CONFIG, reducible_material_cost_per_run: float = 0.0) -> InventionResult:
    """`reducible_material_cost_per_run` is the cost of the resulting item's own
    build materials that actually scale with ME (base qty > 1 - EVE never
    reduces a material below 1 unit/run) - pass 0 to ignore build-side ME
    savings entirely (e.g. when just pricing invention itself, not deciding
    which decryptor to build with)."""
    recipe = storage.get_invention_recipe(t1_blueprint_type_id)
    if recipe is None:
        raise ValueError(f"No invention recipe found for type ID {t1_blueprint_type_id}.")
    if recipe["base_probability"] is None:
        raise ValueError(f"No base success probability found for type ID {t1_blueprint_type_id}.")

    decryptor = DECRYPTORS[decryptor_name]
    probability = min(1.0, recipe["base_probability"] * skill_multiplier(cfg) * decryptor.probability_multiplier)
    output_runs = max(0, recipe["base_runs"] + decryptor.run_bonus)

    datacore_cost = 0.0
    for material_id, qty in recipe["datacores"]:
        sde_type = storage.get_sde_type(material_id)
        volume = sde_type[3] if sde_type else None
        price = pricing.buy_price(material_id, home, jita, volume, cfg)
        datacore_cost += qty * (price or 0.0)

    decryptor_cost = 0.0
    if decryptor.type_id:
        sde_type = storage.get_sde_type(decryptor.type_id)
        volume = sde_type[3] if sde_type else None
        decryptor_cost = pricing.buy_price(decryptor.type_id, home, jita, volume, cfg) or 0.0

    total_attempt_cost = datacore_cost + decryptor_cost
    expected_cost_per_success = (total_attempt_cost / probability) if probability > 0 else None
    expected_cost_per_run = (
        (expected_cost_per_success / output_runs) if expected_cost_per_success is not None and output_runs > 0 else None
    )
    material_savings_per_run = (decryptor.me_bonus / 100) * reducible_material_cost_per_run
    net_cost_per_run = (
        (expected_cost_per_run - material_savings_per_run) if expected_cost_per_run is not None else None
    )

    t1_type = storage.get_sde_type(t1_blueprint_type_id)
    product_type = storage.get_sde_type(recipe["product_type_id"])

    return InventionResult(
        t1_blueprint_type_id=t1_blueprint_type_id,
        t1_blueprint_name=t1_type[2] if t1_type else str(t1_blueprint_type_id),
        product_type_id=recipe["product_type_id"],
        product_name=product_type[2] if product_type else str(recipe["product_type_id"]),
        decryptor=decryptor_name, probability=probability, output_runs=output_runs,
        datacore_cost=datacore_cost, decryptor_cost=decryptor_cost,
        total_attempt_cost=total_attempt_cost,
        expected_cost_per_success=expected_cost_per_success,
        expected_cost_per_run=expected_cost_per_run,
        me=decryptor.me_bonus, te=decryptor.te_bonus,
        material_savings_per_run=material_savings_per_run,
        net_cost_per_run=net_cost_per_run,
    )


def compare_decryptors(t1_blueprint_type_id: int, home: dict, jita: dict,
                        cfg: ProductionConfig = PRODUCTION_CONFIG,
                        reducible_material_cost_per_run: float = 0.0) -> list[InventionResult]:
    """One InventionResult per decryptor option (including "None"), cheapest
    net cost per BPC run first (expected invention cost minus the ME material
    savings that decryptor gives on every subsequent build)."""
    results = [
        estimate(t1_blueprint_type_id, name, home, jita, cfg, reducible_material_cost_per_run)
        for name in DECRYPTORS
    ]
    results.sort(key=lambda r: r.net_cost_per_run if r.net_cost_per_run is not None else float("inf"))
    return results


def best_decryptor_for_item(t1_blueprint_type_id: int, home: dict, jita: dict,
                             cfg: ProductionConfig = PRODUCTION_CONFIG,
                             reducible_material_cost_per_run: float = 0.0) -> Optional[InventionResult]:
    """The single cheapest (net cost per run) decryptor choice, or None if no
    invention recipe/probability data exists for `t1_blueprint_type_id`."""
    try:
        results = compare_decryptors(t1_blueprint_type_id, home, jita, cfg, reducible_material_cost_per_run)
    except ValueError:
        return None
    return results[0] if results else None
