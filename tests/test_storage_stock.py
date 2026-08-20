import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 34  # Tritanium
LOCATION_ID = 1000000000001


@pytest.fixture(autouse=True)
def _wipe():
    # character_assets/corp_assets are column-only-bucket tables (PK =
    # item_id alone, not tenant_id-widened - see pg_helpers.wipe_tables) and
    # every test below reuses small hardcoded item_ids (1, 2, 900, ...)
    # across different tenants, so a fresh tenant_id alone doesn't prevent a
    # PK collision with a previous test's row. sde_stations is a genuinely
    # shared table (no tenant_id at all) and two tests below insert the same
    # fake station_id into it.
    pg_helpers.wipe_tables("character_assets", "corp_assets", "sde_stations")
    yield


def test_esi_stock_at_location_excludes_non_stock_flags(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "pilot"),
        (2, TYPE_ID, LOCATION_ID, "AssetSafety", 500, 0, "pilot"),
        (3, TYPE_ID, LOCATION_ID, "Deliveries", 50, 0, "pilot"),
        (4, TYPE_ID, LOCATION_ID, "CorpDeliveries", 25, 0, "pilot"),
        (5, TYPE_ID, LOCATION_ID, "CorpMarket", 10, 0, "pilot"),
    ])

    # Only the genuine Hangar quantity counts as usable stock - AssetSafety/
    # Deliveries/CorpDeliveries/CorpMarket share the same location_id but
    # aren't actually available to build with.
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID) == 100


def test_esi_stock_at_location_still_unwraps_corp_office(tenant):
    office_item_id = 900
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (2, TYPE_ID, office_item_id, "CorpSAG1", 300, 0, "My Corp (corp)"),
        (3, TYPE_ID, office_item_id, "AssetSafety", 999, 0, "My Corp (corp)"),  # nested-but-unusable, still excluded
    ])

    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID) == 300


def test_esi_stock_at_location_sees_stock_inside_a_container_in_corp_hangar(tenant):
    # GitHub issue #4: esi_stock_at_location used to resolve nesting only one
    # level deep (a hardcoded Office special-case) - a Station Container
    # sitting inside the corp Office (two hops: container -> Office ->
    # structure) read as completely invisible stock. Confirmed real report:
    # ~300M tritanium sitting in a container at C-J never showed up as
    # available to Distribution, which kept recommending pulling it from
    # other production facilities instead of the (actually well-stocked)
    # warehouse.
    office_item_id = 900
    container_item_id = 901
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (container_item_id, 649, office_item_id, "CorpSAG1", 1, 0, "My Corp (corp)"),  # a Station Container
        (2, TYPE_ID, container_item_id, "Unlocked", 300000000, 0, "My Corp (corp)"),  # tritanium inside it
    ])

    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID) == 300000000


def test_search_item_stock_locations_groups_by_location_and_owner(tenant):
    other_location = 1000000000002
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "Alice"),
        (2, TYPE_ID, LOCATION_ID, "Hangar", 50, 0, "Alice"),  # same (location, owner) - must sum, not duplicate
        (3, TYPE_ID, other_location, "Hangar", 20, 0, "Bob"),
    ])

    rows = storage.search_item_stock_locations(TYPE_ID)

    by_key = {(r[0], r[2]): r[3] for r in rows}
    assert by_key[(LOCATION_ID, "Alice")] == 150
    assert by_key[(other_location, "Bob")] == 20


def test_search_item_stock_locations_excludes_non_stock_flags(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "Alice"),
        (2, TYPE_ID, LOCATION_ID, "AssetSafety", 999, 0, "Alice"),
    ])

    rows = storage.search_item_stock_locations(TYPE_ID)

    assert len(rows) == 1
    assert rows[0][3] == 100  # AssetSafety quantity never counted


def test_search_item_stock_locations_resolves_multi_hop_nested_containers(tenant):
    # A real EVE shape: item sits inside a container, which sits inside a
    # ship, which sits in a station hangar - three hops up to the station.
    ship_item_id = 500
    container_item_id = 501
    storage.replace_assets("character_assets", [
        (ship_item_id, 600, LOCATION_ID, "Hangar", 1, 0, "Alice"),  # the ship itself, in station hangar
        (container_item_id, 601, ship_item_id, "Cargo", 1, 0, "Alice"),  # a container, inside the ship's cargo
        (99, TYPE_ID, container_item_id, "Unlocked", 42, 0, "Alice"),  # the searched item, inside the container
    ])

    rows = storage.search_item_stock_locations(TYPE_ID)

    assert rows == [(LOCATION_ID, None, "Alice", 42)]  # resolved all the way up to the station, not the container


