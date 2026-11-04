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
