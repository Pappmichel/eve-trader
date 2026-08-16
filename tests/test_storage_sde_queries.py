from eve_trader import storage


def _insert_type(db_path, type_id, name, published=1, group_id=1):
    with storage.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published, market_group_id, meta_level) "
            "VALUES (?, ?, ?, 1.0, ?, NULL, NULL)",
            (type_id, group_id, name, published),
        )


def _insert_product(db_path, blueprint_type_id, activity_id, product_type_id, quantity):
    with storage.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sde_blueprint_products (blueprint_type_id, activity_id, product_type_id, quantity) "
            "VALUES (?, ?, ?, ?)",
            (blueprint_type_id, activity_id, product_type_id, quantity),
        )


def _insert_probability(db_path, t1_blueprint_type_id, product_type_id, probability):
    with storage.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sde_invention_probability (t1_blueprint_type_id, product_type_id, probability) "
            "VALUES (?, ?, ?)",
            (t1_blueprint_type_id, product_type_id, probability),
        )


def test_get_blueprint_for_product_prefers_published_blueprint(tmp_path):
    # Confirmed real bug against live data: product 16672 (Tungsten Carbide)
    # has an unpublished leftover "Test Reaction Blueprint" (blueprint_type_id
    # 45732, produces 20/run) alongside the real published one (46207,
    # produces 10000/run) - without a published-first tiebreak this could
    # return either one.
    db_path = tmp_path / "test.db"
    _insert_type(db_path, 45732, "Test Reaction Blueprint", published=0)
    _insert_type(db_path, 46207, "Tungsten Carbide Reaction Formula", published=1)
    _insert_product(db_path, 45732, 11, 16672, 20.0)
    _insert_product(db_path, 46207, 11, 16672, 10000.0)

    row = storage.get_blueprint_for_product(16672, db_path=db_path)
    assert row == (46207, 11, 10000.0)


def test_find_invention_recipe_by_product_name_is_case_and_whitespace_insensitive(tmp_path):
    db_path = tmp_path / "test.db"
    _insert_type(db_path, 100, "Loki")
    _insert_type(db_path, 101, "Loki Blueprint")
    _insert_product(db_path, 200, 8, 101, 1.0)

    assert storage.find_invention_recipe_by_product_name("loki blueprint", db_path=db_path) == (200, 101)
    assert storage.find_invention_recipe_by_product_name("  Loki Blueprint  ", db_path=db_path) == (200, 101)


def test_find_invention_recipe_by_product_type_id_prefers_highest_probability(tmp_path):
    # Confirmed real bug: T3 hulls/subsystems have 3 valid relic BPCs
    # (Intact/Malfunctioning/Wrecked) with different success probability but
    # identical materials/time - without an explicit ORDER BY, whichever row
    # SQLite's unordered scan returned first won, by accident not by design.
    db_path = tmp_path / "test.db"
    product_type_id = 999
    _insert_product(db_path, 301, 8, product_type_id, 1.0)  # Wrecked - worst odds
    _insert_product(db_path, 302, 8, product_type_id, 1.0)  # Intact - best odds
    _insert_product(db_path, 303, 8, product_type_id, 1.0)  # Malfunctioning - middle
    _insert_probability(db_path, 301, product_type_id, 0.14)
    _insert_probability(db_path, 302, product_type_id, 0.26)
    _insert_probability(db_path, 303, product_type_id, 0.21)

    storage.find_invention_recipe_by_product_type_id.cache_clear()
    result = storage.find_invention_recipe_by_product_type_id(product_type_id, db_path=db_path)
    assert result == 302


def test_search_sde_types_tolerates_incidental_whitespace(tmp_path):
    db_path = tmp_path / "test.db"
    _insert_type(db_path, 34, "Tritanium")

    assert storage.search_sde_types("  Tritanium  ", db_path=db_path) == [(34, "Tritanium")]
