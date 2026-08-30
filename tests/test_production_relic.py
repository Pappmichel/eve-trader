"""Tests for Tech III relic handling in eve_trader/production/invention.py -
confirmed real bug, reported by a user, 2026-08-30, independently verified
against wiki.eveuniversity.org/Tech_3_Production: a Tech III item's real
invention input is a Sleeper relic (Intact/Malfunctioning/Wrecked grade),
never a real T1 blueprint - the app used to always resolve to the highest-
probability (Intact) grade only, never priced the relic's own market cost
into the invention attempt, and routed its logistics through the wrong
(blueprint-copy) availability check entirely (see test_production_engine.py
for that last part).
"""
import pytest

from eve_trader import storage
from eve_trader.production import invention
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import ANCIENT_RELIC_CATEGORY_ID

# Zeroed datacore/encryption skills - ProductionConfig's own defaults are
# 4/4/4 (a realistic well-skilled operator), which would make skill_
# multiplier() != 1.0 and complicate the hand-calculated expected values
# below. Zeroing them keeps probability == base_probability exactly.
_ZERO_SKILL_CFG = ProductionConfig(datacore_skill_1_level=0, datacore_skill_2_level=0, encryption_skill_level=0)

# Real EVE University-confirmed grade stats (probability, output_runs) -
# same numbers independently verified in the business-logic audit that
# found this bug.
WRECKED_TYPE_ID, MALFUNCTIONING_TYPE_ID, INTACT_TYPE_ID = 301, 303, 302
RECIPES = {
    WRECKED_TYPE_ID: {"base_probability": 0.14, "base_runs": 3, "datacores": [], "product_type_id": 999},
    MALFUNCTIONING_TYPE_ID: {"base_probability": 0.21, "base_runs": 10, "datacores": [], "product_type_id": 999},
    INTACT_TYPE_ID: {"base_probability": 0.26, "base_runs": 20, "datacores": [], "product_type_id": 999},
}
# Deliberately chosen so the cheapest grade is NOT the highest-probability
# one - Wrecked is 50x cheaper than Intact, which more than offsets its
# worse odds/fewer runs. Proves real cost-vs-odds optimization, not just
# "always picks Intact" (the old, confirmed-wrong behavior).
RELIC_PRICES = {WRECKED_TYPE_ID: 1_000_000.0, MALFUNCTIONING_TYPE_ID: 10_000_000.0, INTACT_TYPE_ID: 50_000_000.0}


@pytest.fixture(autouse=True)
def _fake_sde(monkeypatch):
    monkeypatch.setattr(storage, "get_invention_recipe", lambda type_id: RECIPES[type_id])
    monkeypatch.setattr(storage, "get_sde_type",
                         lambda type_id: (type_id, None, f"Type {type_id}", 0.01, None, None, None, None))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda *a, **k: [])
    monkeypatch.setattr(storage, "get_type_category",
                         lambda type_id: ANCIENT_RELIC_CATEGORY_ID if type_id in RECIPES else 9)


def test_estimate_prices_a_relic_but_not_a_real_t1_blueprint(monkeypatch):
    monkeypatch.setattr(invention.pricing, "buy_price", lambda type_id, *a, **k: RELIC_PRICES.get(type_id, 0.0))

    relic_result = invention.estimate(INTACT_TYPE_ID, "None", {}, {})
    assert relic_result.relic_cost == pytest.approx(50_000_000.0)
    assert relic_result.total_attempt_cost == pytest.approx(50_000_000.0)

    # A genuine T1 blueprint (category_id 9, not a relic) must NOT have its
    # own "buy price" added - same deliberate simplification Tech II already
    # had (a T1 BPO is normally a near-free reprint of something you own).
    monkeypatch.setattr(storage, "get_invention_recipe",
                         lambda type_id: {"base_probability": 0.4, "base_runs": 1, "datacores": [], "product_type_id": 1})
    real_bp_result = invention.estimate(500, "None", {}, {})  # 500 not in RECIPES -> category_id 9 (real blueprint)
    assert real_bp_result.relic_cost == 0.0
    assert real_bp_result.total_attempt_cost == 0.0


def test_best_recipe_for_decryptor_picks_cheapest_grade_not_highest_probability(monkeypatch):
    """The core PB-08-style fix: Wrecked (worst odds, 0.14/3-run) wins here
    because it's 50x cheaper than Intact (best odds, 0.26/20-run) - net cost
    per run more than offsets the worse odds. The old, confirmed-wrong
    behavior always resolved to Intact alone and could never even see this."""
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id",
                         lambda product_blueprint_type_id: (INTACT_TYPE_ID, MALFUNCTIONING_TYPE_ID, WRECKED_TYPE_ID))
    monkeypatch.setattr(invention.pricing, "buy_price", lambda type_id, *a, **k: RELIC_PRICES.get(type_id, 0.0))

    result = invention.best_recipe_for_decryptor(999, "None", {}, {}, cfg=_ZERO_SKILL_CFG)

    assert result is not None
    assert result.t1_blueprint_type_id == WRECKED_TYPE_ID
    # net_cost_per_run = (1_000_000 / 0.14) / 3 ~= 2,380,952
    assert result.net_cost_per_run == pytest.approx(2_380_952.38, rel=1e-4)


def test_best_recipe_for_decryptor_picks_intact_when_it_is_actually_cheapest(monkeypatch):
    """Sanity check the other direction - when Intact really is the best
    deal (all grades priced equally here), it must still win, proving this
    isn't a hardcoded "always pick the cheapest sticker price" shortcut
    either - it's net cost per run, genuinely computed per grade."""
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id",
                         lambda product_blueprint_type_id: (INTACT_TYPE_ID, MALFUNCTIONING_TYPE_ID, WRECKED_TYPE_ID))
    monkeypatch.setattr(invention.pricing, "buy_price", lambda type_id, *a, **k: 1_000_000.0)  # equal price, all grades

    result = invention.best_recipe_for_decryptor(999, "None", {}, {})

    assert result is not None
    assert result.t1_blueprint_type_id == INTACT_TYPE_ID  # best odds/runs wins when price is equal


def test_compare_recipes_and_decryptors_covers_every_grade(monkeypatch):
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id",
                         lambda product_blueprint_type_id: (INTACT_TYPE_ID, MALFUNCTIONING_TYPE_ID, WRECKED_TYPE_ID))
    monkeypatch.setattr(invention.pricing, "buy_price", lambda type_id, *a, **k: RELIC_PRICES.get(type_id, 0.0))

    results = invention.compare_recipes_and_decryptors(999, {}, {})

    grades_seen = {r.t1_blueprint_type_id for r in results}
    assert grades_seen == {INTACT_TYPE_ID, MALFUNCTIONING_TYPE_ID, WRECKED_TYPE_ID}
    assert len(results) == 3 * len(invention.DECRYPTORS)  # every grade x every decryptor
    assert results[0].t1_blueprint_type_id == WRECKED_TYPE_ID  # cheapest overall, sorted first
