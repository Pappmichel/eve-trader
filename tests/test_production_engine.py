import pytest

from eve_trader import storage
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import SCC_SURCHARGE_RATE, ACTIVITY_MODS, rig_security_multiplier
from eve_trader.production.engine import _activity_mods, _material_qty, _tech_ii_mods, _total_missing, classify_activity
from eve_trader.production.models import CharacterSlotRow

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

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
    # Tech III hulls/subsystems are Reverse-Engineering products, not T1-BP
    # Invention products, so find_invention_recipe_by_product_type_id always
    # returns None for them - a manually-selected decryptor must still drive
    # ME/TE instead of silently falling back to the flat Tech II baseline.
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
    cfg = ProductionConfig()

    material_mult, time_mult, decryptor_name = _tech_ii_mods(
        type_id=1, blueprint_id=2, activity_id=1, cfg=cfg, home={}, jita={},
        selected_decryptors={1: "Accelerant"}, memo={},
    )
    # Accelerant: absolute ME4/TE14 -> multipliers 0.96/0.86 (no structure/rig bonus, cfg default = Citadel/no rig).
    assert round(material_mult, 4) == 0.96
    assert round(time_mult, 4) == 0.86
    assert decryptor_name == "Accelerant"


@pg_helpers.postgres_required()
def test_tech_iii_falls_back_to_flat_baseline_without_override(monkeypatch, tenant):
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
    cfg = ProductionConfig()

    material_mult, time_mult, decryptor_name = _tech_ii_mods(
        type_id=1, blueprint_id=2, activity_id=1, cfg=cfg, home={}, jita={},
        selected_decryptors={}, memo={},
    )
    assert round(material_mult, 4) == 0.98
    assert round(time_mult, 4) == 0.96
    assert decryptor_name is None


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
    # (find_invention_recipe_by_product_type_id finds a real recipe), and
    # must be routed through the decryptor-based Tech II path, not priced
    # off the flat "perfect Tech I BPO" baseline - metaLevel plays no part in
    # the decision at all, only the invention recipe does.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: 999)

    activity, bp = classify_activity(100)
    assert activity == "Tech II"
    assert bp == (200, 1, 1.0)


def test_classify_activity_still_tech_i_for_genuinely_uninvented_low_meta_item(monkeypatch):
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
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
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
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
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (100, 1, "Some Officer Mod", 5.0, 1, 600, 14, 5))

    activity, _ = classify_activity(100)
    assert activity == "Officer"


def test_classify_activity_treats_uninvented_storyline_item_as_storyline(monkeypatch):
    # metaGroupID 3 = Storyline - confirmed live (e.g. "Purloined Sansha Data
    # Analyzer", built from a real "Mangled Sansha Data Analyzer" conversion
    # blueprint) - same ME0/TE0, non-researchable treatment as Faction.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (201, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
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
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (102, 1, "Some Deadspace Mod", 5.0, 1, 600, 14, 6))

    activity, _ = classify_activity(102)
    assert activity == "Deadspace"


def test_classify_activity_falls_back_to_tech_i_for_unmapped_meta_group(monkeypatch):
    # Any other real metaGroupID not explicitly mapped (e.g. 17/19 - SKINs,
    # apparel, boosters) still falls back to plain "Tech I", not a KeyError.
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (203, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
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
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
    monkeypatch.setattr(
        storage, "get_sde_type",
        lambda type_id: (29073, 913, "Capital Nanoelectrical Microprocessor", 10.0, 1, 1884, None, None),
    )

    assert engine.job_category(29073) == "Advanced Components"


def test_job_category_treats_basic_capital_components_as_capital(monkeypatch):
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (200, 1, 1.0))
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
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
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (100, 958, "Plain Tech I Thing", 40.0, 1, 1125, 0, None))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 99)  # not a ship - plain SDE volume path
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id: None)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])

    cfg = ProductionConfig()
    home = {100: _fake_quote(100, sell=500.0)}

    best, build_cost, buy_price = engine.unit_cost_detail(
        100, cfg, home, {}, memo={}, selected_decryptors={}, t2_memo={}, cost_indices={}, adjusted_prices={})

    assert buy_price == pytest.approx(500.0 * (1 + cfg.jita_buy_broker_fee))
    assert build_cost == 0.0  # no materials, no job cost (adjusted_prices empty -> eiv=0)
    assert best == 0.0  # min(buy, 0.0) - build wins


def test_unit_cost_detail_returns_buy_only_when_not_buildable(monkeypatch):
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: None)  # no blueprint at all
    monkeypatch.setattr(storage, "find_invention_recipe_by_product_type_id", lambda blueprint_id: None)
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


# ---------------------------------------------------------------- discover_ship_margins
@pg_helpers.postgres_required()
def test_discover_ship_margins_only_includes_ships(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
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
    monkeypatch.setattr(storage, "load_sde_types_with_market_group", lambda: [
        (1, "Unbuildable Ship", 1.0, 100, None, engine.SHIP_CATEGORY_ID),
    ])
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))  # no blueprint

    results = engine.discover_ship_margins(ProductionConfig())

    assert results == []


@pg_helpers.postgres_required()
def test_discover_ship_margins_populates_prices_and_both_margins(monkeypatch, tenant):
    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)  # home[1]=(buy=100,sell=200), jita={}
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
