"""Manual blueprint copy costs are keyed by the product built from the copy.
A blueprint's own name used to be stored under the blueprint's type id and
was never applied; the add form now maps it to the product, and
phase1_schema.sql moves rows stored that way."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

PRODUCT = 990_701
BLUEPRINT = 990_702
ORE = 990_703
SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "phase1_schema.sql"


@pytest.fixture(autouse=True)
def _sde_rows():
    with storage.connect_unscoped() as conn:
        for type_id, name in ((PRODUCT, "Keying Test Dread"), (BLUEPRINT, "Keying Test Dread Blueprint"),
                              (ORE, "Keying Test Ore")):
            conn.execute(
                "INSERT INTO sde_types (type_id, type_name, published) VALUES (?, ?, 1) "
                "ON CONFLICT (type_id) DO UPDATE SET type_name = excluded.type_name",
                (type_id, name),
            )
        conn.execute(
            "INSERT INTO sde_blueprint_products (blueprint_type_id, activity_id, product_type_id, quantity) "
            "VALUES (?, 1, ?, 1) ON CONFLICT DO NOTHING",
            (BLUEPRINT, PRODUCT),
        )
    _clear_sde_caches()
    yield
    with storage.connect_unscoped() as conn:
        conn.execute("DELETE FROM sde_blueprint_products WHERE blueprint_type_id = ?", (BLUEPRINT,))
        conn.execute("DELETE FROM sde_types WHERE type_id = ANY(?)", ([PRODUCT, BLUEPRINT, ORE],))
    _clear_sde_caches()


def _clear_sde_caches():
    storage.get_blueprint_for_product.cache_clear()
    storage.get_sde_type.cache_clear()


def _rows():
    return {tid: (name, cost, runs) for tid, name, cost, runs in storage.load_manual_blueprint_copy_costs()}


def _migration_sql() -> str:
    match = re.search(r"UPDATE manual_blueprint_copy_costs m.*?;", SCHEMA.read_text(encoding="utf-8"), re.S)
    assert match, "migration statement missing from phase1_schema.sql"
    return match.group(0)


def test_blueprint_name_is_stored_under_its_product(tenant):
    result = actions.do_add_manual_blueprint_copy_cost("Keying Test Dread Blueprint", 1_000_000_000.0, 1)
    assert result["type_id"] == PRODUCT
    assert result["type_name"] == "Keying Test Dread"
    assert _rows() == {PRODUCT: ("Keying Test Dread", 1_000_000_000.0, 1)}
    assert storage.get_manual_blueprint_copy_cost_per_run(PRODUCT) == 1_000_000_000.0


def test_product_name_is_stored_unchanged(tenant):
    result = actions.do_add_manual_blueprint_copy_cost("Keying Test Dread", 800_000_000.0, 2)
    assert result["type_id"] == PRODUCT
    assert _rows() == {PRODUCT: ("Keying Test Dread", 800_000_000.0, 2)}


def test_item_without_blueprint_is_rejected(tenant):
    with pytest.raises(ActionError, match="not built from a blueprint"):
        actions.do_add_manual_blueprint_copy_cost("Keying Test Ore", 1.0, 1)
    assert _rows() == {}


def test_migration_moves_a_row_keyed_by_the_blueprint(tenant):
    storage.upsert_manual_blueprint_copy_cost(BLUEPRINT, "Keying Test Dread Blueprint", 1_120_000_000.0, 1)
    with storage.connect() as conn:
        conn.execute(_migration_sql())
    assert _rows() == {PRODUCT: ("Keying Test Dread", 1_120_000_000.0, 1)}
    # Idempotent: a second run changes nothing.
    with storage.connect() as conn:
        conn.execute(_migration_sql())
    assert _rows() == {PRODUCT: ("Keying Test Dread", 1_120_000_000.0, 1)}


def test_migration_keeps_both_rows_when_the_product_already_has_one(tenant):
    storage.upsert_manual_blueprint_copy_cost(PRODUCT, "Keying Test Dread", 900_000_000.0, 1)
    storage.upsert_manual_blueprint_copy_cost(BLUEPRINT, "Keying Test Dread Blueprint", 1_120_000_000.0, 1)
    with storage.connect() as conn:
        conn.execute(_migration_sql())
    assert _rows() == {
        PRODUCT: ("Keying Test Dread", 900_000_000.0, 1),
        BLUEPRINT: ("Keying Test Dread Blueprint", 1_120_000_000.0, 1),
    }
