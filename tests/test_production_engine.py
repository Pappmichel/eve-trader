import pytest

from eve_trader import storage
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import ANCIENT_RELIC_CATEGORY_ID, SCC_SURCHARGE_RATE, ACTIVITY_MODS, rig_security_multiplier
from eve_trader.production.engine import (
    _activity_mods, _material_qty, _structural_material_closure, _tech_ii_mods, _total_missing, classify_activity,
)
from eve_trader.production.models import CharacterSlotRow

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant, tenant_pair  # noqa: F401

# Only the plan_asset_optimized/plan_production tests below need `tenant` +
# postgres_required() - everything else in this file monkeypatches
# storage.* entirely, but those two are wrapped in
# @storage.with_batch_session(), which unconditionally checks out a real
# pooled connection (to run `SELECT set_config(...)`) even when every
# individual storage read/write inside is mocked - confirmed live, those
# tests still raised "no current tenant set" without this. No module-level
# pytestmark here (most tests in this file genuinely don't need Postgres) -
# each of the 11 affected tests gets its own
# @pg_helpers.postgres_required() decorator instead.
psycopg = pytest.importorskip("psycopg")


@pytest.fixture(autouse=True)
def _reset_discover_cache():
    # discover_build_candidates now caches its own results (see engine.py's
    # _discover_cache) - without this, whichever test in this file runs
    # first would "win" and every later discover_build_candidates call in
    # the same test session would silently reuse its (monkeypatched, test-
    # specific) results instead of exercising its own setup.
    engine.invalidate_discover_cache()
    yield
    engine.invalidate_discover_cache()


@pytest.fixture(autouse=True)
def _reset_ship_margin_cache():
    # Same reasoning as _reset_discover_cache above, for engine._ship_margin_cache.
    engine.invalidate_ship_margin_cache()
    yield
    engine.invalidate_ship_margin_cache()


def test_material_qty_never_reduces_below_runs():
    # base_qty=1 is never ME-reduced in real EVE - floored at runs regardless
    # of how large the reduction is.
    assert _material_qty(1, 0.90, 10) == 10
    assert _material_qty(1, 0.60, 100) == 100


def test_material_qty_rounds_up_per_batch():
    # 10 runs * 3 base_qty * 0.90 = 27.0 exactly - no rounding needed.
    assert _material_qty(3, 0.90, 10) == 27
    # 7 runs * 5 base_qty * 0.97 = 33.95 -> rounds up to 34, still above the
    # runs=7 floor.
    assert _material_qty(5, 0.97, 7) == 34


def test_current_stock_checks_every_location_not_a_curated_set(monkeypatch):
    # Real bug found live (2026-08-01): a curated "configured production
    # locations" set (home structure + Logistik tab job_category_locations +
    # Jita stations) still missed a real 1,572,335-unit Silicates stash
    # sitting at a structure that was in *neither* the active category-
    # location list *nor* its saved quick-switch options - any maintained
    # whitelist can silently miss a real structure the same way. Fixed by
    # checking every location at once (location_id=None) instead of a list -
    # confirm _current_stock passes None through, not a specific location.
    calls = []

    def fake_esi_stock(type_id, location_id):
        calls.append(location_id)
        return 1_572_335.0

    monkeypatch.setattr(storage, "esi_stock_at_location", fake_esi_stock)
    monkeypatch.setattr(storage, "esi_incoming_industry_qty", lambda type_id: {"runs": 0, "jobs": 0})
    cfg = ProductionConfig(home_location_id=1000000000001)

    total = engine._current_stock(type_id=16636, manual_stock={}, cfg=cfg, bp=None)

    assert calls == [None]  # checked everywhere in one call, not a specific/curated location
    assert total == 1_572_335.0


def _no_listings(monkeypatch):
    monkeypatch.setattr(storage, "sell_order_qty_at_location", lambda type_id, location_id: 0.0)
    monkeypatch.setattr(storage, "sell_order_qty_in_region", lambda type_id, region_id: 0.0)


def test_market_status_skips_items_with_no_market_target(monkeypatch):
    # GitHub issue #33: a stock target that's purely backup/component stock
    # (never meant to be listed anywhere) has home_market_stock and
    # jita_market_stock both NULL/0 - it shouldn't clutter the Market Status
    # tab with two permanently-empty target columns. Confirmed live: Griffin
    # had home_market_stock=0 (not NULL) and still showed up before 0 was
    # treated the same as NULL.
    cfg = ProductionConfig(home_location_id=1000000000001)
    _no_listings(monkeypatch)
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {})
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 0.0)
    monkeypatch.setattr(storage, "esi_incoming_industry_qty", lambda type_id: {"runs": 0, "jobs": 0})
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [
        (1, "No Market Target", 10.0, None, None),
        (2, "Home Target Only", 0.0, 20.0, None),
        (3, "Jita Target Only", 0.0, None, 20.0),
        (4, "Griffin", 0.0, 0.0, None),
        (5, "Zero Both", 0.0, 0.0, 0.0),
    ])

    rows = engine.market_status(cfg)

    assert [r.type_id for r in rows] == [2, 3]


def test_total_missing_owned_stock_beyond_backup_covers_market_targets(monkeypatch):
    # Real bug found via a user report ("Buy List zeigt was ich insgesamt
    # brauche, nicht was ich kaufen muss") - live example: Sentinel had
    # backup_stock=0, current_stock=20 (owned, unlisted), home/jita targets
    # of 20 each. Before this fix, current_stock was only ever netted
    # against backup_stock - since backup_stock was already 0, the 20 owned
    # hulls were silently discarded instead of covering half of the 40 units
    # of market-target demand.
    cfg = ProductionConfig(home_location_id=1000000000001)
    _no_listings(monkeypatch)

    missing = _total_missing(12005, backup_stock=0.0, home_market_stock=20.0, jita_market_stock=20.0,
                              current_stock=20.0, cfg=cfg)

    assert missing == 20.0  # 20 owned units cover half of the 40 total market-target demand


def test_total_missing_backup_reserve_is_covered_before_market_targets(monkeypatch):
    # Backup must be satisfied first - a unit "spoken for" by the reserve
    # isn't available to also cover a market target.
    cfg = ProductionConfig(home_location_id=1000000000001)
    _no_listings(monkeypatch)

    missing = _total_missing(1, backup_stock=10.0, home_market_stock=10.0, jita_market_stock=None,
                              current_stock=10.0, cfg=cfg)

    assert missing == 10.0  # all 10 owned units went to backup, none left for the home target


def test_total_missing_surplus_cascades_backup_then_home_then_jita(monkeypatch):
    cfg = ProductionConfig(home_location_id=1000000000001)
    _no_listings(monkeypatch)

    missing = _total_missing(1, backup_stock=5.0, home_market_stock=5.0, jita_market_stock=5.0,
                              current_stock=12.0, cfg=cfg)

    # 5 to backup (0 left), 5 to home (0 left), 2 left for jita -> jita short by 3.
    assert missing == 3.0


def test_total_missing_still_nets_against_already_listed_quantity(monkeypatch):
    # Listed units and owned-but-unlisted units are two different pools
    # (see _current_stock/_total_missing docstrings) - both must still net
    # against the target, not just one or the other.
    cfg = ProductionConfig(home_location_id=1000000000001)
    monkeypatch.setattr(storage, "sell_order_qty_at_location", lambda type_id, location_id: 5.0)
    monkeypatch.setattr(storage, "sell_order_qty_in_region", lambda type_id, region_id: 0.0)

    missing = _total_missing(1, backup_stock=0.0, home_market_stock=20.0, jita_market_stock=None,
                              current_stock=10.0, cfg=cfg)

    assert missing == 5.0  # 20 target - 5 already listed - 10 owned = 5 short


def test_total_missing_zero_current_stock_matches_prior_behavior(monkeypatch):
    cfg = ProductionConfig(home_location_id=1000000000001)
    _no_listings(monkeypatch)

    missing = _total_missing(1, backup_stock=0.0, home_market_stock=36.0, jita_market_stock=36.0,
                              current_stock=0.0, cfg=cfg)

    assert missing == 72.0


@pg_helpers.postgres_required()
def test_tech_iii_manual_decryptor_applies_without_invention_recipe(monkeypatch, tenant):
    # Defensive fallback case (rare - missing/stale SDE data, not the normal
    # Tech III path, which genuinely does have invention-recipe candidates -
    # see production/invention.py's own module docstring): no invention
    # recipe/candidate can be found at all, but a manually-selected
    # decryptor must still drive ME/TE instead of silently falling back to
    # the flat Tech II baseline.
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    cfg = ProductionConfig()

    material_mult, time_mult, decryptor_name, chosen = _tech_ii_mods(
        type_id=1, blueprint_id=2, activity_id=1, cfg=cfg, home={}, jita={},
        selected_decryptors={1: "Accelerant"}, memo={},
    )
    # Accelerant: absolute ME4/TE14 -> multipliers 0.96/0.86 (no structure/rig bonus, cfg default = Citadel/no rig).
    assert round(material_mult, 4) == 0.96
    assert round(time_mult, 4) == 0.86
    assert decryptor_name == "Accelerant"
    assert chosen is None  # no real recipe - this is the defensive override-only fallback


@pg_helpers.postgres_required()
def test_tech_iii_falls_back_to_flat_baseline_without_override(monkeypatch, tenant):
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    cfg = ProductionConfig()

    material_mult, time_mult, decryptor_name, chosen = _tech_ii_mods(
        type_id=1, blueprint_id=2, activity_id=1, cfg=cfg, home={}, jita={},
        selected_decryptors={}, memo={},
    )
    assert round(material_mult, 4) == 0.98
    assert round(time_mult, 4) == 0.96
    assert decryptor_name is None
    assert chosen is None


@pg_helpers.postgres_required()
def test_tech_i_uses_owned_bpo_me_te_when_available(monkeypatch, tenant):
    # Owned BPO is ME10/TE6 (better ME than the flat "perfect" TE20 baseline
    # assumes, worse TE) - real owned data must win over the flat assumption.
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id: (10, 6))
    cfg = ProductionConfig()  # default structure = "Citadel (no bonuses)" -> no extra bonus

    material_mult, time_mult, _ = _activity_mods("Tech I", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=2)
    assert round(material_mult, 4) == 0.90  # 1 - 10/100
    assert round(time_mult, 4) == 0.94      # 1 - 6/100


@pg_helpers.postgres_required()
def test_tech_i_falls_back_to_flat_baseline_when_bpo_not_owned(monkeypatch, tenant):
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id: None)
    cfg = ProductionConfig()

    material_mult, time_mult, _ = _activity_mods("Tech I", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=2)
    assert round(material_mult, 4) == 0.90  # flat ACTIVITY_MODS["Tech I"] baseline
    assert round(time_mult, 4) == 0.80


@pg_helpers.postgres_required()
def test_faction_is_always_me0_te0_and_ignores_owned_bpo_data(monkeypatch, tenant):
    # Confirmed with the user (2026-07-16): unlike Tech I, a Faction
    # blueprint's ME/TE is fixed at 0/0 in real EVE and can never be
    # researched or changed - even if get_owned_bpo_best_me_te somehow
    # returned something else (it never legitimately would for a real
    # Faction BPO), it must be ignored; ACTIVITY_MODS["Faction"] (1.00/1.00,
    # i.e. no reduction) is the one and only correct value, not a fallback.
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id: (10, 6))
    cfg = ProductionConfig()

    material_mult, time_mult, _ = _activity_mods("Faction", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=2)
    assert round(material_mult, 4) == 1.00
    assert round(time_mult, 4) == 1.00


def test_classify_activity_treats_low_meta_level_invented_item_as_tech_ii(monkeypatch):
    # Real bug found against live SDE data: a Tech III subsystem ("Loki Core
    # - Augmented Nuclear Reactor") has metaLevel=1 - genuinely invented
    # (find_invention_recipe_candidates_by_product_type_id finds a real
    # recipe), and must be routed through the decryptor-based Tech II path,
    # not priced off the flat "perfect Tech I BPO" baseline - metaLevel
    # plays no part in the decision at all, only the invention recipe does.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: (999,))

    activity, bp = classify_activity(100)
    assert activity == "Tech II"
    assert bp == (200, 1, 1.0)


def test_classify_activity_still_tech_i_for_genuinely_uninvented_low_meta_item(monkeypatch):
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (100, 958, "Plain Tech I Thing", 40.0, 1, 1125, 0, None))

    activity, _ = classify_activity(100)
    assert activity == "Tech I"


