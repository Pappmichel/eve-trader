"""ProductionConfig.excluded_system_ids: stock, blueprints and running jobs in an
excluded solar system do not exist for Production."""
import pytest

from eve_trader import storage
from eve_trader.config import ConfigError
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig, validate_production_overrides

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

REGION = 10099881
EXCLUDED_SYSTEM, OTHER_SYSTEM = 30099881, 30099882
STATION_IN_EXCLUDED = 60099881
NAMED_STRUCTURE = 1049999999001     # registry knows its system
UNNAMED_SYSTEM_STRUCTURE = 1049999999002  # system unknown, name carries the prefix
OFFICE_IN_EXCLUDED = 1049999999003  # registry Office parented to NAMED_STRUCTURE
OTHER_STRUCTURE = 1049999999004
TYPE_ID = 34


@pytest.fixture
def sde(_apply_esi_access_schema):
    pg_helpers.wipe_tables("character_assets", "corp_assets", "character_blueprints", "corp_blueprints",
                           "character_industry_jobs", "corp_industry_jobs", "global_structure_names")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO sde_regions VALUES (%s,%s) ON CONFLICT DO NOTHING", (REGION, "Region 881"))
        for system, name in ((EXCLUDED_SYSTEM, "XS-881"), (OTHER_SYSTEM, "OK-882")):
            conn.execute("INSERT INTO sde_solar_systems VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (system, name, -0.2, REGION))
        conn.execute("INSERT INTO sde_stations VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                     (STATION_IN_EXCLUDED, EXCLUDED_SYSTEM, "XS-881 - Station"))
    engine.invalidate_excluded_locations_cache()
    yield
    engine.invalidate_excluded_locations_cache()


def _registry(tenant_locations):
    storage.set_cached_structure_name(NAMED_STRUCTURE, "Some Fort", EXCLUDED_SYSTEM)
    storage.set_cached_structure_name(UNNAMED_SYSTEM_STRUCTURE, "XS-881 - The Cauldron")
    storage.set_cached_structure_name(OTHER_STRUCTURE, "OK-882 - Home", OTHER_SYSTEM)
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO location_registry (location_id, kind, parent_location_id, status) "
            "VALUES (?, 'office', ?, 'resolved')", (OFFICE_IN_EXCLUDED, NAMED_STRUCTURE))


@pg_helpers.postgres_required()
def test_location_ids_in_systems_covers_stations_structures_name_prefix_and_offices(tenant, sde):
    _registry(tenant)

    ids = storage.location_ids_in_systems([EXCLUDED_SYSTEM])

    assert set(ids) == {EXCLUDED_SYSTEM, STATION_IN_EXCLUDED, NAMED_STRUCTURE,
                        UNNAMED_SYSTEM_STRUCTURE, OFFICE_IN_EXCLUDED}
    assert storage.location_ids_in_systems([]) == []


@pg_helpers.postgres_required()
def test_stock_and_blueprints_in_an_excluded_system_are_ignored(tenant, sde):
    _registry(tenant)
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, UNNAMED_SYSTEM_STRUCTURE, "Hangar", 1000, 0, "pilot"),
        (2, TYPE_ID, STATION_IN_EXCLUDED, "Hangar", 200, 0, "pilot"),
        (3, TYPE_ID, OTHER_STRUCTURE, "Hangar", 30, 0, "pilot"),
    ])
    storage.replace_blueprints("character_blueprints", [
        (10, 999, NAMED_STRUCTURE, "Hangar", -1, 10, 20, -1),
        (11, 999, NAMED_STRUCTURE, "Hangar", -2, 5, 10, 7),
        (12, 999, OTHER_STRUCTURE, "Hangar", -2, 2, 4, 3),
    ])
    excluded = storage.location_ids_in_systems([EXCLUDED_SYSTEM])

    assert storage.esi_stock_at_location(TYPE_ID, None) == 1230
    assert storage.esi_stock_at_location(TYPE_ID, None, exclude_location_ids=excluded) == 30
    assert storage.esi_stock_by_location_bulk([TYPE_ID], exclude_location_ids=excluded) == {
        TYPE_ID: {OTHER_STRUCTURE: 30}}
    assert storage.get_owned_bpo_best_me_te(999) == (10, 20)
    assert storage.get_owned_bpo_best_me_te(999, exclude_location_ids=excluded) is None
    assert storage.available_blueprint_copies(999, None, exclude_location_ids=excluded) == 3
    assert storage.list_blueprint_copies(999, exclude_location_ids=excluded) == [(2, 3, 1)]


