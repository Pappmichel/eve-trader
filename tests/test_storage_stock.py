from eve_trader import storage

TYPE_ID = 34  # Tritanium
LOCATION_ID = 1000000000001


def test_esi_stock_at_location_excludes_non_stock_flags(tmp_path):
    db_path = tmp_path / "test.db"
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "pilot"),
        (2, TYPE_ID, LOCATION_ID, "AssetSafety", 500, 0, "pilot"),
        (3, TYPE_ID, LOCATION_ID, "Deliveries", 50, 0, "pilot"),
        (4, TYPE_ID, LOCATION_ID, "CorpDeliveries", 25, 0, "pilot"),
        (5, TYPE_ID, LOCATION_ID, "CorpMarket", 10, 0, "pilot"),
    ], db_path=db_path)

    # Only the genuine Hangar quantity counts as usable stock - AssetSafety/
    # Deliveries/CorpDeliveries/CorpMarket share the same location_id but
    # aren't actually available to build with.
    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, db_path=db_path) == 100


def test_esi_stock_at_location_still_unwraps_corp_office(tmp_path):
    db_path = tmp_path / "test.db"
    office_item_id = 900
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (2, TYPE_ID, office_item_id, "CorpSAG1", 300, 0, "My Corp (corp)"),
        (3, TYPE_ID, office_item_id, "AssetSafety", 999, 0, "My Corp (corp)"),  # nested-but-unusable, still excluded
    ], db_path=db_path)

    assert storage.esi_stock_at_location(TYPE_ID, LOCATION_ID, db_path=db_path) == 300


def test_search_item_stock_locations_groups_by_location_and_owner(tmp_path):
    db_path = tmp_path / "test.db"
    other_location = 1000000000002
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "Alice"),
        (2, TYPE_ID, LOCATION_ID, "Hangar", 50, 0, "Alice"),  # same (location, owner) - must sum, not duplicate
        (3, TYPE_ID, other_location, "Hangar", 20, 0, "Bob"),
    ], db_path=db_path)

    rows = storage.search_item_stock_locations(TYPE_ID, db_path=db_path)

    by_key = {(r[0], r[2]): r[3] for r in rows}
    assert by_key[(LOCATION_ID, "Alice")] == 150
    assert by_key[(other_location, "Bob")] == 20


def test_search_item_stock_locations_excludes_non_stock_flags(tmp_path):
    db_path = tmp_path / "test.db"
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 100, 0, "Alice"),
        (2, TYPE_ID, LOCATION_ID, "AssetSafety", 999, 0, "Alice"),
    ], db_path=db_path)

    rows = storage.search_item_stock_locations(TYPE_ID, db_path=db_path)

    assert len(rows) == 1
    assert rows[0][3] == 100  # AssetSafety quantity never counted


def test_search_item_stock_locations_resolves_multi_hop_nested_containers(tmp_path):
    # A real EVE shape: item sits inside a container, which sits inside a
    # ship, which sits in a station hangar - three hops up to the station.
    db_path = tmp_path / "test.db"
    ship_item_id = 500
    container_item_id = 501
    storage.replace_assets("character_assets", [
        (ship_item_id, 600, LOCATION_ID, "Hangar", 1, 0, "Alice"),  # the ship itself, in station hangar
        (container_item_id, 601, ship_item_id, "Cargo", 1, 0, "Alice"),  # a container, inside the ship's cargo
        (99, TYPE_ID, container_item_id, "Unlocked", 42, 0, "Alice"),  # the searched item, inside the container
    ], db_path=db_path)

    rows = storage.search_item_stock_locations(TYPE_ID, db_path=db_path)

    assert rows == [(LOCATION_ID, None, "Alice", 42)]  # resolved all the way up to the station, not the container


def test_search_item_stock_locations_resolves_corp_office_nesting(tmp_path):
    db_path = tmp_path / "test.db"
    office_item_id = 900
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (2, TYPE_ID, office_item_id, "CorpSAG1", 300, 0, "My Corp (corp)"),
    ], db_path=db_path)

    rows = storage.search_item_stock_locations(TYPE_ID, db_path=db_path)

    assert rows == [(LOCATION_ID, None, "My Corp (corp)", 300)]


def test_search_item_stock_locations_prefers_structure_name_over_station_name(tmp_path):
    db_path = tmp_path / "test.db"
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 10, 0, "Alice"),
    ], db_path=db_path)
    storage.set_cached_structure_name(LOCATION_ID, "Player Structure Name", db_path=db_path)
    with storage.connect(db_path) as conn:
        conn.execute("INSERT INTO sde_stations VALUES (?,?,?)", (LOCATION_ID, 30000142, "NPC Station Name"))

    rows = storage.search_item_stock_locations(TYPE_ID, db_path=db_path)

    assert rows == [(LOCATION_ID, "Player Structure Name", "Alice", 10)]


def test_search_item_stock_locations_falls_back_to_station_name(tmp_path):
    db_path = tmp_path / "test.db"
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, LOCATION_ID, "Hangar", 10, 0, "Alice"),
    ], db_path=db_path)
    with storage.connect(db_path) as conn:
        conn.execute("INSERT INTO sde_stations VALUES (?,?,?)", (LOCATION_ID, 30000142, "NPC Station Name"))

    rows = storage.search_item_stock_locations(TYPE_ID, db_path=db_path)

    assert rows == [(LOCATION_ID, "NPC Station Name", "Alice", 10)]


def test_get_cached_structure_names_batches_and_preserves_was_cached_semantics(tmp_path):
    # Batched form of get_cached_structure_name (GET /logistics/structure-
    # names used to call the single-id version once per location_id in a
    # Python loop - N connections instead of one query). Three cases in one
    # call: a location that resolved to a real name, one that was attempted
    # and failed (cached as None - must NOT be re-resolved on every page
    # load), and one never attempted at all (was_cached=False).
    db_path = tmp_path / "test.db"
    storage.set_cached_structure_name(1, "Real Structure", db_path=db_path)
    storage.set_cached_structure_name(2, None, db_path=db_path)  # attempted, resolution failed

    result = storage.get_cached_structure_names([1, 2, 3], db_path=db_path)

    assert result[1] == (True, "Real Structure")
    assert result[2] == (True, None)
    assert result[3] == (False, None)


def test_get_cached_structure_names_empty_input_returns_empty_dict(tmp_path):
    db_path = tmp_path / "test.db"
    assert storage.get_cached_structure_names([], db_path=db_path) == {}