def test_classify_activity_treats_uninvented_faction_ship_as_faction_not_tech_i_or_ii(monkeypatch):
    # Real bug found against live SDE data (2026-07-16): Faction/Pirate ships
    # (Machariel, Nestor: metaLevel 8) have no real invention recipe at all -
    # they're built from their own real, directly-researchable blueprint,
    # same as a Tech I item mechanically - but the old "metaLevel>=2 OR
    # invented" rule classified any elevated-metaLevel item as "Tech II"
    # regardless, which wrongly priced them off the flat Tech II ME/TE
    # baseline instead of the correct Tech I-style one (confirmed live: this
    # hit 854 of 4208 scanned manufacturable items - Faction ships,
    # Officer/Deadspace modules, faction boosters). Fixed by dropping
    # metaLevel entirely from the Tech II decision - but per the user
    # (2026-07-16), these items should still be visibly labeled "Faction",
    # not lumped in as plain "Tech I", via the SDE's real metaGroupID (4).
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (17739, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (17738, 27, "Machariel", 470000.0, 1, 1380, 8, 4))

    activity, bp = classify_activity(17738)  # Machariel's real type_id
    assert activity == "Faction"
    assert bp == (17739, 1, 1.0)


def test_classify_activity_treats_uninvented_officer_item_as_officer(monkeypatch):
    # Extended 2026-07-17: metaGroupID 5 = Officer - confirmed live against
    # this app's own cached SDE that Officer has real, blueprint-backed,
    # market-priceable products (e.g. "Zorya's Light Entropic Disintegrator",
    # a genuine 3-material Abyssal-tier blueprint), same treatment as Faction
    # (ME0/TE0, non-researchable label, see ACTIVITY_MODS["Officer"]).
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (100, 1, "Some Officer Mod", 5.0, 1, 600, 14, 5))

    activity, _ = classify_activity(100)
    assert activity == "Officer"


def test_classify_activity_treats_uninvented_storyline_item_as_storyline(monkeypatch):
    # metaGroupID 3 = Storyline - confirmed live (e.g. "Purloined Sansha Data
    # Analyzer", built from a real "Mangled Sansha Data Analyzer" conversion
    # blueprint) - same ME0/TE0, non-researchable treatment as Faction.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (201, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (101, 1, "Some Storyline Mod", 5.0, 1, 600, 14, 3))

    activity, _ = classify_activity(101)
    assert activity == "Storyline"


def test_classify_activity_treats_uninvented_deadspace_item_as_deadspace(monkeypatch):
    # metaGroupID 6 = Deadspace - currently unreachable in the real SDE (no
    # Deadspace product has a blueprint at all, deadspace modules are
    # drop-only) but the branch exists for a future SDE that might add one;
    # exercised directly here since discover_build_candidates/plan_production
    # will never hit it against real data today.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (202, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (102, 1, "Some Deadspace Mod", 5.0, 1, 600, 14, 6))

    activity, _ = classify_activity(102)
    assert activity == "Deadspace"


def test_classify_activity_falls_back_to_tech_i_for_unmapped_meta_group(monkeypatch):
    # Any other real metaGroupID not explicitly mapped (e.g. 17/19 - SKINs,
    # apparel, boosters) still falls back to plain "Tech I", not a KeyError.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (203, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (103, 1, "Some Other Item", 5.0, 1, 600, 14, 17))

    activity, _ = classify_activity(103)
    assert activity == "Tech I"


def test_job_category_treats_advanced_capital_components_as_advanced_not_capital(monkeypatch):
    # Real item, real bug (2026-08-19): group 913 "Advanced Capital
    # Construction Components" (e.g. Capital Nanoelectrical Microprocessor)
    # is a Tech 2 *capital* component, but per its own ME rig's ESI
    # description it's built on the same "Standup M-Set Advanced Component
    # Manufacturing" structure as plain Tech 2/Tech 3/Tools/Data Interfaces
    # components, not the dedicated Basic-Capital-Component one - confirmed
    # against the user's own real build location. Only group 873 (actual
    # Basic Capital Components) should ever come back "Capital Components".
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (29074, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(
        storage, "get_sde_type",
        lambda type_id: (29073, 913, "Capital Nanoelectrical Microprocessor", 10.0, 1, 1884, None, None),
    )

    assert engine.job_category(29073) == "Advanced Components"


def test_job_category_treats_basic_capital_components_as_capital(monkeypatch):
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(
        storage, "get_sde_type",
        lambda type_id: (100, 873, "Capital Construction Parts", 10.0, 1, 1884, None, None),
    )

    assert engine.job_category(100) == "Capital Components"


def test_unit_cost_detail_returns_build_cost_and_buy_price_separately(monkeypatch):
    # unit_cost_detail exists so a caller (Doctrine's Shopping List) can show
    # Build cost and Buy price side by side, not just _unit_cost's own
    # min(buy, build) - confirms it exposes both, not just the winner.
    # Empty materials isolates unit_cost_detail's own new top-level formula
    # without needing to also exercise _unit_cost's recursion (never directly
    # tested even for _unit_cost itself in this file - always mocked away in
    # its own downstream tests).
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (100, 958, "Plain Tech I Thing", 40.0, 1, 1125, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 99)  # not a ship - plain SDE volume path
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id: None)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(storage, "get_manual_blueprint_copy_cost_per_run", lambda type_id: None)

    cfg = ProductionConfig()
    home = {100: _fake_quote(100, sell=500.0)}

    best, build_cost, buy_price = engine.unit_cost_detail(
        100, cfg, home, {}, memo={}, selected_decryptors={}, t2_memo={}, cost_indices={}, adjusted_prices={})

    assert buy_price == pytest.approx(500.0 * (1 + cfg.jita_buy_broker_fee))
    assert build_cost == 0.0  # no materials, no job cost (adjusted_prices empty -> eiv=0)
    assert best == 0.0  # min(buy, 0.0) - build wins


def test_unit_cost_detail_adds_amortized_manual_bpc_cost_per_unit(monkeypatch):
    # GitHub issue #40: a blueprint copy that must be bought outright adds
    # its purchase cost, amortized over its included runs, to the per-unit
    # build cost - on top of materials/job cost.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 2.0))  # 2 units/run
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (100, 958, "Plain Tech I Thing", 40.0, 1, 1125, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 99)
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id: None)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    # 1,000,000 ISK copy good for 10 runs -> 100,000 ISK/run -> 50,000 ISK/unit (2 units/run)
    monkeypatch.setattr(storage, "get_manual_blueprint_copy_cost_per_run", lambda type_id: 1_000_000.0 / 10)

    cfg = ProductionConfig()
    home = {100: _fake_quote(100, sell=500.0)}

    best, build_cost, buy_price = engine.unit_cost_detail(
        100, cfg, home, {}, memo={}, selected_decryptors={}, t2_memo={}, cost_indices={}, adjusted_prices={})

    assert build_cost == pytest.approx(50_000.0)
    assert best == pytest.approx(min(buy_price, 50_000.0))


def test_unit_cost_detail_returns_buy_only_when_not_buildable(monkeypatch):
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: None)  # no blueprint at all
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (100, 958, "Buy-only Thing", 40.0, 1, 1125, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 99)

    cfg = ProductionConfig()
    home = {100: _fake_quote(100, sell=500.0)}

    best, build_cost, buy_price = engine.unit_cost_detail(
        100, cfg, home, {}, memo={}, selected_decryptors={}, t2_memo={}, cost_indices={}, adjusted_prices={})

    assert build_cost is None
    assert buy_price == best == pytest.approx(500.0 * (1 + cfg.jita_buy_broker_fee))


def test_reaction_never_uses_owned_bpo_data(monkeypatch):
    # Reactions have no BPO research in real EVE - _owned_bpo_mods must never
    # be consulted for them, even if the lookup would return something.
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id: (10, 10))
    cfg = ProductionConfig()

    material_mult, time_mult, _ = _activity_mods("Reaction", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=2)
    assert round(material_mult, 4) == 1.00
    assert round(time_mult, 4) == 1.00


@pg_helpers.postgres_required()
def test_job_cost_rate_includes_facility_tax_and_scc_surcharge(monkeypatch, tenant):
    # Confirmed real bug (wiki.eveuniversity.org/Manufacturing): real job
    # cost = EIV * (system_index * structure_bonus + facility_tax + SCC
    # surcharge) - facility tax and the SCC surcharge are additive, not
    # folded into the index multiplication. Uses the flat ACTIVITY_MODS
    # fallback (empty cost_indices) so the expected number is easy to hand-
    # compute; default cfg structure is "Citadel (no bonuses)" -> cost_mult=1.0.
    cfg = ProductionConfig()
    _, _, job_cost_rate = _activity_mods("Tech I", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=None)
    expected = ACTIVITY_MODS["Tech I"].job_cost_rate * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE
    assert round(job_cost_rate, 6) == round(expected, 6)
    # Sanity: the additive terms must actually move the number, not be a no-op.
    assert job_cost_rate > ACTIVITY_MODS["Tech I"].job_cost_rate


def test_job_cost_rate_prefers_category_system_index_over_flat_split(monkeypatch):
    # GitHub issue #12: a category-specific system index (from Logistik's
    # own per-category structure assignment) must win over the flat
    # component/manufacturing 2-way split when both are present.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (500, 11, 1.0))  # activity_id 11 = Reaction
    cfg = ProductionConfig()
    cost_indices = {
        "component": {"reaction": 0.05},  # flat split - must be ignored, not used
        "category:Reactions": {"reaction": 0.20},
    }

    _, _, job_cost_rate = _activity_mods("Reaction", type_id=1, cfg=cfg, cost_indices=cost_indices, blueprint_id=2)

    expected = 0.20 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE  # cost_mult=1.0, default "Citadel" structure
    assert round(job_cost_rate, 6) == round(expected, 6)


def test_job_cost_rate_falls_back_to_flat_split_when_category_has_no_index(monkeypatch):
    # cost_indices carries *some* "category:" entry (so the lookup fires),
    # but not one for *this* item's own category - must still fall back to
    # the flat component/manufacturing split, not silently drop to the
    # ACTIVITY_MODS default while a perfectly good flat index sits right there.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (500, 11, 1.0))
    cfg = ProductionConfig()
    cost_indices = {
        "component": {"reaction": 0.05},
        "category:Capital Components": {"manufacturing": 0.30},  # a different category
    }

    _, _, job_cost_rate = _activity_mods("Reaction", type_id=1, cfg=cfg, cost_indices=cost_indices, blueprint_id=2)

    expected = 0.05 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE
    assert round(job_cost_rate, 6) == round(expected, 6)


def test_job_cost_rate_skips_job_category_lookup_when_no_category_entries_present(monkeypatch):
    # Efficiency/tenant-safety guard: job_category(type_id) re-derives
    # classify_activity, a real storage-backed call - must not even be
    # attempted when cost_indices has no "category:" entries at all (the
    # overwhelming majority of call sites, including every test in this file
    # that passes cost_indices={} with no tenant context set up).
    def _boom(type_id):
        raise AssertionError("must not call storage.get_blueprint_for_product when no category entries exist")
    monkeypatch.setattr(storage, "get_blueprint_for_product", _boom)
    cfg = ProductionConfig()

    material_mult, time_mult, job_cost_rate = _activity_mods(
        "Reaction", type_id=1, cfg=cfg, cost_indices={"component": {"reaction": 0.05}}, blueprint_id=2)

    expected = 0.05 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE
    assert round(job_cost_rate, 6) == round(expected, 6)


def test_job_cost_rate_manual_override_wins_over_category_index(monkeypatch):
    # Confirmed with the user 2026-08-27: a manual override always wins,
    # even over the per-category system index (the previous highest
    # priority) - not just a tiebreaker against the flat split.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (500, 11, 1.0))  # Reaction
    cfg = ProductionConfig(reaction_cost_index_override=0.42)
    cost_indices = {"category:Reactions": {"reaction": 0.20}}

    _, _, job_cost_rate = _activity_mods("Reaction", type_id=1, cfg=cfg, cost_indices=cost_indices, blueprint_id=2)

    expected = 0.42 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE
    assert round(job_cost_rate, 6) == round(expected, 6)


def test_job_cost_rate_manual_override_works_with_no_system_configured_at_all(monkeypatch):
    # The whole point of an override is to work even without picking a
    # system in Settings - empty cost_indices, no category entries.
    cfg = ProductionConfig(reaction_cost_index_override=0.33)

    _, _, job_cost_rate = _activity_mods("Reaction", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=2)

    expected = 0.33 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE
    assert round(job_cost_rate, 6) == round(expected, 6)


def test_job_cost_rate_component_and_reaction_overrides_are_not_mixed_up(monkeypatch):
    # The "component" profile feeds two genuinely different rates - a
    # Reaction job must use reaction_cost_index_override, a Tech I/II
    # component-group job must use component_cost_index_override, even
    # though both resolve to the same "component" system_profile bucket.
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 334, "Fernite Plate", 0.01, 1, 1, 0, None))
    cfg = ProductionConfig(reaction_cost_index_override=0.11, component_cost_index_override=0.22)

    _, _, reaction_rate = _activity_mods("Reaction", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=2)
    _, _, component_rate = _activity_mods("Tech I", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=None)

    assert round(reaction_rate, 6) == round(0.11 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE, 6)
    assert round(component_rate, 6) == round(0.22 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE, 6)


def test_job_cost_rate_manufacturing_override_applies_to_non_component_items(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 999, "Some Ship", 0.01, 1, 1, 0, None))
    cfg = ProductionConfig(manufacturing_cost_index_override=0.07)

    _, _, job_cost_rate = _activity_mods("Tech I", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=None)

    expected = 0.07 * 1.0 + cfg.facility_tax_rate + SCC_SURCHARGE_RATE
    assert round(job_cost_rate, 6) == round(expected, 6)