def test_search_item_stock_locations_resolves_corp_office_nesting(tenant):
    office_item_id = 900
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (2, TYPE_ID, office_item_id, "CorpSAG1", 300, 0, "My Corp (corp)"),
    ])

    rows = storage.search_item_stock_locations(TYPE_ID)

    assert rows == [(LOCATION_ID, None, "My Corp (corp)", 300)]


def test_search_item_stock_locations_prefers_structure_name_over_station_name(tenant):
    # sde_stations.station_id is a real NPC station id (small - INTEGER, not
    # BIGINT like a player structure's location_id, see phase1_schema.sql) -
    # this test needs the *same* id in both structure_names and sde_stations
    # to exercise the prefer-structure-name-over-station-name fallback, so
    # it uses a station-scale id here rather than the module's LOCATION_ID
    # (which represents a player structure elsewhere in this file).
    station_location_id = 60003760
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, station_location_id, "Hangar", 10, 0, "Alice"),
    ])
    storage.set_cached_structure_name(station_location_id, "Player Structure Name")
    with storage.connect() as conn:
        conn.execute("INSERT INTO sde_stations VALUES (?,?,?)", (station_location_id, 30000142, "NPC Station Name"))

    rows = storage.search_item_stock_locations(TYPE_ID)

    assert rows == [(station_location_id, "Player Structure Name", "Alice", 10)]


def test_search_item_stock_locations_falls_back_to_station_name(tenant):
    station_location_id = 60003761
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, station_location_id, "Hangar", 10, 0, "Alice"),
    ])
    with storage.connect() as conn:
        conn.execute("INSERT INTO sde_stations VALUES (?,?,?)", (station_location_id, 30000142, "NPC Station Name"))

    rows = storage.search_item_stock_locations(TYPE_ID)

    assert rows == [(station_location_id, "NPC Station Name", "Alice", 10)]


def test_get_cached_structure_names_batches_and_preserves_was_cached_semantics(tenant):
    # Batched form of get_cached_structure_name (GET /logistics/structure-
    # names used to call the single-id version once per location_id in a
    # Python loop - N connections instead of one query). Three cases in one
    # call: a location that resolved to a real name, one that was attempted
    # and failed (cached as None - must NOT be re-resolved on every page
    # load), and one never attempted at all (was_cached=False).
    storage.set_cached_structure_name(1, "Real Structure")
    storage.set_cached_structure_name(2, None)  # attempted, resolution failed

    result = storage.get_cached_structure_names([1, 2, 3])

    assert result[1] == (True, "Real Structure")
    assert result[2] == (True, None)
    assert result[3] == (False, None)


def test_get_cached_structure_names_empty_input_returns_empty_dict(tenant):
    assert storage.get_cached_structure_names([]) == {}


def test_set_cached_structure_name_stores_solar_system_id(tenant):
    storage.set_cached_structure_name(LOCATION_ID, "C-J Keepstar", solar_system_id=30000142)

    assert storage.get_structure_system_id(LOCATION_ID) == 30000142


def test_set_cached_structure_name_never_overwrites_system_id_with_none(tenant):
    # GitHub issue #12: a later best-effort resolve (e.g. force=True retried
    # through docking-history fallback, which happened to not return
    # solar_system_id this time) must not blow away a value an earlier
    # successful resolve already captured.
    storage.set_cached_structure_name(LOCATION_ID, "C-J Keepstar", solar_system_id=30000142)
    storage.set_cached_structure_name(LOCATION_ID, "C-J Keepstar (renamed)", solar_system_id=None)

    assert storage.get_structure_system_id(LOCATION_ID) == 30000142


def test_get_structure_system_id_none_when_never_resolved(tenant):
    assert storage.get_structure_system_id(LOCATION_ID) is None


def test_load_category_system_ids_joins_category_locations_and_structure_names(tenant):
    storage.upsert_category_location("Reactions", LOCATION_ID)
    storage.set_cached_structure_name(LOCATION_ID, "C-J Keepstar", solar_system_id=30000142)
    other_location = 1000000000002
    storage.upsert_category_location("Capital Components", other_location)
    # Never resolved - must be absent from the result, not present with None.
    storage.upsert_category_location("Advanced Components", 1000000000003)

    result = storage.load_category_system_ids()

    assert result == {"Reactions": 30000142}
