"""Tests for storage.py's manual_listed_stock CRUD (docs/
MANUAL_TRACKING_PLAN.md phase 7), Postgres-backed."""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 34  # Tritanium


def test_upsert_then_qty(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_listed_stock(TYPE_ID, "home", 100.0)

        assert storage.manual_listed_stock_qty(TYPE_ID, "home") == 100.0
        assert storage.manual_listed_stock_qty(TYPE_ID, "jita") == 0.0


def test_upsert_is_upsert_not_duplicate(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_listed_stock(TYPE_ID, "home", 100.0)
        storage.upsert_manual_listed_stock(TYPE_ID, "home", 200.0)

        assert storage.manual_listed_stock_qty(TYPE_ID, "home") == 200.0


def test_home_and_jita_are_independent(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_listed_stock(TYPE_ID, "home", 100.0)
        storage.upsert_manual_listed_stock(TYPE_ID, "jita", 50.0)

        assert storage.manual_listed_stock_qty(TYPE_ID, "home") == 100.0
        assert storage.manual_listed_stock_qty(TYPE_ID, "jita") == 50.0


def test_delete_manual_listed_stock(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_listed_stock(TYPE_ID, "home", 100.0)
        storage.delete_manual_listed_stock(TYPE_ID, "home")

        assert storage.manual_listed_stock_qty(TYPE_ID, "home") == 0.0


def test_load_manual_listed_stock_returns_all_entries(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_listed_stock(TYPE_ID, "home", 100.0)
        storage.upsert_manual_listed_stock(TYPE_ID, "jita", 50.0)

        rows = storage.load_manual_listed_stock()

    assert set(rows.keys()) == {(TYPE_ID, "home"), (TYPE_ID, "jita")}
    assert rows[(TYPE_ID, "home")][0] == 100.0
    assert rows[(TYPE_ID, "jita")][0] == 50.0


def test_manual_listed_stock_is_tenant_isolated(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.upsert_manual_listed_stock(TYPE_ID, "home", 100.0)
    with storage.tenant_context(tenant_b):
        assert storage.manual_listed_stock_qty(TYPE_ID, "home") == 0.0
