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

Also covers Tech III hulls/subsystems, using the exact same activity_id=8
Invention mechanic as Tech II (CCP removed the old relic-based "Reverse
Engineering" mechanic years ago, confirmed against real SDE data - see
engine.py classify_activity's docstring) - probability/runs/materials work
identically either way. The *input* consumed does NOT: Tech II consumes a
real, ownable/reprintable T1 BPO/BPC, but Tech III consumes a Sleeper relic
(Intact/Malfunctioning/Wrecked - production/constants.py's ANCIENT_RELIC_
CATEGORY_ID), which can only ever be bought or looted, never manufactured -
see this module's own known-simplifications paragraph below and estimate()'s
relic_cost handling for why this matters for the total cost, not just how
the "blueprint" gets acquired.

Known simplifications: ignores job installation cost (facility fee) for the
invention job itself, and - for a genuine T1 blueprint only, never a Tech
III relic (see above) - ignores the T1 BPC's own copy cost (copying time +
copy job fee to produce the blueprint copy an invention attempt consumes) -
both real EVE costs, neither modeled here. Since both are omitted
consistently across every decryptor choice for the same item, they don't
change *which* decryptor `estimate` recommends unless two decryptors are
close enough that this gap could plausibly flip the ranking - worth keeping
in mind for genuinely marginal calls, confirmed real 2026-08-18. The T1-BPC-
copy-cost omission specifically does NOT extend to a Tech III relic - unlike
a T1 BPO you already own and can reprint near-for-free, a relic must be
bought/looted fresh for every single attempt, so omitting its cost the same
way would be a real, not-minor, understatement of Tech III's true cost
(confirmed real, reported by a user, 2026-08-30 - see estimate()'s own
relic_cost handling, which is why this simplification is now scoped to
"a real blueprint" specifically, not "the invention input" generally).
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from . import pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import ANCIENT_RELIC_CATEGORY_ID, DECRYPTORS
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

    # Confirmed real bug (business-logic audit, 2026-08-29, PB-06): a
    # datacore/decryptor with genuinely no sell order anywhere used to be
    # silently priced at 0 ISK (`price or 0.0`) rather than "unknown" -
    # understating the invention attempt's real cost, which could make an
    # actually-expensive decryptor look cheaper than it is. `datacore_cost`/
    # `decryptor_cost` below still sum whatever *is* priced (useful partial
    # info), but `all_prices_known` gates the decision-driving derived
    # fields (expected_cost_per_success/per_run/net_cost_per_run) so a
    # missing price surfaces as an honest "can't estimate" (None) instead of
    # a too-low number silently feeding compare_decryptors' own ranking.
    all_prices_known = True

    datacore_cost = 0.0
    for material_id, qty in recipe["datacores"]:
        sde_type = storage.get_sde_type(material_id)
        volume = sde_type[3] if sde_type else None
        price = pricing.buy_price(material_id, home, jita, volume, cfg)
        if price is None:
            all_prices_known = False
        datacore_cost += qty * (price or 0.0)

    decryptor_cost = 0.0
    if decryptor.type_id:
        sde_type = storage.get_sde_type(decryptor.type_id)
        volume = sde_type[3] if sde_type else None
        price = pricing.buy_price(decryptor.type_id, home, jita, volume, cfg)
        if price is None:
            all_prices_known = False
        decryptor_cost = price or 0.0

    # Confirmed real bug (reported by a user, 2026-08-30): a Tech III relic
    # (see module docstring) is the "blueprint" being consumed here, exactly
    # like a T1 BPC is for Tech II - but unlike a T1 BPC (usually a
    # near-free reprint of an already-owned BPO, the documented reason its
    # own cost is deliberately NOT modeled below), a relic must be bought or
    # looted fresh for every attempt and is often the single most expensive
    # input. Silently omitting it the same way T1 BPC copy cost is omitted
    # would materially overstate Tech III profitability, not just round it
    # slightly - so it IS priced and added to total_attempt_cost, gated by
    # the same all_prices_known/None-if-unpriced handling as every other
    # priced input above. A genuine T1 blueprint (category_id 9) never hits
    # this branch, so Tech II's existing "ignore BPC copy cost" behavior is
    # completely unchanged.
    relic_cost = 0.0
    if storage.get_type_category(t1_blueprint_type_id) == ANCIENT_RELIC_CATEGORY_ID:
        sde_type = storage.get_sde_type(t1_blueprint_type_id)
        volume = sde_type[3] if sde_type else None
        price = pricing.buy_price(t1_blueprint_type_id, home, jita, volume, cfg)
        if price is None:
            all_prices_known = False
        relic_cost = price or 0.0

    total_attempt_cost = datacore_cost + decryptor_cost + relic_cost
    expected_cost_per_success = (
        (total_attempt_cost / probability) if probability > 0 and all_prices_known else None
    )
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
        datacore_cost=datacore_cost, decryptor_cost=decryptor_cost, relic_cost=relic_cost,
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
    """The single cheapest (net cost per run) decryptor choice for ONE fixed
    invention-source candidate (t1_blueprint_type_id), or None if no
    invention recipe/probability data exists for it. For Tech III, the
    caller decides which of the (up to 3) relic-grade candidates this
    compares decryptors within - see best_recipe_and_decryptor below to also
    let the grade itself vary, which is what Tech III actually needs."""
    try:
        results = compare_decryptors(t1_blueprint_type_id, home, jita, cfg, reducible_material_cost_per_run)
    except ValueError:
        return None
    return results[0] if results else None


