import pytest
from pathlib import Path

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 34  # Tritanium
LOCATION_ID = 1000000000001
_SORTING_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "sorting_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_sorting_schema(_apply_phase1_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_SORTING_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _wipe():
    # character_assets/corp_assets are column-only-bucket tables (PK =
    # item_id alone, not tenant_id-widened - see pg_helpers.wipe_tables) and
    # every test below reuses small hardcoded item_ids (1, 2, 900, ...)
    # across different tenants, so a fresh tenant_id alone doesn't prevent a
    # PK collision with a previous test's row. sde_stations is a genuinely
    # shared table (no tenant_id at all) and two tests below insert the same
    # fake station_id into it. character_slots is the same column-only-bucket
    # shape (PK = character_name alone) and the tests added for GitHub issue
    # #39 below reuse small hardcoded names ("Alice"/"Bob") too.
    pg_helpers.wipe_tables(
        "character_assets", "corp_assets", "sde_stations", "character_slots",
        "sorting_intake_sources",
    )
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


def test_esi_stock_at_location_allowed_flags_restricts_to_named_divisions(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "pilot"),
        (2, TYPE_ID, LOCATION_ID, "CorpSAG1", 40, 0, "pilot"),
        (3, TYPE_ID, LOCATION_ID, "CorpSAG2", 25, 0, "pilot"),
    ])

    # Only the named division(s) count, even though CorpSAG2 is a perfectly
    # normal, non-NON_STOCK_LOCATION_FLAGS division too - allowed_flags is a
    # strictly narrower filter on top of the existing exclude-list, not a
    # replacement for it.
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, allowed_flags=("CorpSAG1",)) == 40
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, allowed_flags=("Hangar", "CorpSAG1")) == 140


def test_esi_stock_at_location_allowed_flags_still_excludes_non_stock_flags(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "AssetSafety", 500, 0, "pilot"),
        (2, TYPE_ID, LOCATION_ID, "CorpSAG1", 40, 0, "pilot"),
    ])

    # Even if a caller mistakenly named a NON_STOCK_LOCATION_FLAGS value in
    # allowed_flags, the existing exclusion still wins - allowed_flags only
    # narrows, it can never widen past what's already excluded.
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, allowed_flags=("AssetSafety", "CorpSAG1")) == 40


def test_esi_stock_at_location_none_allowed_flags_matches_default_behaviour(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "pilot"),
        (2, TYPE_ID, LOCATION_ID, "CorpSAG1", 40, 0, "pilot"),
    ])

    # Both an empty tuple and the None default must behave exactly like
    # today (no allowed_flags at all) - backward compatibility is required.
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID) == 140
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, allowed_flags=()) == 140
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, allowed_flags=None) == 140


def test_esi_stock_at_location_allowed_flags_with_location_id_none(tenant):
    other_location = 1000000000002
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "CorpSAG1", 40, 0, "pilot"),
        (2, TYPE_ID, other_location, "CorpSAG1", 15, 0, "pilot"),
        (3, TYPE_ID, LOCATION_ID, "Hangar", 999, 0, "pilot"),
    ])

    # allowed_flags composes with the location_id=None ("everywhere") case
    # too, not just the single-location one.
    assert storage.esi_stock_at_location(TYPE_ID, None, allowed_flags=("CorpSAG1",)) == 55


def test_esi_stock_at_location_exclude_intake_skips_configured_source_at_that_location(tenant):
    other_location = 1000000000002
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 500, 0, "pappmichl5"),
        (2, TYPE_ID, other_location, "Hangar", 80, 0, "pappmichl5"),
        (3, TYPE_ID, LOCATION_ID, "Hangar", 40, 0, "someone else"),
        (4, TYPE_ID, LOCATION_ID, "CorpSAG1", 10, 0, "pappmichl5"),
    ])
    storage.add_sorting_intake_source("character", "Hangar", owner_name="pappmichl5")

    # Without the opt-in, intake still counts (call sites that haven't been
    # updated keep today's behaviour).
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID) == 550
    # At C-J: intake Hangar for pappmichl5 is excluded; other owner in the
    # same flag, and pappmichl5's non-intake CorpSAG1, still count.
    assert storage.esi_stock_at_location(
        TYPE_ID, LOCATION_ID, exclude_intake_at_location_id=LOCATION_ID) == 50
    # Same owner+flag at a different structure is real stock, not intake.
    assert storage.esi_stock_at_location(
        TYPE_ID, other_location, exclude_intake_at_location_id=LOCATION_ID) == 80
    # Corp-wide (_current_stock's location_id=None): C-J intake Hangar is
    # dropped, Jita Hangar + remaining C-J stacks remain.
    assert storage.esi_stock_at_location(
        TYPE_ID, None, exclude_intake_at_location_id=LOCATION_ID) == 130