# ----------------------------------------------------- _structural_material_closure
def _fake_classify_activity(blueprints: dict[int, tuple]):
    """blueprints: {type_id: (blueprint_id, activity_id, product_qty)} - a
    type_id absent from this dict has no blueprint (a leaf/Buy-only item)."""
    def _classify(type_id):
        bp = blueprints.get(type_id)
        return ("Tech I" if bp else "Buy"), bp
    return _classify


def test_structural_material_closure_walks_multiple_levels_and_stops_at_no_blueprint_leaf(monkeypatch):
    monkeypatch.setattr(engine, "classify_activity", _fake_classify_activity({
        100: (1000, 1, 1.0),  # -> materials 200, 201
        200: (1002, 1, 1.0),  # -> material 300
        # 201, 300 have no blueprint - leaves
    }))
    materials = {(1000, 1): [(200, 5.0), (201, 3.0)], (1002, 1): [(300, 1.0)]}
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda bp_id, activity_id: materials[(bp_id, activity_id)])

    result = _structural_material_closure([100])

    assert result == {100, 200, 201, 300}


def test_structural_material_closure_visits_a_shared_material_only_once(monkeypatch):
    # 100 and 400 both need 300 - a real, common BOM shape (shared base
    # mineral/component) - classify_activity must only be asked about 300
    # once, not once per parent that reaches it.
    classify_calls: list[int] = []
    classify = _fake_classify_activity({
        100: (1000, 1, 1.0),  # -> material 300
        400: (1004, 1, 1.0),  # -> material 300
    })

    def _tracking_classify(type_id):
        classify_calls.append(type_id)
        return classify(type_id)
    monkeypatch.setattr(engine, "classify_activity", _tracking_classify)
    materials = {(1000, 1): [(300, 1.0)], (1004, 1): [(300, 1.0)]}
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda bp_id, activity_id: materials[(bp_id, activity_id)])

    result = _structural_material_closure([100, 400])

    assert result == {100, 400, 300}
    assert classify_calls.count(300) == 1


def test_structural_material_closure_capped_at_max_depth(monkeypatch):
    # A chain deeper than MAX_DEPTH (10) - the walk must stop instead of
    # recursing forever (a real EVE BOM never legitimately goes this deep;
    # this only happens for a bad/circular blueprint reference).
    chain_length = 15
    blueprints = {i: (i * 10, 1, 1.0) for i in range(chain_length)}  # 0 needs 1, 1 needs 2, ...
    monkeypatch.setattr(engine, "classify_activity", _fake_classify_activity(blueprints))
    materials = {(i * 10, 1): [(i + 1, 1.0)] for i in range(chain_length - 1)}
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda bp_id, activity_id: materials.get((bp_id, activity_id), []))

    result = _structural_material_closure([0])

    assert len(result) <= engine.MAX_DEPTH + 1  # seed depth + MAX_DEPTH more levels, never the full 15-item chain
    assert chain_length - 1 not in result  # the deepest item is never reached


def test_plan_context_populates_category_cost_indices_sharing_fetches_by_system(monkeypatch):
    # GitHub issue #12: _PlanContext.cost_indices gains one "category:<name>"
    # entry per Logistik category with a resolved system - two categories
    # sharing the same system must only trigger one ESI fetch for it.
    from eve_trader import esi_client as esi_client_module
    from eve_trader import goonmetrics_client as goonmetrics_client_module
    from eve_trader.production import pricing as pricing_module

    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {})
    monkeypatch.setattr(storage, "load_manual_build_buy", lambda: {})
    monkeypatch.setattr(storage, "load_selected_decryptors", lambda: {})
    monkeypatch.setattr(storage, "load_category_system_ids",
                         lambda: {"Reactions": 30000142, "Capital Components": 30000142, "Equipment": 30000144})
    monkeypatch.setattr(pricing_module, "home_prices", lambda cfg, type_ids: {})
    monkeypatch.setattr(pricing_module, "jita_prices", lambda type_ids: {})
    monkeypatch.setattr(goonmetrics_client_module.GoonmetricsClient, "__init__", lambda self: None)
    monkeypatch.setattr(esi_client_module.ESIClient, "__init__", lambda self: None)
    monkeypatch.setattr(esi_client_module.ESIClient, "get_adjusted_prices", lambda self: {})

    fetch_calls = []
    def fake_indices_for(client, system_id):
        fetch_calls.append(system_id)
        if system_id is None:
            return {}
        return {"manufacturing": 0.10 * system_id / 30000142, "reaction": 0.05}
    monkeypatch.setattr(pricing_module, "system_cost_indices_for", fake_indices_for)

    cfg = ProductionConfig(component_system_id=None, manufacturing_system_id=None)
    ctx = engine._PlanContext(cfg)

    real_system_fetches = sorted(s for s in fetch_calls if s is not None)
    assert real_system_fetches == [30000142, 30000144]  # one fetch per distinct system, not per category
    assert ctx.cost_indices["category:Reactions"] == ctx.cost_indices["category:Capital Components"]
    assert ctx.cost_indices["category:Equipment"] != ctx.cost_indices["category:Reactions"]