def compare_recipes_and_decryptors(product_blueprint_type_id: int, home: dict, jita: dict,
                                    cfg: ProductionConfig = PRODUCTION_CONFIG,
                                    reducible_material_cost_per_run: float = 0.0) -> list[InventionResult]:
    """Every (invention-source candidate, decryptor) combination for
    `product_blueprint_type_id`, cheapest net cost per run first - generalizes
    compare_decryptors (which only ever varies the decryptor, for one fixed
    candidate blueprint) to also vary the candidate itself. Confirmed real
    gap (reported by a user, 2026-08-30): for Tech II this is identical to
    calling compare_decryptors directly (storage.find_invention_recipe_
    candidates_by_product_type_id always returns exactly one candidate
    there, a real T1 blueprint) - the actual difference is Tech III, which
    has up to three relic-grade candidates (Intact/Malfunctioning/Wrecked),
    each with materially different odds/output/cost, all now compared
    side by side rather than the app silently always assuming the highest-
    probability (Intact) grade and never even showing the other two."""
    results: list[InventionResult] = []
    for candidate in storage.find_invention_recipe_candidates_by_product_type_id(product_blueprint_type_id):
        try:
            results.extend(compare_decryptors(candidate, home, jita, cfg, reducible_material_cost_per_run))
        except ValueError:
            continue
    results.sort(key=lambda r: r.net_cost_per_run if r.net_cost_per_run is not None else float("inf"))
    return results


def best_recipe_and_decryptor(product_blueprint_type_id: int, home: dict, jita: dict,
                               cfg: ProductionConfig = PRODUCTION_CONFIG,
                               reducible_material_cost_per_run: float = 0.0) -> Optional[InventionResult]:
    """The single globally-cheapest (candidate, decryptor) combination for
    `product_blueprint_type_id` - see compare_recipes_and_decryptors, this is
    just its first element. None if no invention recipe/probability data
    exists for `product_blueprint_type_id` at all."""
    results = compare_recipes_and_decryptors(product_blueprint_type_id, home, jita, cfg, reducible_material_cost_per_run)
    return results[0] if results else None


def best_recipe_for_decryptor(product_blueprint_type_id: int, decryptor_name: str, home: dict, jita: dict,
                               cfg: ProductionConfig = PRODUCTION_CONFIG,
                               reducible_material_cost_per_run: float = 0.0) -> Optional[InventionResult]:
    """Like best_recipe_and_decryptor, but with the decryptor fixed - still
    explores every grade candidate (Tech III's Intact/Malfunctioning/Wrecked
    relics), picking whichever grade is cheapest with that one decryptor.
    Used when a decryptor has been manually selected (storage.
    selected_decryptors) but the grade itself should still be auto-optimized.
    None if no invention recipe/probability data exists for
    `product_blueprint_type_id` at all."""
    best: Optional[InventionResult] = None
    for candidate in storage.find_invention_recipe_candidates_by_product_type_id(product_blueprint_type_id):
        try:
            result = estimate(candidate, decryptor_name, home, jita, cfg, reducible_material_cost_per_run)
        except ValueError:
            continue
        if best is None:
            best = result
            continue
        best_key = best.net_cost_per_run if best.net_cost_per_run is not None else float("inf")
        result_key = result.net_cost_per_run if result.net_cost_per_run is not None else float("inf")
        if result_key < best_key:
            best = result
    return best