def test_esi_stock_at_location_allowed_flags_sees_stock_nested_inside_a_division_container(tenant):
    # Regression: allowed_flags originally filtered on the raw location_flag
    # column, which is only meaningful for a row sitting directly in the
    # division - anything nested one level deeper inside a container (a very
    # common real shape: a fresh delivery arrives as one wrapped container)
    # carries its own container-internal flag ("Unlocked"), not the
    # division's, and silently vanished from a filtered count. Same bug
    # class as GitHub issue #4/#20 (see
    # test_esi_stock_at_location_sees_stock_inside_a_container_in_corp_hangar
    # above), reintroduced here and fixed via resolved_hangar_flag/
    # storage._resolve_hangar_flags.
    office_item_id = 900
    container_item_id = 901
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (container_item_id, 649, office_item_id, "CorpSAG1", 1, 0, "My Corp (corp)"),  # a Station Container
        (2, TYPE_ID, container_item_id, "Unlocked", 300000000, 0, "My Corp (corp)"),  # tritanium inside it
        (3, TYPE_ID, office_item_id, "CorpSAG2", 500, 0, "My Corp (corp)"),  # a different division - excluded
    ])

    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, allowed_flags=("CorpSAG1",)) == 300000000
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, allowed_flags=("CorpSAG2",)) == 500


def test_assets_at_flag_sums_across_character_and_corp_assets(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "CorpSAG3", 10, 0, "pilot"),
    ])
    storage.replace_assets("corp_assets", [
        (2, TYPE_ID, LOCATION_ID, "CorpSAG3", 5, 0, "My Corp (corp)"),
        (3, 35, LOCATION_ID, "CorpSAG3", 7, 0, "My Corp (corp)"),  # Pyerite - a different type_id
    ])

    rows = storage.assets_at_flag("CorpSAG3")

    assert dict(rows) == {TYPE_ID: 15, 35: 7}


def test_assets_at_flag_ignores_other_divisions(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "CorpSAG3", 10, 0, "pilot"),
        (2, TYPE_ID, LOCATION_ID, "Hangar", 999, 0, "pilot"),
    ])

    assert dict(storage.assets_at_flag("CorpSAG3")) == {TYPE_ID: 10}


def test_assets_at_flag_empty_when_nothing_matches(tenant):
    assert storage.assets_at_flag("CorpSAG7") == []


def test_assets_at_flag_sees_contents_of_a_container_sitting_in_the_division(tenant):
    # Same regression as
    # test_esi_stock_at_location_allowed_flags_sees_stock_nested_inside_a_division_container
    # for assets_at_flag: a Wareneingang delivery routinely arrives as one
    # container placed in the division, with its actual contents one level
    # deeper - filtering on the raw location_flag column would report the
    # division as empty.
    office_item_id = 900
    container_item_id = 901
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (container_item_id, 649, office_item_id, "CorpSAG3", 1, 0, "My Corp (corp)"),
        (2, TYPE_ID, container_item_id, "Unlocked", 42, 0, "My Corp (corp)"),
    ])

    # The container itself (type_id 649) also genuinely sits in CorpSAG3 and
    # is correctly counted too - the regression this guards against is the
    # tritanium (TYPE_ID) *inside* it going missing, not the container.
    assert dict(storage.assets_at_flag("CorpSAG3")) == {TYPE_ID: 42, 649: 1}


def test_assets_at_flag_owner_name_filters_to_that_character(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 10, 0, "Alice"),
        (2, TYPE_ID, LOCATION_ID, "Hangar", 20, 0, "Bob"),
        (3, 35, LOCATION_ID, "Hangar", 7, 0, "Alice"),
    ])

    assert dict(storage.assets_at_flag("Hangar", tables=("character_assets",), owner_name="Alice")) == {
        TYPE_ID: 10, 35: 7,
    }
    assert dict(storage.assets_at_flag("Hangar", tables=("character_assets",), owner_name="Bob")) == {TYPE_ID: 20}
    # None keeps today's unfiltered behaviour (both characters).
    assert dict(storage.assets_at_flag("Hangar", tables=("character_assets",))) == {TYPE_ID: 30, 35: 7}


def test_assets_at_flag_owner_name_does_not_match_other_table_owners(tenant):
    storage.replace_assets("corp_assets", [
        (1, TYPE_ID, LOCATION_ID, "CorpSAG1", 40, 0, "My Corp (corp)"),
    ])
    storage.replace_assets("character_assets", [
        (2, TYPE_ID, LOCATION_ID, "CorpSAG1", 3, 0, "Alice"),
    ])

    # A corp source must not pick up Alice's personal stack just because
    # the hangar flag happens to match.
    assert dict(storage.assets_at_flag("CorpSAG1", tables=("corp_assets",))) == {TYPE_ID: 40}
    assert dict(storage.assets_at_flag("CorpSAG1", tables=("character_assets",), owner_name="Alice")) == {TYPE_ID: 3}


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