def test_plan_context_home_prices_exclude_a_live_confirmed_empty_market(monkeypatch):
    # End-to-end regression test for the reported bug (2026-08-26): a stock
    # target whose real C-J market has zero sell orders right now must not
    # show up priced at ctx.home, even if some other source (Goonmetrics)
    # would have reported a stale nonzero price.
    from eve_trader import esi_client as esi_client_module
    from eve_trader.esi_client import OrderStats
    from eve_trader.production import esi_sync as esi_sync_module

    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 1.0, None, None)])
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {})
    monkeypatch.setattr(storage, "load_manual_build_buy", lambda: {})
    monkeypatch.setattr(storage, "load_selected_decryptors", lambda: {})
    monkeypatch.setattr(storage, "load_category_system_ids", lambda: {})
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Buy", None))  # no blueprint - closure is just {587}

    monkeypatch.setattr(esi_sync_module, "list_producer_characters", lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(esi_client_module.ESIClient, "structure_order_stats_bulk",
                         lambda self, location_id, type_ids, auth_role: {
                             587: OrderStats(sell_percentile=None, sell_volume=0.0, buy_percentile=None, buy_volume=0.0),
                         })
    monkeypatch.setattr(esi_client_module.ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {
        587: OrderStats(sell_percentile=100.0, sell_volume=5.0, buy_percentile=90.0, buy_volume=3.0),
    })
    monkeypatch.setattr(esi_client_module.ESIClient, "get_adjusted_prices", lambda self: {})

    cfg = ProductionConfig(component_system_id=None, manufacturing_system_id=None,
                            home_market="c-j6mt", home_location_id=1049588174021)
    ctx = engine._PlanContext(cfg)

    assert ctx.home[587].sell == 0.0
    assert ctx.jita[587].sell == 100.0


def test_reaction_te_rig_applies(monkeypatch):
    # Confirmed real bug: reactor Time Efficiency rigs are a real EVE item
    # (e.g. "Standup M-Set Composite Reactor Time Efficiency I/II") - this
    # used to be hard-coded to assume no such rig exists and always skip the
    # rig's TE bonus for reactions. A T2 TE rig (te_bonus=0.24) in a nullsec
    # system (is_reaction security multiplier 1.1x) must now measurably
    # reduce time even though the Athanor itself has no reaction-duration
    # structure bonus of its own.
    monkeypatch.setattr(storage, "get_system_security", lambda system_id: -0.5)  # nullsec
    cfg = ProductionConfig(reaction_structure_type="Athanor (M Refinery)", reaction_rig_tier="T2-Rig")

    _, time_mult, _ = _activity_mods("Reaction", type_id=1, cfg=cfg, cost_indices={}, blueprint_id=None)
    # Athanor has no reaction-duration structure bonus of its own
    # (te_multiplier=1.00, confirmed 2026-08-18 - see constants.py) - the rig
    # alone must still measurably reduce time.
    assert time_mult < 0.90
    assert round(time_mult, 5) == round(1.00 * (1 - 0.24 * 1.1), 5)


def test_reaction_rig_security_multiplier_uses_refinery_table_not_engineering_complex_table():
    # Confirmed real bug: reaction/refinery rigs use a *different* security
    # scaling table than Engineering Complex rigs (no highsec case at all -
    # reactor rigs are hard-banned there since reactions can't run in
    # highsec; 1x lowsec; 1.1x null/wormhole) - verified against EVE Ref
    # dogma attributes on the real reactor TE rig items. Engineering Complex
    # rigs keep the well-known 1x/1.9x/2.1x table.
    assert rig_security_multiplier(0.9, is_reaction=False) == 1.0
    assert rig_security_multiplier(0.3, is_reaction=False) == 1.9
    assert rig_security_multiplier(-0.2, is_reaction=False) == 2.1

    assert rig_security_multiplier(0.9, is_reaction=True) == 1.0
    assert rig_security_multiplier(0.3, is_reaction=True) == 1.0
    assert rig_security_multiplier(-0.2, is_reaction=True) == 1.1


def test_ec_rig_security_multiplier_uses_rounded_not_raw_security():
    # Confirmed real bug: EVE classifies a system by its *rounded*, displayed
    # security status (CCP's own system-security dev docs), not the SDE's
    # raw multi-decimal true-sec. A system with raw true-sec 0.45-0.4999...
    # displays and is classified as 0.5 = highsec - the old code compared the
    # raw value directly and would have wrongly given such a system the
    # 1.9x lowsec rig bonus instead of the correct 1.0x (no bonus).
    assert rig_security_multiplier(0.45, is_reaction=False) == 1.0
    assert rig_security_multiplier(0.449, is_reaction=False) == 1.9  # rounds to 0.4, still lowsec
    assert rig_security_multiplier(0.4999, is_reaction=False) == 1.0


def _fake_quote(type_id, buy=100.0, sell=200.0):
    return CurrentPrice(type_id=type_id, updated="2026-01-01", buy=buy, sell=sell)


def test_margin_home_computes_net_of_market_fees():
    cfg = ProductionConfig(market_fees=0.05)
    home = {1: _fake_quote(1, sell=200.0)}

    # sell_price = 200 * 0.95 = 190; margin = (190 - 100) / 100 = 0.9
    assert engine.margin_home(1, 100.0, home, cfg) == pytest.approx(0.9)


def test_margin_home_none_when_no_home_sell_quote():
    cfg = ProductionConfig()
    assert engine.margin_home(1, 100.0, {}, cfg) is None


def test_margin_home_none_when_build_cost_invalid():
    cfg = ProductionConfig()
    home = {1: _fake_quote(1, sell=200.0)}

    assert engine.margin_home(1, None, home, cfg) is None
    assert engine.margin_home(1, 0.0, home, cfg) is None


def test_margin_jita_nets_fees_and_export_haul_cost(monkeypatch):
    cfg = ProductionConfig(market_fees=0.05, haul_cost_per_m3=10.0)
    jita = {1: _fake_quote(1, sell=200.0)}
    monkeypatch.setattr(engine, "_haul_volume", lambda *a, **k: 2.0)

    # sell_price = 200*0.95 - 10*2 = 190 - 20 = 170; margin = (170-100)/100 = 0.7
    assert engine.margin_jita(1, 100.0, jita, cfg) == pytest.approx(0.7)


def test_margin_jita_none_when_no_jita_sell_quote(monkeypatch):
    cfg = ProductionConfig()
    monkeypatch.setattr(engine, "_haul_volume", lambda *a, **k: 1.0)

    assert engine.margin_jita(1, 100.0, {}, cfg) is None


def test_build_margin_dispatches_to_jita_when_jita_target_set(monkeypatch):
    cfg = ProductionConfig()
    monkeypatch.setattr(engine, "margin_home", lambda *a, **k: 0.1)
    monkeypatch.setattr(engine, "margin_jita", lambda *a, **k: 0.9)

    assert engine._build_margin(1, 100.0, jita_target=5.0, home={}, jita={}, cfg=cfg) == 0.9


def test_build_margin_dispatches_to_home_when_no_jita_target(monkeypatch):
    cfg = ProductionConfig()
    monkeypatch.setattr(engine, "margin_home", lambda *a, **k: 0.1)
    monkeypatch.setattr(engine, "margin_jita", lambda *a, **k: 0.9)

    assert engine._build_margin(1, 100.0, jita_target=None, home={}, jita={}, cfg=cfg) == 0.1
    assert engine._build_margin(1, 100.0, jita_target=0.0, home={}, jita={}, cfg=cfg) == 0.1


class _FakeGmClient:
    """Stub for GoonmetricsClient - avoids a real Goonmetrics history call in
    discover_build_candidates' second (post-margin-gate) pass. `movement_by_type`
    maps type_id -> list of daily movement values; a type_id with no entry has
    no history at all (daily_movement/potential_daily_profit both 0.0), same as
    a genuinely untraded item would."""
    def __init__(self, movement_by_type: dict[int, list[float]] | None = None):
        self.movement_by_type = movement_by_type or {}

    def price_history_chunked(self, region_id, type_ids):
        from eve_trader.goonmetrics_client import HistoryPoint
        points = []
        for type_id in type_ids:
            for m in self.movement_by_type.get(type_id, []):
                points.append(HistoryPoint(region_id=region_id, type_id=type_id, date="2026-01-01",
                                            min_price=0.0, max_price=0.0, avg_price=0.0,
                                            movement=m, num_orders=1))
        return points


class _FakePlanContext:
    """Stub for engine._PlanContext - avoids real ESI/Goonmetrics calls
    (network) for tests that only care about discover_build_candidates'
    scanning/filtering/sorting logic, not _PlanContext's own construction.
    Pre-populates a plausible (buy != sell, buy > 0) home quote for type_ids
    1-3 - discover_build_candidates now requires that before even looking at
    margin (see MAX_PLAUSIBLE_BUILD_MARGIN's buy==sell synthetic-price
    rejection), so every test needs one unless it's specifically testing
    that rejection."""
    def __init__(self, cfg):
        self.stock_targets = [(1, "Already Tracked", 0, None, None)]
        self.manual_stock = {}
        self.manual_overrides = {}
        self.selected_decryptors = {}
        self.home = {tid: _fake_quote(tid) for tid in (1, 2, 3)}
        self.jita = {}
        self.cost_indices = {}
        self.adjusted_prices = {}


@pg_helpers.postgres_required()
def test_discover_build_candidates_skips_existing_stock_targets(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Already Tracked", 1.0, 100, None, 7),  # type_id 1 - already a stock target, must be skipped
        (2, "New Widget", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)

    cfg = ProductionConfig(min_margin=0.15)
    results = engine.discover_build_candidates(cfg, client=_FakeGmClient())

    assert [r["type_id"] for r in results] == [2]


@pg_helpers.postgres_required()
def test_discover_build_candidates_skips_non_manufacturable_items(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Raw Ore", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))  # no blueprint at all

    results = engine.discover_build_candidates(ProductionConfig())

    assert results == []


@pg_helpers.postgres_required()
def test_discover_build_candidates_rejects_buy_equals_sell_as_synthetic_price(monkeypatch, tenant):
    # Confirmed live: 'Racket' Light Neutron Blaster I showed buy=sell=
    # 689083.36 to the ISK - a real independent buy-side/sell-side order
    # book essentially never coincides exactly, so this is treated as a
    # synthetic/fallback price (no real market for this item), not a
    # genuine live quote.
    class FakeContextWithDegeneratePrice(_FakePlanContext):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.home = {2: _fake_quote(2, buy=100.0, sell=100.0)}  # buy == sell

    monkeypatch.setattr(engine, "_PlanContext", FakeContextWithDegeneratePrice)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Suspicious Item", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 50.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)

    results = engine.discover_build_candidates(ProductionConfig(min_margin=0.15))

    assert results == []


@pg_helpers.postgres_required()
def test_discover_build_candidates_applies_min_margin_gate(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Marginal Widget", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.05)  # below min_margin

    results = engine.discover_build_candidates(ProductionConfig(min_margin=0.15))

    assert results == []


@pg_helpers.postgres_required()
def test_discover_build_candidates_rejects_implausibly_high_margins(monkeypatch, tenant):
    # Confirmed real CCP SDE data quirk: reward/event ships (Gnosis, Praxis,
    # Sunesis, Metamorphosis) have a real published blueprint with a
    # vestigial 1-material placeholder list, producing a nonsensical
    # near-zero build cost and a billions-of-percent margin - must be
    # filtered out, not ranked #1.
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Vestigial Placeholder Ship", 1.0, 100, None, 6),
        (3, "Genuine Opportunity", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    margins = {2: 50000.0, 3: 0.35}  # item 2: absurd (5,000,000%), item 3: a real, plausible 35%
    monkeypatch.setattr(engine, "_build_margin", lambda type_id, *a, **k: margins[type_id])

    results = engine.discover_build_candidates(ProductionConfig(min_margin=0.15), client=_FakeGmClient())

    assert [r["type_id"] for r in results] == [3]


def test_build_material_tree_recurses_until_a_bought_leaf(monkeypatch):
    sde_types = {1: (1, 1, "Widget", 1.0, 1, None, None), 2: (2, 1, "Raw Mineral", 1.0, 1, None, None)}
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: sde_types.get(type_id))

    def fake_classify(type_id):
        if type_id == 1:
            return ("Tech I", (10, 1, 1))  # blueprint_id=10, activity_id=1, 1 unit/run
        return ("Input", None)  # type 2 has no blueprint - a bought leaf
    monkeypatch.setattr(engine, "classify_activity", fake_classify)
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))  # no ME reduction
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda bp_id, activity_id: [(2, 5.0)])

    tree = engine.build_material_tree(1, quantity=3, cfg=ProductionConfig(), home={}, jita={},
                                       selected_decryptors={}, t2_memo={})

    assert tree["type_id"] == 1
    assert tree["type_name"] == "Widget"
    assert tree["quantity"] == 3
    assert tree["activity"] == "Tech I"
    assert len(tree["children"]) == 1
    child = tree["children"][0]
    assert child["type_id"] == 2
    assert child["type_name"] == "Raw Mineral"
    assert child["activity"] == "Buy"
    assert child["quantity"] == 15  # 3 runs * 5 base_qty, no ME reduction
    assert child["children"] == []


@pg_helpers.postgres_required()
def test_discover_build_candidates_sorts_by_potential_daily_profit_not_margin(monkeypatch, tenant):
    # Confirmed with the user: margin alone isn't a useful ranking - a huge
    # margin nobody actually trades is worthless. Item 3 has the higher raw
    # margin (80% vs 20%) but zero observed market movement (nobody's buying
    # it); item 2's lower margin still beats it once real daily liquidity is
    # factored in - it must rank first, not item 3.
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Modest Margin, Liquid", 1.0, 100, None, 7),
        (3, "High Margin, Illiquid", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    margins = {2: 0.20, 3: 0.80}
    monkeypatch.setattr(engine, "_build_margin", lambda type_id, *a, **k: margins[type_id])
    client = _FakeGmClient({2: [50000.0, 60000.0]})  # item 3 has no history at all -> 0 movement

    results = engine.discover_build_candidates(ProductionConfig(min_margin=0.15), client=client)

    assert [r["type_id"] for r in results] == [2, 3]
    assert results[0]["potential_daily_profit"] > 0
    assert results[1]["potential_daily_profit"] == 0.0
    assert results[1]["margin"] > results[0]["margin"]  # the raw margin ranking would have gotten this backwards


@pg_helpers.postgres_required()
def test_discover_build_candidates_computes_potential_daily_profit_from_movement_and_margin(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Widget", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)  # 50%
    client = _FakeGmClient({2: [1000.0, 2000.0, 3000.0]})  # avg movement = 2000.0 units/day

    results = engine.discover_build_candidates(ProductionConfig(min_margin=0.15), client=client)

    assert results[0]["daily_movement"] == 2000.0
    # potential_daily_profit = daily_movement (units) * margin * build_cost = 2000 * 0.5 * 100
    assert round(results[0]["potential_daily_profit"], 6) == 100000.0


@pg_helpers.postgres_required()
def test_discover_build_candidates_applies_min_daily_profit_gate(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Untraded Widget", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)  # clears min_margin easily
    client = _FakeGmClient({})  # no trading history at all -> potential_daily_profit == 0.0

    cfg = ProductionConfig(min_margin=0.15, min_daily_profit=1.0)  # requires > 0
    results = engine.discover_build_candidates(cfg, client=client)

    assert results == []


@pg_helpers.postgres_required()
def test_discover_build_candidates_reuses_cache_within_ttl(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    scan_calls = []
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: (
        scan_calls.append(1),
        [(2, "Widget", 1.0, 100, None, 7)],
    )[1])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)
    cfg = ProductionConfig(min_margin=0.15)

    first = engine.discover_build_candidates(cfg, client=_FakeGmClient())
    # Second call, even with completely different (would-be) monkeypatched
    # results, must reuse the cache from the first call instead of re-scanning.
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: (_ for _ in ()).throw(
        AssertionError("should not re-scan - cache should have been reused")))
    second = engine.discover_build_candidates(cfg, client=_FakeGmClient())

    assert scan_calls == [1]  # only the first call actually scanned
    assert second == first


@pg_helpers.postgres_required()
def test_discover_build_candidates_top_n_slices_the_cached_list(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (2, "Widget A", 1.0, 100, None, 7),
        (3, "Widget B", 1.0, 100, None, 7),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)
    cfg = ProductionConfig(min_margin=0.15)

    full = engine.discover_build_candidates(cfg, top_n=200, client=_FakeGmClient())
    sliced = engine.discover_build_candidates(cfg, top_n=1, client=_FakeGmClient())

    assert len(full) == 2
    assert sliced == full[:1]


@pg_helpers.postgres_required()
def test_invalidate_discover_cache_forces_a_rescan(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    scan_calls = []
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: (
        scan_calls.append(1),
        [(2, "Widget", 1.0, 100, None, 7)],
    )[1])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)
    cfg = ProductionConfig(min_margin=0.15)

    engine.discover_build_candidates(cfg, client=_FakeGmClient())
    engine.invalidate_discover_cache()
    engine.discover_build_candidates(cfg, client=_FakeGmClient())

    assert scan_calls == [1, 1]  # scanned again after invalidation


@pg_helpers.postgres_required()
def test_invalidate_discover_cache_default_only_clears_the_current_tenant(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    engine._discover_cache[tenant_a] = [{"item": "a"}]
    engine._discover_cache[tenant_b] = [{"item": "b"}]

    with storage.tenant_context(tenant_a):
        engine.invalidate_discover_cache()

    assert tenant_a not in engine._discover_cache
    assert tenant_b in engine._discover_cache  # untouched


@pg_helpers.postgres_required()
def test_invalidate_discover_cache_all_tenants_clears_every_tenant(tenant_pair):
    # GitHub issue #54's own follow-up bug (found in code review of PR #70):
    # admin.do_refresh_sde() is a global, cross-tenant action - the SDE
    # blueprint/material data every tenant's cache was built from can change,
    # not just the calling admin's own. Confirmed real gap: before this,
    # invalidate_discover_cache() had no way to clear anything but the
    # calling tenant's own entry, so every other tenant kept serving stale
    # discover results for up to the full cache TTL after a global refresh.
    tenant_a, tenant_b = tenant_pair
    engine._discover_cache[tenant_a] = [{"item": "a"}]
    engine._discover_cache[tenant_b] = [{"item": "b"}]

    engine.invalidate_discover_cache(all_tenants=True)

    assert engine._discover_cache == {}


@pg_helpers.postgres_required()
def test_invalidate_ship_margin_cache_all_tenants_clears_every_tenant(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    engine._ship_margin_cache[tenant_a] = [{"item": "a"}]
    engine._ship_margin_cache[tenant_b] = [{"item": "b"}]

    engine.invalidate_ship_margin_cache(all_tenants=True)

    assert engine._ship_margin_cache == {}


@pg_helpers.postgres_required()
def test_discover_build_candidates_concurrent_calls_do_not_double_scan(monkeypatch, tenant):
    # A cold cache hit by two threads at once (e.g. the background scheduler
    # and a user's own browser request landing together) must serialize on
    # _discover_cache_lock - the loser should reuse the winner's fresh cache,
    # not redundantly repeat the (slow, real-world ~19,400-item) scan itself.
    import threading
    import time as time_module

    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    scan_calls = []

    def slow_load():
        scan_calls.append(1)
        time_module.sleep(0.2)  # widen the race window so both threads are mid-cache-check together
        return [(2, "Widget", 1.0, 100, None, 7)]

    monkeypatch.setattr(storage, "load_sde_types_with_market_group", slow_load)
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)
    cfg = ProductionConfig(min_margin=0.15)

    results: list = [None, None]

    def call(i):
        # contextvars (the `tenant` fixture's storage.tenant_context) don't
        # propagate to a new thread automatically (unlike asyncio tasks) -
        # each spawned thread needs its own explicit set_current_tenant,
        # using the same tenant id the main thread's `tenant` fixture set.
        with storage.tenant_context(tenant):
            results[i] = engine.discover_build_candidates(cfg, client=_FakeGmClient())

    t1 = threading.Thread(target=call, args=(0,))
    t2 = threading.Thread(target=call, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert scan_calls == [1]  # only one thread actually performed the scan
    assert results[0] == results[1]


@pg_helpers.postgres_required()
def test_discover_build_candidates_cache_is_isolated_per_tenant(monkeypatch, tenant_pair):
    # GitHub issue #54 (real cross-tenant data leak, found in a
    # full-codebase audit 2026-08-21): _discover_cache used to be a single
    # process-global value shared by every tenant - the first tenant to call
    # discover_build_candidates got their scan served to every other tenant
    # for up to _DISCOVER_CACHE_TTL seconds. Two different tenants calling
    # within that window must each get their own scan, never the other's.
    tenant_a, tenant_b = tenant_pair
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    scan_calls = []

    def load_sde_types():
        scan_calls.append(1)
        # A different type_name each call, so the two tenants' results are
        # trivially distinguishable - if the cache leaked, tenant B would
        # see tenant A's "Tenant A Widget" instead of its own scan.
        name = f"Tenant {'A' if len(scan_calls) == 1 else 'B'} Widget"
        return [(2, name, 1.0, 100, None, 7)]

    monkeypatch.setattr(storage, "load_sde_types_with_market_group", load_sde_types)
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)
    cfg = ProductionConfig(min_margin=0.15)

    with storage.tenant_context(tenant_a):
        result_a = engine.discover_build_candidates(cfg, client=_FakeGmClient())
    with storage.tenant_context(tenant_b):
        result_b = engine.discover_build_candidates(cfg, client=_FakeGmClient())

    assert scan_calls == [1, 1]  # each tenant triggered its own scan, neither reused the other's
    assert result_a[0]["type_name"] == "Tenant A Widget"
    assert result_b[0]["type_name"] == "Tenant B Widget"

    # Re-calling within the TTL window still correctly reuses each tenant's
    # own cache (the fix isn't "never cache", just "cache per tenant").
    with storage.tenant_context(tenant_a):
        result_a_again = engine.discover_build_candidates(cfg, client=_FakeGmClient())
    assert scan_calls == [1, 1]  # no third scan - tenant A's own cache was reused
    assert result_a_again == result_a


# ---------------------------------------------------------------------- _haul_volume
def test_haul_volume_uses_flight_volume_for_non_ship_non_module_types(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Tritanium", 0.01, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 4)  # Material

    assert engine._haul_volume(34, ProductionConfig()) == 0.01


def test_haul_volume_uses_packaged_volume_for_ships(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 25, "Rifter", 27289.0, 1, 64, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: engine.SHIP_CATEGORY_ID)
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: 2500.0)

    assert engine._haul_volume(587, ProductionConfig()) == 2500.0


def test_haul_volume_uses_packaged_volume_for_capital_modules(monkeypatch):
    # GitHub issue #11 (still broken after the first "shipped" fix): capital
    # modules (e.g. Capital Shield Booster I, SDE flight volume 4000) also
    # shrink when packaged (confirmed live via ESI: packaged_volume=1000) -
    # not just ships.
    monkeypatch.setattr(storage, "get_sde_type",
                         lambda type_id: (type_id, 40, "Capital Shield Booster I", 4000.0, 1, 778, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: engine.MODULE_CATEGORY_ID)
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: 1000.0)

    assert engine._haul_volume(20703, ProductionConfig()) == 1000.0


def test_haul_volume_looks_up_esi_once_then_caches_for_modules(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type",
                         lambda type_id: (type_id, 40, "Capital Shield Booster I", 4000.0, 1, 778, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: engine.MODULE_CATEGORY_ID)
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: None)  # not cached yet
    cached = {}
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: cached.setdefault(type_id, v))

    from eve_trader import esi_client
    monkeypatch.setattr(esi_client.ESIClient, "get_packaged_volume", lambda self, type_id: 1000.0)

    assert engine._haul_volume(20703, ProductionConfig()) == 1000.0
    assert cached == {20703: 1000.0}


def test_haul_volume_returns_none_when_type_not_in_sde(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)

    assert engine._haul_volume(999999, ProductionConfig()) is None


# ---------------------------------------------------------------- discover_ship_margins
# Minimal stand-in for the real sde_market_groups tree (market_group_id,
# parent_group_id, name) - 1612 "Special Edition Ships" and its child 1699
# "Special Edition Cruisers" mirror the real subtree confirmed live
# (2026-08-19) to contain every ship named in GitHub issue #7; 1379 "Navy
# Faction" is a sibling subtree that must stay unaffected (Faction ships like
# Navy Issue battleships should still show up on the Margin tab).
_FAKE_MARKET_GROUPS = [
    (1612, 4, "Special Edition Ships"),
    (1699, 1612, "Special Edition Cruisers"),
    (1379, 1378, "Navy Faction"),
]


def _stub_market_groups(monkeypatch):
    monkeypatch.setattr(storage, "load_sde_market_groups", lambda: _FAKE_MARKET_GROUPS)


@pg_helpers.postgres_required()
def test_discover_ship_margins_only_includes_ships(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    _stub_market_groups(monkeypatch)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Some Ship", 1.0, 100, None, engine.SHIP_CATEGORY_ID),
        (2, "Some Module", 1.0, 100, None, 7),  # not a ship - must be excluded
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)

    results = engine.discover_ship_margins(ProductionConfig())

    assert [r["type_id"] for r in results] == [1]


@pg_helpers.postgres_required()
def test_discover_ship_margins_includes_already_tracked_stock_targets(monkeypatch, tenant):
    # Deliberately different from discover_build_candidates - the Margin page
    # is an information browser, not a candidate filter, so an already-
    # tracked stock target (type_id 1 in _FakePlanContext.stock_targets)
    # still appears here.
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    _stub_market_groups(monkeypatch)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Already Tracked Ship", 1.0, 100, None, engine.SHIP_CATEGORY_ID),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)

    results = engine.discover_ship_margins(ProductionConfig())

    assert [r["type_id"] for r in results] == [1]


@pg_helpers.postgres_required()
def test_discover_ship_margins_skips_non_manufacturable_ships(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    _stub_market_groups(monkeypatch)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Unbuildable Ship", 1.0, 100, None, engine.SHIP_CATEGORY_ID),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))  # no blueprint

    results = engine.discover_ship_margins(ProductionConfig())

    assert results == []


@pg_helpers.postgres_required()
def test_discover_ship_margins_skips_special_edition_ships(monkeypatch, tenant):
    # GitHub issue #7 (still broken after the first "shipped" fix): a ship
    # with a real, published blueprint but market_group_id under "Special
    # Edition Ships" (1612) shouldn't show as buildable, even though the
    # existing published-blueprint filter alone doesn't catch it.
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    _stub_market_groups(monkeypatch)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Guardian-Vexor", 1.0, 1699, None, engine.SHIP_CATEGORY_ID),  # under 1612 via 1699
        (2, "Vexor", 1.0, 76, None, engine.SHIP_CATEGORY_ID),  # ordinary ship market group
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)

    results = engine.discover_ship_margins(ProductionConfig())

    assert [r["type_id"] for r in results] == [2]


@pg_helpers.postgres_required()
def test_discover_ship_margins_keeps_navy_issue_ships(monkeypatch, tenant):
    # Faction ships (Navy Issue, market_group_id 1379 "Navy Faction") live in
    # a sibling subtree, not under "Special Edition Ships" - the user
    # explicitly wants these to keep showing (GitHub issue #7's own "auch
    # faction... schiffe" ask).
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    _stub_market_groups(monkeypatch)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Raven Navy Issue", 1.0, 1379, None, engine.SHIP_CATEGORY_ID),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)

    results = engine.discover_ship_margins(ProductionConfig())

    assert [r["type_id"] for r in results] == [1]


@pg_helpers.postgres_required()
def test_discover_ship_margins_populates_prices_and_both_margins(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)  # home[1]=(buy=100,sell=200), jita={}
    _stub_market_groups(monkeypatch)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Some Ship", 1.0, 100, None, engine.SHIP_CATEGORY_ID),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "margin_home", lambda *a, **k: 0.5)
    monkeypatch.setattr(engine, "margin_jita", lambda *a, **k: None)  # no Jita quote in _FakePlanContext

    results = engine.discover_ship_margins(ProductionConfig())

    assert results == [{
        "type_id": 1, "type_name": "Some Ship", "activity": "Tech I",
        "home_price": 200.0, "jita_price": None, "build_cost": 100.0,
        "margin_home": 0.5, "margin_jita": None, "meta_level": None,
    }]


@pg_helpers.postgres_required()
def test_discover_ship_margins_reuses_cache_within_ttl(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    _stub_market_groups(monkeypatch)
    scan_calls = []
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: (
        scan_calls.append(1),
        [(1, "Some Ship", 1.0, 100, None, engine.SHIP_CATEGORY_ID)],
    )[1])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    cfg = ProductionConfig()

    first = engine.discover_ship_margins(cfg)
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: (_ for _ in ()).throw(
        AssertionError("should not re-scan - cache should have been reused")))
    second = engine.discover_ship_margins(cfg)

    assert scan_calls == [1]
    assert second == first