@pg_helpers.postgres_required()
def test_engine_reads_the_excluded_systems_from_config(tenant, sde, monkeypatch):
    _registry(tenant)
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, UNNAMED_SYSTEM_STRUCTURE, "Hangar", 1000, 0, "pilot"),
        (3, TYPE_ID, OTHER_STRUCTURE, "Hangar", 30, 0, "pilot"),
    ])
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda kind: (None, None))
    cfg = ProductionConfig()

    assert engine._stock_on_hand(TYPE_ID, {}, cfg) == 1030
    cfg.excluded_system_ids = (EXCLUDED_SYSTEM,)
    # _stock_at_location reads the live (proxied) config; point it at cfg.
    monkeypatch.setattr(engine, "production_excluded_location_ids",
                        lambda c=cfg, _orig=engine.production_excluded_location_ids: _orig(c))
    assert engine._stock_on_hand(TYPE_ID, {}, cfg) == 30


def test_excluded_system_ids_validation():
    validate_production_overrides({"excluded_system_ids": [30004807]})
    with pytest.raises(ConfigError, match="excluded_system_ids"):
        validate_production_overrides({"excluded_system_ids": [0]})
    with pytest.raises(ConfigError, match="excluded_system_ids"):
        validate_production_overrides({"excluded_system_ids": ["UALX-3"]})


def test_no_excluded_systems_means_no_filter():
    assert engine.production_excluded_location_ids(ProductionConfig()) is None


# ---------------------------------------------------------------- manual data
BP_TYPE = 998
JOB_PRODUCT = 35


def _manual_rows():
    """Manual stock, an original, copies and a job: one at a structure in the
    excluded system, one without a location (0), one elsewhere."""
    storage.upsert_manual_stock(TYPE_ID, 1_000_000, NAMED_STRUCTURE)
    storage.upsert_manual_stock(TYPE_ID, 40, 0)
    storage.upsert_manual_stock(TYPE_ID, 5, OTHER_STRUCTURE)
    storage.insert_manual_owned_blueprint(BP_TYPE, True, 10, 20, None, 1, NAMED_STRUCTURE)
    storage.insert_manual_owned_blueprint(BP_TYPE, True, 4, 6, None, 1, 0)
    storage.insert_manual_owned_blueprint(BP_TYPE, False, 8, 0, 10, 2, NAMED_STRUCTURE)
    storage.insert_manual_owned_blueprint(BP_TYPE, False, 2, 0, 3, 1, 0)
    storage.insert_manual_industry_job(JOB_PRODUCT, 1, 500, 5, NAMED_STRUCTURE)
    storage.insert_manual_industry_job(JOB_PRODUCT, 1, 7, 1, 0)


def _wipe_manual():
    pg_helpers.wipe_tables("manual_stock", "manual_owned_blueprints", "manual_industry_jobs")


@pg_helpers.postgres_required()
def test_manual_readers_drop_excluded_locations_but_keep_location_zero(tenant, sde):
    _wipe_manual()
    _registry(tenant)
    _manual_rows()
    excluded = storage.location_ids_in_systems([EXCLUDED_SYSTEM])

    # Default stays unfiltered (manual-stock page, ledger, valuation).
    assert storage.load_manual_stock()[TYPE_ID] == 1_000_045
    assert storage.manual_bpo_best_me_te(BP_TYPE) == (10, 20)
    assert storage.manual_bpc_runs(BP_TYPE) == 23
    assert storage.manual_incoming_qty(JOB_PRODUCT) == 507

    assert storage.load_manual_stock(exclude_location_ids=excluded)[TYPE_ID] == 45
    assert storage.manual_bpo_best_me_te(BP_TYPE, exclude_location_ids=excluded) == (4, 6)
    assert storage.manual_bpo_best_me_te_bulk([BP_TYPE], exclude_location_ids=excluded) == {BP_TYPE: (4, 6)}
    assert storage.manual_bpc_runs(BP_TYPE, exclude_location_ids=excluded) == 3
    assert storage.manual_incoming_qty(JOB_PRODUCT, exclude_location_ids=excluded) == 7
    assert storage.manual_incoming_qty_bulk([JOB_PRODUCT], exclude_location_ids=excluded) == {JOB_PRODUCT: 7.0}
    assert storage.list_blueprint_copies(BP_TYPE, exclude_location_ids=excluded) == [(2, 3, 1)]


