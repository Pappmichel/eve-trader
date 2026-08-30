import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _wipe():
    # sde_types/sde_blueprint_products/sde_invention_probability are shared
    # tables (no tenant_id at all) - wipe before each test so a leftover row
    # from an earlier test can never affect this one, regardless of which
    # ids happen to be reused. Note: conftest.py's autouse
    # _clear_storage_lru_caches fixture already handles the @lru_cache'd
    # storage functions (get_blueprint_for_product,
    # find_invention_recipe_candidates_by_product_type_id) reading this data.
    pg_helpers.wipe_tables("sde_types", "sde_blueprint_products", "sde_invention_probability")
    yield


def _insert_type(type_id, name, published=1, group_id=1):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published, market_group_id, meta_level) "
            "VALUES (?, ?, ?, 1.0, ?, NULL, NULL)",
            (type_id, group_id, name, published),
        )


def _insert_product(blueprint_type_id, activity_id, product_type_id, quantity):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_blueprint_products (blueprint_type_id, activity_id, product_type_id, quantity) "
            "VALUES (?, ?, ?, ?)",
            (blueprint_type_id, activity_id, product_type_id, quantity),
        )


def _insert_probability(t1_blueprint_type_id, product_type_id, probability):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_invention_probability (t1_blueprint_type_id, product_type_id, probability) "
            "VALUES (?, ?, ?)",
            (t1_blueprint_type_id, product_type_id, probability),
        )


def test_get_blueprint_for_product_prefers_published_blueprint(tenant):
    # Confirmed real bug against live data: product 16672 (Tungsten Carbide)
    # has an unpublished leftover "Test Reaction Blueprint" (blueprint_type_id
    # 45732, produces 20/run) alongside the real published one (46207,
    # produces 10000/run) - without a published-first tiebreak this could
    # return either one.
    _insert_type(45732, "Test Reaction Blueprint", published=0)
    _insert_type(46207, "Tungsten Carbide Reaction Formula", published=1)
    _insert_product(45732, 11, 16672, 20.0)
    _insert_product(46207, 11, 16672, 10000.0)

    row = storage.get_blueprint_for_product(16672)
    assert row == (46207, 11, 10000.0)


def test_get_blueprint_for_product_none_when_only_blueprint_is_unpublished(tenant):
    # Confirmed live 2026-08-19 (GitHub issue #7): Freki (32207) and Utu
    # (2834) both have meta_group_id=4 "Faction" (a genuinely buildable
    # category in general) but their *only* blueprint row is CCP-unpublished
    # (Freki Blueprint 32208, Utu Blueprint 2835) - never actually obtainable
    # by any player. Without excluding unpublished rows outright (not just
    # deprioritizing them), classify_activity would still treat these as
    # buildable and they'd leak into the Margin tab / build-candidate scans.
    _insert_type(32208, "Freki Blueprint", published=0)
    _insert_product(32208, 1, 32207, 1.0)

    assert storage.get_blueprint_for_product(32207) is None


def test_find_invention_recipe_by_product_name_is_case_and_whitespace_insensitive(tenant):
    _insert_type(100, "Loki")
    _insert_type(101, "Loki Blueprint")
    _insert_product(200, 8, 101, 1.0)

    assert storage.find_invention_recipe_by_product_name("loki blueprint") == (200, 101)
    assert storage.find_invention_recipe_by_product_name("  Loki Blueprint  ") == (200, 101)


def test_find_invention_recipe_candidates_by_product_type_id_orders_by_probability(tenant):
    # T3 hulls/subsystems have 3 valid relic "blueprints" (Intact/
    # Malfunctioning/Wrecked) with different success probability but
    # identical materials/time - every candidate must be returned (not
    # collapsed to just the best one, confirmed real bug reported by a user,
    # 2026-08-30: a single-result predecessor of this function silently
    # discarded the other two grades, so a real buy-cost-vs-odds tradeoff
    # could never even be considered), ordered best-probability-first for a
    # deterministic first element.
    product_type_id = 999
    _insert_product(301, 8, product_type_id, 1.0)  # Wrecked - worst odds
    _insert_product(302, 8, product_type_id, 1.0)  # Intact - best odds
    _insert_product(303, 8, product_type_id, 1.0)  # Malfunctioning - middle
    _insert_probability(301, product_type_id, 0.14)
    _insert_probability(302, product_type_id, 0.26)
    _insert_probability(303, product_type_id, 0.21)

    result = storage.find_invention_recipe_candidates_by_product_type_id(product_type_id)
    assert result == (302, 303, 301)


def test_find_invention_recipe_candidates_by_product_type_id_single_result_for_tech_ii(tenant):
    _insert_product(200, 8, 101, 1.0)
    _insert_probability(200, 101, 0.4)

    assert storage.find_invention_recipe_candidates_by_product_type_id(101) == (200,)


def test_find_invention_recipe_candidates_by_product_type_id_empty_for_uninvented(tenant):
    assert storage.find_invention_recipe_candidates_by_product_type_id(999999) == ()


def test_search_sde_types_tolerates_incidental_whitespace(tenant):
    _insert_type(34, "Tritanium")

    assert storage.search_sde_types("  Tritanium  ") == [(34, "Tritanium")]