@pg_helpers.postgres_required()
def test_invalidate_ship_margin_cache_forces_a_rescan(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    _stub_market_groups(monkeypatch)
    scan_calls = []
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: (
        scan_calls.append(1),
        [(1, "Some Ship", 1.0, 100, None, engine.SHIP_CATEGORY_ID)],
    )[1])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (999, 1, 1.0)))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    cfg = ProductionConfig()

    engine.discover_ship_margins(cfg)
    engine.invalidate_ship_margin_cache()
    engine.discover_ship_margins(cfg)

    assert scan_calls == [1, 1]


# ---------------------------------------------------------------- _expand_all
# BOM used by every test below:
#   ItemA -> 10x Common
#   ItemB -> 10x Middle -> 10x Common
#   Common -> 1x Mineral (raw, bp=None)
# Common is reachable at depth 1 (via ItemA) *and* depth 2 (via ItemB ->
# Middle) - the shared-material-at-different-depths shape that reproduces
# the overbuild-buffer double-counting bug (see _expand_all's docstring,
# "buffered_parents").
_EXPAND_ALL_BP = {
    1: (101, 1, 1),  # ItemA
    2: (102, 1, 1),  # ItemB
    3: (103, 1, 1),  # Common
    4: (104, 1, 1),  # Middle
}
_EXPAND_ALL_MATERIALS = {
    101: [(3, 10)],   # ItemA needs 10x Common
    102: [(4, 10)],   # ItemB needs 10x Middle
    104: [(3, 10)],   # Middle needs 10x Common
    103: [(5, 1)],    # Common needs 1x Mineral
}


@pytest.fixture
def _expand_all_bom(monkeypatch):
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: (
        ("Tech I", _EXPAND_ALL_BP[type_id]) if type_id in _EXPAND_ALL_BP else ("Input", None)
    ))
    monkeypatch.setattr(storage, "get_blueprint_materials",
                         lambda blueprint_id, activity_id: _EXPAND_ALL_MATERIALS.get(blueprint_id, []))
    # No ME/TE reduction, no owned BPO - material_mult stays exactly 1.0 so
    # expected totals are exact integers, not rounding-dependent.
    monkeypatch.setattr(engine, "_activity_mods", lambda activity, type_id, cfg, cost_indices, blueprint_id=None: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                         lambda type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth: "Buy" if bp is None else "Build")
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: 0.0)


def test_expand_all_does_not_double_count_overbuild_buffer_for_a_component_shared_at_different_depths(_expand_all_bom):
    # Real bug found via a user report ("Buy List total looks far too high") -
    # Common is reachable at depth 1 (from ItemA) and depth 2 (from ItemB via
    # Middle), landing it in two different rounds of _expand_all's breadth-
    # first walk. Before the buffered_parents fix, Common's *whole-tree*
    # overbuild buffer (component_overbuild * base_qty * material_mult *
    # base_runs[Common]) was added once per round it appeared in, not once
    # total - with hundreds of real stock targets sharing common minerals at
    # many different depths, this compounded into billions of units for a
    # base mineral instead of thousands.
    cfg = ProductionConfig(component_overbuild=0.5)
    stock_targets = [
        (1, "ItemA", 1, 0, 0),
        (2, "ItemB", 1, 0, 0),
    ]

    base_runs = engine._base_runs(cfg, {}, {}, {}, {}, {}, {}, {}, {}, stock_targets)
    assert base_runs == {1: 1.0, 2: 1.0, 3: 110.0, 4: 10.0}  # Common's whole-tree aggregate: 10 (via ItemA) + 100 (via ItemB->Middle)

    buy_totals, build_runs = engine._expand_all({1: 1.0, 2: 1.0}, cfg, {}, {}, {}, {}, {}, {}, {}, {}, base_runs)

    # Hand-verified expected values (see task notes): Common's own build
    # count still legitimately grows to 215 runs (its buffered quantity
    # cascades forward into its own material demand, which is correct - not
    # what this bug was about) - but Mineral must reflect exactly ONE
    # contribution of Common's buffer (55), not two (110).
    assert build_runs[(103, 1, 3)] == 215  # Common
    assert buy_totals[5] == 270  # Mineral - was 325 before the fix (one duplicate 55-unit buffer application)


def test_expand_all_still_pools_multiple_edges_to_the_same_material_within_one_round(_expand_all_bom):
    # The *other* half of the double-counting story (already handled before
    # this fix, confirmed still correct): two parents claiming the same
    # material in the *same* round must still only pool once, not double.
    cfg = ProductionConfig(component_overbuild=0.0)  # isolate pooling from the buffer math
    stock_targets = [(1, "ItemA", 1, 0, 0), (2, "ItemB", 1, 0, 0)]
    base_runs = engine._base_runs(cfg, {}, {}, {}, {}, {}, {}, {}, {}, stock_targets)

    buy_totals, build_runs = engine._expand_all({1: 1.0, 2: 1.0}, cfg, {}, {}, {}, {}, {}, {}, {}, {}, base_runs)

    # Common: 10 (ItemA, 1 run) + 100 (Middle, 10 runs) = 110, no buffer (overbuild=0).
    assert build_runs[(103, 1, 3)] == 110
    assert buy_totals[5] == 110  # Mineral: 1:1 with Common's runs, no buffer