@pg_helpers.postgres_required()
def test_engine_ignores_manual_data_in_excluded_systems_with_and_without_planner_reads(
        tenant, sde, monkeypatch):
    from eve_trader.production import max_buildable
    from eve_trader.production.config import PRODUCTION_CONFIG

    _wipe_manual()
    _registry(tenant)
    _manual_rows()
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda kind: (None, None))
    monkeypatch.setattr(PRODUCTION_CONFIG, "excluded_system_ids", (EXCLUDED_SYSTEM,))
    engine.invalidate_excluded_locations_cache()

    manual = storage.load_manual_stock(**engine.production_excluded_kwargs())
    assert manual[TYPE_ID] == 45
    assert engine._stock_on_hand(TYPE_ID, manual, PRODUCTION_CONFIG) == 45
    assert max_buildable._Context(PRODUCTION_CONFIG, "full").manual_stock[TYPE_ID] == 45

    plain = (engine._owned_bpo_best_me_te(BP_TYPE), engine._manual_incoming_qty(JOB_PRODUCT),
             engine._available_blueprint_copies(BP_TYPE, None),
             engine._current_stock(JOB_PRODUCT, {}, PRODUCTION_CONFIG, None))
    assert plain == ((4, 6), 7.0, 3, 7.0)
    with engine.planner_reads() as scope:
        scope.prefetch([JOB_PRODUCT], PRODUCTION_CONFIG, blueprint_type_ids=[BP_TYPE])
        memo = (engine._owned_bpo_best_me_te(BP_TYPE), engine._manual_incoming_qty(JOB_PRODUCT),
                engine._available_blueprint_copies(BP_TYPE, None),
                engine._current_stock(JOB_PRODUCT, {}, PRODUCTION_CONFIG, None))
    assert memo == plain

    # Without excluded systems everything counts again.
    monkeypatch.setattr(PRODUCTION_CONFIG, "excluded_system_ids", ())
    assert engine._owned_bpo_best_me_te(BP_TYPE) == (10, 20)
    assert engine._manual_incoming_qty(JOB_PRODUCT) == 507


@pg_helpers.postgres_required()
def test_invention_jobs_delivering_into_an_excluded_system_are_ignored(tenant, sde):
    _registry(tenant)
    with storage.connect() as conn:
        for job_id, loc in ((901, NAMED_STRUCTURE), (902, OTHER_STRUCTURE), (903, None)):
            conn.execute(
                "INSERT INTO character_industry_jobs (job_id, activity_id, product_type_id, runs, "
                "output_location_id, status, probability) VALUES (?, 8, ?, 10, ?, 'active', 0.5)",
                (job_id, BP_TYPE, loc))
    excluded = storage.location_ids_in_systems([EXCLUDED_SYSTEM])

    assert storage.esi_expected_invention_successes(BP_TYPE, 0.3) == 15.0
    assert storage.esi_expected_invention_successes(
        BP_TYPE, 0.3, exclude_location_ids=excluded) == 10.0


def test_reaction_job_in_an_excluded_system_keeps_its_slot_but_adds_no_output(monkeypatch):
    from eve_trader.production.models import CharacterSlotRow
    from eve_trader.production.reaction_capacity import window_state

    monkeypatch.setattr(storage, "get_product_quantity", lambda *a, **k: 10.0)
    monkeypatch.setattr(engine, "production_excluded_location_ids",
                        lambda *a, **k: [STATION_IN_EXCLUDED])
    cfg = ProductionConfig(
        reaction_slot_override=5,
        reaction_window_next="2030-01-15T12:00:00+00:00",
        reaction_window_interval_days=14.0,
    )
    rows = [CharacterSlotRow(character_name="A", job_type="Reactions", total_slots=5,
                             used_slots=0, free_slots=5, excluded_from_planning=False)]
    jobs = [
        (1, 11, 100, 50, "X", 2, STATION_IN_EXCLUDED, "active", "2030-01-20T00:00:00+00:00", None, "A"),
        (2, 11, 100, 51, "Y", 1, OTHER_STRUCTURE, "active", "2030-01-16T00:00:00+00:00", None, "A"),
        (3, 11, 100, 52, "Z", 3, STATION_IN_EXCLUDED, "active", "2030-01-10T00:00:00+00:00", None, "A"),
    ]

    state = window_state(cfg, character_rows=rows, industry_jobs=jobs)

    assert state.busy_slots == 2
    assert 50 not in state.incoming1_from_jobs and 52 not in state.on_hand_from_jobs
    assert state.incoming1_from_jobs.get(51) == 10.0