def test_manual_blueprint_copy_cost_round_trips(tenant):
    # GitHub issue #40.
    storage.upsert_manual_blueprint_copy_cost(TYPE_ID, "Tritanium", purchase_cost=1_000_000.0, runs=10)

    rows = storage.load_manual_blueprint_copy_costs()

    assert rows == [(TYPE_ID, "Tritanium", 1_000_000.0, 10)]


def test_manual_blueprint_copy_cost_upsert_updates_existing_row(tenant):
    storage.upsert_manual_blueprint_copy_cost(TYPE_ID, "Tritanium", purchase_cost=1_000_000.0, runs=10)
    storage.upsert_manual_blueprint_copy_cost(TYPE_ID, "Tritanium", purchase_cost=2_000_000.0, runs=5)

    rows = storage.load_manual_blueprint_copy_costs()

    assert rows == [(TYPE_ID, "Tritanium", 2_000_000.0, 5)]


def test_manual_blueprint_copy_cost_update_existing_row(tenant):
    storage.upsert_manual_blueprint_copy_cost(TYPE_ID, "Tritanium", purchase_cost=1_000_000.0, runs=10)

    updated = storage.update_manual_blueprint_copy_cost(TYPE_ID, purchase_cost=2_000_000.0, runs=5)

    assert updated is True
    assert storage.load_manual_blueprint_copy_costs() == [(TYPE_ID, "Tritanium", 2_000_000.0, 5)]


def test_manual_blueprint_copy_cost_update_missing_row_returns_false(tenant):
    updated = storage.update_manual_blueprint_copy_cost(TYPE_ID, purchase_cost=2_000_000.0, runs=5)

    assert updated is False
    assert storage.load_manual_blueprint_copy_costs() == []


def test_manual_blueprint_copy_cost_delete(tenant):
    storage.upsert_manual_blueprint_copy_cost(TYPE_ID, "Tritanium", purchase_cost=1_000_000.0, runs=10)

    storage.delete_manual_blueprint_copy_cost(TYPE_ID)

    assert storage.load_manual_blueprint_copy_costs() == []


def test_get_manual_blueprint_copy_cost_per_run_amortizes(tenant):
    storage.upsert_manual_blueprint_copy_cost(TYPE_ID, "Tritanium", purchase_cost=1_000_000.0, runs=10)

    assert storage.get_manual_blueprint_copy_cost_per_run(TYPE_ID) == pytest.approx(100_000.0)


def test_get_manual_blueprint_copy_cost_per_run_none_when_not_registered(tenant):
    assert storage.get_manual_blueprint_copy_cost_per_run(TYPE_ID) is None


def test_replace_character_slots_preserves_excluded_flag_across_resync(tenant):
    # GitHub issue #39: replace_character_slots is an UPSERT, not
    # delete+reinsert - a character's excluded_from_planning flag must
    # survive the next ESI re-sync instead of resetting to the column
    # default every time.
    storage.replace_character_slots([("Alice", 5, 3, 2), ("Bob", 5, 3, 2)])
    storage.set_character_slot_excluded("Alice", True)

    storage.replace_character_slots([("Alice", 6, 3, 2), ("Bob", 5, 3, 2)])  # simulates a re-sync

    rows = {r[0]: r for r in storage.load_character_slots()}
    assert rows["Alice"][1] == 6  # manufacturing_slots updated
    assert rows["Alice"][4] is True  # excluded_from_planning preserved
    assert rows["Bob"][4] is False


def test_replace_character_slots_removes_deregistered_characters(tenant):
    storage.replace_character_slots([("Alice", 5, 3, 2), ("Bob", 5, 3, 2)])

    storage.replace_character_slots([("Alice", 5, 3, 2)])  # Bob no longer registered

    names = {r[0] for r in storage.load_character_slots()}
    assert names == {"Alice"}


def test_set_character_slot_excluded_toggles_flag(tenant):
    storage.replace_character_slots([("Alice", 5, 3, 2)])

    storage.set_character_slot_excluded("Alice", True)
    assert storage.load_character_slots()[0][4] is True

    storage.set_character_slot_excluded("Alice", False)
    assert storage.load_character_slots()[0][4] is False


def test_set_character_slot_excluded_is_a_noop_for_unknown_character(tenant):
    storage.set_character_slot_excluded("Nobody", True)  # doesn't raise
    assert storage.load_character_slots() == []