# ------------------------------------------------------------ plan_asset_optimized
@pg_helpers.postgres_required()
def test_plan_asset_optimized_applies_the_same_overbuild_buffer_as_expand_all(monkeypatch, _expand_all_bom, tenant):
    # Real gap reported live (2026-08-15): "in der asset optimized buildlist
    # taucht nur eine reaction auf. in der normalen build list sehr viel
    # mehr" - plan_asset_optimized computed bare demand only, no
    # cfg.component_overbuild buffer, while plan_production's _expand_all
    # did - the two Baulisten must agree on *how much* to build (see
    # plan_asset_optimized's docstring), not just whether. Same BOM/cfg as
    # test_expand_all_does_not_double_count_overbuild_buffer_for_a_component_shared_at_different_depths
    # (ItemA -> 10x Common, ItemB -> 10x Middle -> 10x Common, Common -> 1x
    # Mineral) - Common's job_runs here must land on the exact same 215 that
    # _expand_all's build_runs produces for the identical inputs.
    stock_targets = [(1, "ItemA", 1, 0, 0), (2, "ItemB", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                         lambda type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth=0:
                         "Buy" if bp is None else "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)  # never excluded on margin grounds
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: None)

    cfg = ProductionConfig(component_overbuild=0.5)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert jobs_by_id[3].job_runs == 215  # Common - matches _expand_all's build_runs[(103, 1, 3)] exactly


@pg_helpers.postgres_required()
def test_plan_asset_optimized_jobs_include_margin_home(monkeypatch, _expand_all_bom, tenant):
    # GitHub issue #38: AssetPlanJob.margin comes from margin_home, never
    # margin_jita - see plan_production's own equivalent test for why
    # margin_home itself is mocked rather than exercised end-to-end here.
    stock_targets = [(1, "ItemA", 1, 0, 0), (2, "ItemB", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                         lambda type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth=0:
                         "Buy" if bp is None else "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: None)
    monkeypatch.setattr(engine, "margin_home", lambda type_id, *a, **k: 0.42 if type_id == 3 else None)

    cfg = ProductionConfig(component_overbuild=0.5)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert jobs_by_id[3].margin == pytest.approx(0.42)


@pg_helpers.postgres_required()
def test_plan_asset_optimized_pools_without_a_buffer_when_overbuild_is_zero(monkeypatch, _expand_all_bom, tenant):
    # Control case (component_overbuild=0.0) isolating pooling from the
    # buffer math, mirroring
    # test_expand_all_still_pools_multiple_edges_to_the_same_material_within_one_round.
    stock_targets = [(1, "ItemA", 1, 0, 0), (2, "ItemB", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                         lambda type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth=0:
                         "Buy" if bp is None else "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: None)

    cfg = ProductionConfig(component_overbuild=0.0)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert jobs_by_id[3].job_runs == 110  # Common: 10 (ItemA) + 100 (Middle), no buffer


@pg_helpers.postgres_required()
def test_plan_asset_optimized_computes_stock_coverage_for_stock_targets_and_intermediates_alike(monkeypatch, _expand_all_bom, tenant):
    # Display-only "how much of this item itself is already on hand" signal
    # (user request 2026-08-15, refined after user pushback: "Blocked" only
    # tells you about *this item's own materials*, not about how much of
    # *this item itself* is in stock - two different questions). A stock
    # target (ItemA) nets against its configured backup/home/Jita goal; a
    # pure intermediate material (Common - only ever reached as ItemA's
    # blueprint material, no goal of its own) nets against the pooled
    # (bare + overbuild buffer) demand of the round it was queued in instead
    # - same underlying question ("how much of what's wanted is here"), two
    # different denominators.
    stock_targets = [(1, "ItemA", 10, 0, 0)]  # backup_stock target = 10
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                         lambda type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth=0:
                         "Buy" if bp is None else "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: None)
    # ItemA has 4 of its 10-unit target on hand; Common has 18 of the 60
    # units ItemA's 6 runs demand this round (10 base_qty * 1.0 mult * 6
    # runs, overbuild=0); everything else (e.g. Mineral) has none.
    monkeypatch.setattr(engine, "_current_stock",
                         lambda type_id, *a, **k: {1: 4.0, 3: 18.0}.get(type_id, 0.0))

    cfg = ProductionConfig(component_overbuild=0.0)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert round(jobs_by_id[1].stock_coverage, 2) == 0.4  # 4 of 10 target already on hand
    assert round(jobs_by_id[3].stock_coverage, 2) == 0.3  # 18 of 60 this-round demand already on hand


@pg_helpers.postgres_required()
def test_plan_asset_optimized_readiness_ignores_in_progress_industry_jobs(monkeypatch, tenant):
    # Real bug reported live (2026-08-15): user physically tried to queue
    # jobs off the Asset-Optimized list and couldn't, because a shared
    # material (Sylramic Fibers in their case) was already fully claimed by
    # an in-progress reaction - _current_stock counts that job's *eventual*
    # output as available ("as good as in stock" - correct for job *sizing*,
    # so as not to plan to build a redundant batch), but two DIFFERENT jobs
    # both needing that same material both showed "Ready Now" against a
    # batch that doesn't physically exist yet and can only ever satisfy one
    # of them. Readiness must be judged against _stock_on_hand (physically
    # there right now) instead, with its own ledger, independent of whatever
    # _current_stock (used for job sizing) reports.
    #
    # ItemP and ItemQ each need 10x Common (a raw/Buy-only material - no
    # blueprint of its own, so it never becomes a job itself here). 20 units
    # of Common are "incoming" (an in-progress job - _current_stock reports
    # 20, plenty to cover both P and Q's combined 20-unit demand for SIZING
    # purposes) but NONE of it is physically on hand yet (_stock_on_hand
    # reports 0) - so neither P nor Q should show as ready to start now.
    bp_by_id = {1: (101, 1, 1), 2: (102, 1, 1)}
    materials_by_bp = {101: [(3, 10)], 102: [(3, 10)]}
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: (
        ("Tech I", bp_by_id[type_id]) if type_id in bp_by_id else ("Input", None)
    ))
    monkeypatch.setattr(storage, "get_blueprint_materials",
                         lambda blueprint_id, activity_id: materials_by_bp.get(blueprint_id, []))
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                         lambda type_id, cfg, home, jita, manual_overrides, cost_memo, bp, depth=0:
                         "Buy" if bp is None else "Build")
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: None)
    monkeypatch.setattr(engine, "_current_stock", lambda type_id, *a, **k: 20.0 if type_id == 3 else 0.0)
    monkeypatch.setattr(engine, "_stock_on_hand", lambda type_id, *a, **k: 0.0)  # nothing physically here yet

    stock_targets = [(1, "ItemP", 1, 0, 0), (2, "ItemQ", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))

    cfg = ProductionConfig(component_overbuild=0.0)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert jobs_by_id[1].runs_ready_now == 0  # not actually startable - Common isn't physically here
    assert jobs_by_id[2].runs_ready_now == 0


@pg_helpers.postgres_required()
def test_plan_asset_optimized_recommends_slot_split_for_eligible_categories_only(monkeypatch, tenant):
    # User request 2026-08-15: recommend how many free character job slots to
    # split a job's ready runs across, but only for Reactions/Advanced
    # Components/Capital Components - not Equipment/Ships/etc. Only one
    # eligible job in the pool here, so the proportional allocator (see the
    # dedicated fairness test below) degenerates to "give it the whole pool,
    # capped at its own ready runs" - ItemP (Reactions, 5 ready runs) and
    # ItemQ (Equipment, 5 ready runs) share the same setup otherwise, so only
    # ItemP should get a recommendation at all.
    bp_by_id = {1: (101, 11, 1), 2: (102, 11, 1)}  # activity_id 11 = Reaction
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Reaction", bp_by_id[type_id]))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: "Reactions" if type_id == 1 else "Equipment")
    monkeypatch.setattr(engine, "character_slot_overview", lambda: [
        CharacterSlotRow(character_name="Alice", job_type="Reactions", total_slots=3, used_slots=0, free_slots=3),
        CharacterSlotRow(character_name="Alice", job_type="Manufacturing", total_slots=5, used_slots=0, free_slots=5),
    ])

    stock_targets = [(1, "ItemP", 5, 0, 0), (2, "ItemQ", 5, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))

    cfg = ProductionConfig(component_overbuild=0.0)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert jobs_by_id[1].runs_ready_now == 5  # no materials needed - fully ready
    assert jobs_by_id[1].recommended_slots == 3  # capped by the 3 free Reaction slots, not the 5 ready runs
    assert jobs_by_id[2].recommended_slots is None  # Equipment - not an eligible category


def test_free_slots_by_category_excludes_flagged_characters(monkeypatch):
    # GitHub issue #39: a character flagged excluded_from_planning (e.g. an
    # alt kept registered for ESI sync/asset visibility but not meant to run
    # production jobs) must not contribute to the shared free-slot pool.
    monkeypatch.setattr(engine, "character_slot_overview", lambda: [
        CharacterSlotRow(character_name="Alice", job_type="Reactions", total_slots=3, used_slots=0, free_slots=3),
        CharacterSlotRow(character_name="Bob", job_type="Reactions", total_slots=5, used_slots=0, free_slots=5,
                          excluded_from_planning=True),
    ])

    totals = engine._free_slots_by_category()

    assert totals == {"Reactions": 3}  # Bob's 5 free slots excluded entirely


def test_allocate_slots_proportionally_splits_by_relative_size_with_no_remainder():
    # weight == cap here (no time-vs-run-count distinction being tested).
    result = engine._allocate_slots_proportionally([(1, 10.0, 10), (2, 20.0, 20), (3, 70.0, 70)], available=10)

    assert result == {1: 1, 2: 2, 3: 7}
    assert sum(result.values()) == 10


def test_allocate_slots_proportionally_distributes_the_rounding_remainder():
    # 3 equal-sized jobs sharing 10 slots: 10/3 = 3.33 each - floor gives 3
    # each (9 total), the 1 leftover slot (from rounding down) must still be
    # handed out, not silently dropped.
    result = engine._allocate_slots_proportionally([(1, 5.0, 5), (2, 5.0, 5), (3, 5.0, 5)], available=10)

    assert sum(result.values()) == 10
    assert all(v >= 3 for v in result.values())
    assert sorted(result.values()) == [3, 3, 4]


def test_allocate_slots_proportionally_never_exceeds_a_jobs_own_need():
    # Real user correction (2026-08-16): the whole point is that the pool
    # gets split *across* competing jobs - but a job also can't usefully be
    # handed more slots than it has ready runs to fill them with. A huge
    # pool (100) against small caps (2+3+5=10 total) must cap at each job's
    # own need, not force-allocate the full pool regardless.
    result = engine._allocate_slots_proportionally([(1, 2.0, 2), (2, 3.0, 3), (3, 5.0, 5)], available=100)

    assert result == {1: 2, 2: 3, 3: 5}  # each capped at its own need, nothing wasted trying to exceed it


def test_allocate_slots_proportionally_empty_or_zero_pool():
    assert engine._allocate_slots_proportionally([], available=10) == {}
    assert engine._allocate_slots_proportionally([(1, 5.0, 5)], available=0) == {1: 0}


def test_allocate_slots_proportionally_weights_by_time_not_run_count():
    # Second real user correction (2026-08-16): weight is time_needed
    # (runs_ready_now x per-run time), not runs_ready_now alone - a job's
    # own runs_ready_now is still its *cap* (3rd tuple element), but no
    # longer also its weight. Job A: 10 ready runs at 100s each (weight
    # 1000, cap 10). Job B: 90 ready runs at 1s each (weight 90, cap 90).
    # Under old run-count weighting (10 vs 90) B would dominate (9 vs 1) -
    # under time weighting (1000 vs 90) A dominates instead, since A's ready
    # runs need far more total job-time to clear.
    result = engine._allocate_slots_proportionally([(1, 1000.0, 10), (2, 90.0, 90)], available=10)

    assert result == {1: 9, 2: 1}
    assert sum(result.values()) == 10


@pg_helpers.postgres_required()
def test_plan_asset_optimized_splits_slots_proportionally_across_competing_ready_jobs(monkeypatch, tenant):
    # Real user correction (2026-08-16): the pool must be split *across*
    # every ready job sharing it, not each job recommended the pool as if it
    # were the only one wanting it - three jobs with 10/20/70 ready runs
    # sharing 10 Reaction slots must split proportionally (1/2/7 - see the
    # dedicated _allocate_slots_proportionally tests above for the algorithm
    # itself), summing to the real pool total, not "10" shown three times.
    bp_by_id = {1: (101, 11, 1), 2: (102, 11, 1), 3: (103, 11, 1)}
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Reaction", bp_by_id[type_id]))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: "Reactions")
    monkeypatch.setattr(engine, "character_slot_overview", lambda: [
        CharacterSlotRow(character_name="Alice", job_type="Reactions", total_slots=10, used_slots=0, free_slots=10),
    ])

    stock_targets = [(1, "ItemA", 10, 0, 0), (2, "ItemB", 20, 0, 0), (3, "ItemC", 70, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))

    cfg = ProductionConfig(component_overbuild=0.0)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert jobs_by_id[1].recommended_slots == 1
    assert jobs_by_id[2].recommended_slots == 2
    assert jobs_by_id[3].recommended_slots == 7
    total = sum(j.recommended_slots for j in jobs_by_id.values())
    assert total == 10  # sums to the real pool, not each job claiming all 10 independently


@pg_helpers.postgres_required()
def test_plan_asset_optimized_weights_slot_split_by_job_time_not_run_count(monkeypatch, tenant):
    # Second real user correction (2026-08-16): "es soll ... die benötigte
    # zeit gewichtet werden. Jobs die mehr jobtime benötigen, sollen mehr
    # slots bekommen." ItemA has few ready runs (10) but each run takes 100s
    # (per-run time from get_blueprint_time); ItemB has many ready runs (90)
    # but each takes only 1s. By raw run count B would dominate (90 vs 10);
    # by total time needed A dominates instead (1000 vs 90 job-seconds) -
    # end to end through plan_asset_optimized, not just the allocator unit.
    bp_by_id = {1: (101, 11, 1), 2: (102, 11, 1)}
    time_by_bp = {101: 100.0, 102: 1.0}
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Reaction", bp_by_id[type_id]))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: time_by_bp[blueprint_id])
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "job_category", lambda type_id: "Reactions")
    monkeypatch.setattr(engine, "character_slot_overview", lambda: [
        CharacterSlotRow(character_name="Alice", job_type="Reactions", total_slots=10, used_slots=0, free_slots=10),
    ])

    stock_targets = [(1, "ItemA", 10, 0, 0), (2, "ItemB", 90, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))

    cfg = ProductionConfig(component_overbuild=0.0)
    result = engine.plan_asset_optimized(cfg)

    jobs_by_id = {job.type_id: job for job in result["jobs"]}
    assert jobs_by_id[1].runs_ready_now == 10
    assert jobs_by_id[2].runs_ready_now == 90
    assert jobs_by_id[1].recommended_slots == 9  # fewer ready runs, but far more total job-time
    assert jobs_by_id[2].recommended_slots == 1  # more ready runs, but each is nearly instant
    assert sum(j.recommended_slots for j in jobs_by_id.values()) == 10


# ------------------------------------------------------------- plan_production
def _make_fake_plan_context(stock_targets, manual_stock=None):
    class _FakeCtx:
        def __init__(self, cfg):
            self.stock_targets = stock_targets
            self.manual_stock = manual_stock or {}
            self.manual_overrides = {}
            self.selected_decryptors = {}
            self.home = {}
            self.jita = {}
            self.cost_indices = {}
            self.adjusted_prices = {}
    return _FakeCtx


@pg_helpers.postgres_required()
def test_plan_production_drops_demand_entirely_when_build_margin_fails_min_margin(monkeypatch, tenant):
    # Real bug reported live: Ishtar/Golem (build margin 2.5%/10.7%, both
    # below the 15% default min_margin) showed up as ~10B ISK Buy List
    # entries purely because the tool used to fall back to force-buying an
    # unprofitable-to-build stock target instead of dropping its demand -
    # confirmed with the user (2026-08-01) that doesn't make sense ("wenn es
    # sich nicht lohnt sie zu bauen, wieso sollte ich sie kaufen?"): if
    # building isn't profitable, buying isn't assumed to be either, so the
    # item should appear in neither buy_list nor build_list at all.
    stock_targets = [
        (10, "GoodMargin", 1, 0, 0),
        (20, "BadMargin", 1, 0, 0),
    ]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (100 + type_id, 1, 1.0)))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    margins = {10: 0.50, 20: 0.05}  # GoodMargin clears min_margin=0.15, BadMargin doesn't
    monkeypatch.setattr(engine, "_build_margin", lambda type_id, *a, **k: margins[type_id])

    cfg = ProductionConfig(min_margin=0.15)
    result = engine.plan_production(cfg)

    assert {row.type_id for row in result["build_list"]} == {10}  # only GoodMargin was built
    assert {row.type_id for row in result["buy_list"]} == set()  # BadMargin was NOT bought as a fallback

    # Regardless of margin, the real shortfall is still reported for display
    # (InventoryRow/total_missing) - only the buy/build recommendation is gated.
    inventory_by_id = {row.type_id: row for row in result["inventory"]}
    assert inventory_by_id[20].total_missing == 1.0


@pg_helpers.postgres_required()
def test_plan_production_build_list_includes_margin_home(monkeypatch, tenant):
    # GitHub issue #38: BuildJobEntry.margin comes from margin_home, never
    # margin_jita - Production sells only at C-J. margin_home itself is
    # mocked here (its own pricing math has its own dedicated tests) - this
    # only confirms plan_production's BuildJobEntry construction actually
    # wires the field through with the right type_id/home/cfg.
    stock_targets = [(10, "Widget", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (110, 1, 1.0)))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 1.0)  # clears the min_margin gate
    monkeypatch.setattr(engine, "margin_home", lambda type_id, *a, **k: 0.9 if type_id == 10 else None)

    cfg = ProductionConfig(min_margin=0.0)
    result = engine.plan_production(cfg)

    build_by_id = {row.type_id: row for row in result["build_list"]}
    assert build_by_id[10].margin == pytest.approx(0.9)


@pg_helpers.postgres_required()
def test_plan_production_build_list_handles_tech_ii_item_via_t2_memo(monkeypatch, tenant):
    """Regression test for a real production outage (2026-08-30, commit
    846eef4): _tech_ii_mods' memo cache was widened from a 3-tuple to a
    4-tuple (business-logic audit's Tech III relic fix, commit d6af047), but
    two sites read t2_memo[...] directly - bypassing the _tech_ii_mods(...)
    call itself, so grepping for that call site never caught them - and
    still unpacked it as a 3-tuple, raising "ValueError: too many values to
    unpack (expected 3)" in production. Every existing plan_production test
    before this one mocked classify_activity to always return "Tech I",
    which never exercises the `product_activity == "Tech II" and
    product_type_id in t2_memo` branch at all - confirmed the actual gap
    that let this ship. _tech_ii_mods is deliberately NOT mocked here (only
    classify_activity is) so it runs for real and genuinely populates
    t2_memo via its own fallback path (no invention recipe exists for this
    fake type_id), matching exactly how the live crash happened."""
    stock_targets = [(10, "T2 Widget", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech II", (110, 1, 1.0)))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 1.0)
    monkeypatch.setattr(engine, "margin_home", lambda type_id, *a, **k: 0.9 if type_id == 10 else None)

    cfg = ProductionConfig(min_margin=0.0)
    result = engine.plan_production(cfg)  # must not raise ValueError

    assert {row.type_id for row in result["build_list"]} == {10}


@pg_helpers.postgres_required()
def test_plan_production_manual_override_bypasses_the_margin_gate(monkeypatch, tenant):
    # A manual Build/Buy override (storage.manual_build_buy) is an explicit
    # user decision - the margin gate must not second-guess it.
    stock_targets = [(20, "BadMarginButOverridden", 1, 0, 0)]
    ctx_cls = _make_fake_plan_context(stock_targets)

    class _FakeCtxWithOverride(ctx_cls):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.manual_overrides = {20: "Build"}

    monkeypatch.setattr(engine, "_PlanContext", _FakeCtxWithOverride)
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (100 + type_id, 1, 1.0)))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.01)  # far below min_margin

    cfg = ProductionConfig(min_margin=0.15)
    result = engine.plan_production(cfg)

    assert {row.type_id for row in result["build_list"]} == {20}


@pg_helpers.postgres_required()
def test_plan_production_buy_list_on_hand_pct_for_a_top_level_raw_material_target(monkeypatch, tenant):
    # Real feature request: the Buy List should show what % of an item's
    # total demand is already on hand, not just the net quantity left to buy.
    stock_targets = [(30, "PartiallyStocked", 100, 0, 0)]  # backup_stock=100
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))  # raw material, always "Buy"
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 30.0)  # 30 of the 100 target already on hand
    monkeypatch.setattr(engine.pricing, "buy_price", lambda *a, **k: 10.0)
    monkeypatch.setattr(engine.pricing, "buy_source", lambda *a, **k: "Jita")
    monkeypatch.setattr(engine, "_haul_volume", lambda *a, **k: 1.0)

    cfg = ProductionConfig()
    result = engine.plan_production(cfg)

    row = next(r for r in result["buy_list"] if r.type_id == 30)
    assert row.quantity == 70.0  # 100 target - 30 on hand
    assert round(row.on_hand_pct, 1) == 30.0  # 30 of 100 = 30% already on hand


@pg_helpers.postgres_required()
def test_plan_production_buy_list_on_hand_pct_for_a_recursively_reached_material(monkeypatch, tenant):
    # Same "on hand %" column, but for an item reached only as a shared
    # material (not itself a stock target) - gross_demand must be tracked
    # inside _expand_all's netting step too, not just for top-level targets.
    stock_targets = [(1, "ItemA", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))

    def fake_classify(type_id):
        return ("Tech I", (101, 1, 1.0)) if type_id == 1 else ("Input", None)
    monkeypatch.setattr(engine, "classify_activity", fake_classify)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 10)])  # ItemA needs 10x Mineral
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda type_id, *a, **k: "Build" if type_id == 1 else "Buy")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)

    def fake_current_stock(type_id, manual_stock, cfg, bp):
        return 4.0 if type_id == 2 else 0.0  # 4 of the 10 needed Mineral already on hand
    monkeypatch.setattr(engine, "_current_stock", fake_current_stock)
    monkeypatch.setattr(engine.pricing, "buy_price", lambda *a, **k: 10.0)
    monkeypatch.setattr(engine.pricing, "buy_source", lambda *a, **k: "Jita")
    monkeypatch.setattr(engine, "_haul_volume", lambda *a, **k: 1.0)

    cfg = ProductionConfig(component_overbuild=0.0, min_margin=0.15)
    result = engine.plan_production(cfg)

    row = next(r for r in result["buy_list"] if r.type_id == 2)
    assert row.quantity == 6.0  # 10 gross - 4 on hand
    assert round(row.on_hand_pct, 1) == 40.0  # 4 of 10 = 40%


# ---------------------------------------------------------------- logistics_status
def _build_job(type_id=1, category="Advanced Components", job_runs=1, decryptor=None):
    from eve_trader.production.models import BuildJobEntry
    return BuildJobEntry(type_id=type_id, type_name=f"Item{type_id}", blueprint_type_id=type_id + 100,
                          activity="Manufacturing", quantity=job_runs, job_runs=job_runs, job_time_seconds=100.0,
                          unit_build_cost=None, decryptor=decryptor, job_category=category)


def _stub_material_demand(monkeypatch, material_id=2, base_qty=10.0):
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(material_id, base_qty)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))


def test_logistics_status_nets_needed_against_available_at_assigned_location(monkeypatch):
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})
    _stub_material_demand(monkeypatch)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 3.0)

    rows = engine.logistics_status([_build_job()])

    assert len(rows) == 1
    row = rows[0]
    assert row.needed == 10.0
    assert row.available == 3.0
    assert row.missing == 7.0
    assert row.location_id == 1001


def test_logistics_status_skips_categories_with_no_assigned_location(monkeypatch):
    monkeypatch.setattr(storage, "load_category_locations", lambda: {})
    _stub_material_demand(monkeypatch)

    rows = engine.logistics_status([_build_job()])

    assert rows == []


def test_logistics_status_pull_from_hint_picks_richest_surplus_when_no_warehouse(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 1003: 5.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status([_build_job()], cfg)

    row = rows[0]
    assert row.missing == 7.0
    assert row.pull_from_location_id == 1002  # 20 > 5, richest surplus (neither has its own demand here)
    assert row.pull_from_available == 20.0


def test_logistics_status_prefers_warehouse_over_surplus(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=9000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 9000: 6.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status([_build_job()], cfg)

    row = rows[0]
    assert row.missing == 7.0
    # Warehouse (9000) only has 6, less than Capital Components' surplus of 20,
    # but it's still preferred - GitHub issue #4's explicit ask.
    assert row.pull_from_location_id == 9000
    assert row.pull_from_available == 6.0


def test_logistics_status_falls_back_to_surplus_when_warehouse_empty(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=9000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 9000: 0.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status([_build_job()], cfg)

    row = rows[0]
    assert row.pull_from_location_id == 1002
    assert row.pull_from_available == 20.0


def test_logistics_status_pull_from_hint_nets_other_locations_own_demand(monkeypatch):
    # Capital Components has more raw stock (20) than Equipment (5), but
    # Capital Components' own demand (18) eats almost all of it - Equipment's
    # smaller surplus (5, no demand of its own here) should win instead.
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 10.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 1003: 5.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components", job_runs=1.8)],
        cfg)

    row = next(r for r in rows if r.category == "Advanced Components")
    assert row.pull_from_location_id == 1003
    assert row.pull_from_available == 5.0


def test_logistics_status_no_pull_from_hint_when_nothing_missing(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    _stub_material_demand(monkeypatch)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 100.0)  # plenty everywhere

    rows = engine.logistics_status([_build_job()], cfg)

    assert rows[0].missing == 0.0
    assert rows[0].pull_from_location_id is None


# ------------------------------------------------------- distribution_recommendations
def test_distribution_recommendations_moves_from_source_to_shortest_category(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=2000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 2000: 50.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations([_build_job()], cfg)

    assert len(rows) == 1
    row = rows[0]
    assert row.type_id == 2
    assert row.from_location_id == 2000
    assert row.to_category == "Advanced Components"
    assert row.to_location_id == 1001
    assert row.quantity == 7.0  # 10 needed - 3 on hand


def test_distribution_recommendations_falls_back_to_home_location(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=3000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})
    _stub_material_demand(monkeypatch)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: {1001: 0.0, 3000: 50.0}[location_id])

    rows = engine.distribution_recommendations([_build_job()], cfg)

    assert rows[0].from_location_id == 3000


def test_distribution_recommendations_empty_without_any_source_configured(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})

    assert engine.distribution_recommendations([_build_job()], cfg) == []


def test_distribution_recommendations_covers_largest_shortfall_first_when_source_limited(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=2000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 10.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        # Advanced Components short by 8, Capital Components short by 3 - only 5 available at source, not enough for both
        return {1001: 2.0, 1002: 7.0, 2000: 5.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components")], cfg)

    assert len(rows) == 1  # only enough source stock for the larger shortfall
    assert rows[0].to_category == "Advanced Components"
    assert rows[0].quantity == 5.0


def test_distribution_recommendations_falls_back_to_surplus_when_warehouse_exhausted(monkeypatch):
    # Advanced Components needs 8, Capital Components needs 3, warehouse only has 5
    # (covers Advanced Components' larger shortfall in full) - the leftover 3 for
    # Capital Components should come from Equipment's surplus (stock beyond its
    # own demand), not go unfulfilled the way it used to before GitHub issue #4's
    # "also try surplus locations" follow-up.
    cfg = ProductionConfig(distribution_source_location_id=2000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 8.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        return {1001: 0.0, 1002: 5.0, 1003: 20.0, 2000: 8.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components")], cfg)

    warehouse_row = next(r for r in rows if r.from_location_id == 2000)
    assert warehouse_row.to_category == "Advanced Components"
    assert warehouse_row.quantity == 8.0
    surplus_row = next(r for r in rows if r.from_location_id == 1003)
    assert surplus_row.to_category == "Capital Components"
    assert surplus_row.quantity == 3.0


def test_distribution_recommendations_never_double_books_a_surplus_location(monkeypatch):
    # Two categories (Advanced Components, Capital Components) are both short
    # 10 of the same material with no warehouse configured. The only surplus
    # location (Equipment) has just 12 spare - not enough for both shortfalls
    # in full. Recomputing "surplus" independently per category (the bug an
    # earlier attempt at this shipped with) would recommend pulling up to 10
    # from Equipment for *each* category, 20 total, more than the 12 actually
    # sitting there. The combined recommended quantity out of Equipment must
    # never exceed its real surplus.
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 10.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        return {1001: 0.0, 1002: 0.0, 1003: 12.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components")], cfg)

    from_equipment = [r for r in rows if r.from_location_id == 1003]
    assert sum(r.quantity for r in from_equipment) <= 12.0


# ------------------------------------------------------------- invention_logistics
def test_invention_logistics_needs_bpcs_decryptors_and_datacores(monkeypatch):
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                             output_runs=2, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)
    monkeypatch.setattr(storage, "get_invention_recipe", lambda t1_id: {"datacores": [(300, 2), (301, 2)]})
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 9)  # real T1 blueprint, not a relic
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 0.0)
    monkeypatch.setattr(storage, "available_blueprint_copies", lambda type_id, location_id: 0.0)

    rows = engine.invention_logistics([need], cfg)

    by_type = {r.type_id: r for r in rows}
    # GitHub issue #14: every attempt (successful or not) fully consumes one
    # T1 copy, so demand must track recommended_invention_runs (attempts),
    # not bpcs_needed (successful inventions only) - bpcs_needed undercounts
    # real consumption since failed attempts burn a copy too.
    assert by_type[200].needed == 6.0  # recommended_invention_runs T1 copies, not bpcs_needed
    from eve_trader.production.constants import DECRYPTORS
    parity_type_id = DECRYPTORS["Parity"].type_id
    assert by_type[parity_type_id].needed == 6.0  # one decryptor per attempt
    assert by_type[300].needed == 12.0  # 2 per attempt * 6 attempts
    assert by_type[301].needed == 12.0


def test_invention_logistics_t1_blueprint_availability_uses_copy_count_not_generic_stock(monkeypatch):
    # GitHub issue #14: a T1 blueprint's BPO and BPC share the same type_id -
    # the T1 blueprint row must go through available_blueprint_copies (which
    # excludes BPOs), never the generic esi_stock_at_location every other
    # row uses, or an owned BPO would be miscounted as an available invention
    # input.
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                             output_runs=2, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)
    monkeypatch.setattr(storage, "get_invention_recipe", lambda t1_id: {"datacores": []})
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 9)  # real T1 blueprint, not a relic

    def _esi_stock_at_location(type_id, location_id):
        assert type_id != 200, "must not call esi_stock_at_location for a T1 blueprint type_id"
        return 0.0
    monkeypatch.setattr(storage, "esi_stock_at_location", _esi_stock_at_location)
    monkeypatch.setattr(storage, "available_blueprint_copies", lambda type_id, location_id: 4.0)

    rows = engine.invention_logistics([need], cfg)

    by_type = {r.type_id: r for r in rows}
    assert by_type[200].available == 4.0
    assert by_type[200].missing == 2.0  # 6 needed - 4 available copies


def test_invention_logistics_relic_availability_uses_generic_stock_not_copy_count(monkeypatch):
    """Mirrors the real-T1-blueprint test above in the opposite direction -
    confirmed real bug, reported by a user, 2026-08-30: a Tech III relic
    (t1_blueprint_type_id here is the relic's own type_id, e.g. "Intact
    Power Cores") is a plain purchasable/lootable item, never owned as a
    blueprint copy - it must go through esi_stock_at_location (character_
    assets/corp_assets), never available_blueprint_copies (character_
    blueprints/corp_blueprints), which would always return 0 for it since a
    relic type_id never has a row there at all."""
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T3 Widget", t1_blueprint_type_id=302,
                             t1_blueprint_name="Intact Power Cores", decryptor="Parity", probability=0.26,
                             output_runs=20, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)
    monkeypatch.setattr(storage, "get_invention_recipe", lambda t1_id: {"datacores": []})
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: ANCIENT_RELIC_CATEGORY_ID)  # a relic

    def _available_blueprint_copies(type_id, location_id):
        raise AssertionError("must not call available_blueprint_copies for a Tech III relic type_id")
    monkeypatch.setattr(storage, "available_blueprint_copies", _available_blueprint_copies)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 4.0)

    rows = engine.invention_logistics([need], cfg)

    by_type = {r.type_id: r for r in rows}
    assert by_type[302].available == 4.0
    assert by_type[302].missing == 2.0  # 6 needed - 4 available units


def test_invention_logistics_none_decryptor_does_not_demand_type_zero(monkeypatch):
    # GitHub issue #13: DECRYPTORS["None"].type_id is 0 (no real decryptor
    # used) - SDE type_id 0 happens to be named "#System", so a need with no
    # decryptor selected must never add a demand row for type_id 0.
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="None", probability=0.5,
                             output_runs=2, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)
    monkeypatch.setattr(storage, "get_invention_recipe", lambda t1_id: {"datacores": []})
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 9)  # real T1 blueprint, not a relic
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 0.0)
    monkeypatch.setattr(storage, "available_blueprint_copies", lambda type_id, location_id: 0.0)

    rows = engine.invention_logistics([need], cfg)

    assert 0 not in {r.type_id for r in rows}


def test_invention_logistics_empty_without_location_configured(monkeypatch):
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=None)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                             output_runs=2, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)

    assert engine.invention_logistics([need], cfg) == []


def test_invention_logistics_skips_rows_with_no_runs_needed(monkeypatch):
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                             output_runs=2, runs_needed=0, bpcs_needed=0, recommended_invention_runs=0)

    assert engine.invention_logistics([need], cfg) == []


# ------------------------------------------------------------- t1_bpc_invention_needs
def test_t1_bpc_invention_needs_reports_missing_copies_and_bpo_presence(monkeypatch):
    # GitHub issue #114: the T1-blueprint-only slice of invention_logistics'
    # combined demand, plus a BPO-on-site check.
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                             output_runs=2, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 9)  # real T1 blueprint, not a relic
    monkeypatch.setattr(storage, "available_blueprint_copies", lambda type_id, location_id: 4.0)
    monkeypatch.setattr(storage, "has_bpo_at_location", lambda type_id, location_id: True)

    rows = engine.t1_bpc_invention_needs([need], cfg)

    assert len(rows) == 1
    row = rows[0]
    assert row.type_id == 200
    assert row.needed == 6
    assert row.available == 4
    assert row.missing == 2  # 6 needed - 4 available copies
    assert row.bpo_present is True


def test_t1_bpc_invention_needs_relic_uses_generic_stock_and_never_shows_a_bpo(monkeypatch):
    """Same relic-routing fix as invention_logistics' own mirrored test -
    confirmed real bug, reported by a user, 2026-08-30."""
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T3 Widget", t1_blueprint_type_id=302,
                             t1_blueprint_name="Intact Power Cores", decryptor="Parity", probability=0.26,
                             output_runs=20, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: ANCIENT_RELIC_CATEGORY_ID)  # a relic

    def _available_blueprint_copies(type_id, location_id):
        raise AssertionError("must not call available_blueprint_copies for a Tech III relic type_id")
    monkeypatch.setattr(storage, "available_blueprint_copies", _available_blueprint_copies)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 4.0)
    # A relic type_id genuinely never has a quantity==-1 row in the
    # blueprint tables (it was never a blueprint) - has_bpo_at_location
    # naturally returns False for it with no special-casing needed, so this
    # is left un-mocked/real here to prove that (storage.connect() isn't
    # reached in this monkeypatched-everything-else test, so a real call
    # would raise "no current tenant set" if this ever stopped being true).
    monkeypatch.setattr(storage, "has_bpo_at_location", lambda type_id, location_id: False)

    rows = engine.t1_bpc_invention_needs([need], cfg)

    assert len(rows) == 1
    row = rows[0]
    assert row.type_id == 302
    assert row.available == 4
    assert row.missing == 2  # 6 needed - 4 available units
    assert row.bpo_present is False


def test_t1_bpc_invention_needs_aggregates_across_stock_targets_sharing_a_t1_blueprint(monkeypatch):
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need_a = InventionNeedRow(type_id=10, type_name="T2 Widget A", t1_blueprint_type_id=200,
                               t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                               output_runs=2, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)
    need_b = InventionNeedRow(type_id=11, type_name="T2 Widget B", t1_blueprint_type_id=200,
                               t1_blueprint_name="Widget Blueprint", decryptor="Attainment", probability=0.4,
                               output_runs=1, runs_needed=4, bpcs_needed=4, recommended_invention_runs=10)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 9)  # real T1 blueprint, not a relic
    monkeypatch.setattr(storage, "available_blueprint_copies", lambda type_id, location_id: 0.0)
    monkeypatch.setattr(storage, "has_bpo_at_location", lambda type_id, location_id: False)

    rows = engine.t1_bpc_invention_needs([need_a, need_b], cfg)

    assert len(rows) == 1
    assert rows[0].needed == 16  # 6 + 10 attempts, both needing the same T1 blueprint


def test_t1_bpc_invention_needs_empty_without_location_configured(monkeypatch):
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=None)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                             output_runs=2, runs_needed=10, bpcs_needed=5, recommended_invention_runs=6)

    assert engine.t1_bpc_invention_needs([need], cfg) == []


def test_t1_bpc_invention_needs_skips_rows_with_no_runs_needed(monkeypatch):
    from eve_trader.production.models import InventionNeedRow
    cfg = ProductionConfig(invention_location_id=5000)
    need = InventionNeedRow(type_id=10, type_name="T2 Widget", t1_blueprint_type_id=200,
                             t1_blueprint_name="Widget Blueprint", decryptor="Parity", probability=0.5,
                             output_runs=2, runs_needed=0, bpcs_needed=0, recommended_invention_runs=0)

    assert engine.t1_bpc_invention_needs([need], cfg) == []
