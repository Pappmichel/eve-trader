"""Postgres tests for storage.save_latest_buy_list / load_latest_buy_list:
round-trip, wholesale-replace, and RLS tenant isolation.
"""
from pathlib import Path

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant, tenant_pair  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_BUY_LIST_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "production_buy_list_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_production_buy_list_schema(_apply_phase1_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_BUY_LIST_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _wipe_buy_list():
    pg_helpers.wipe_tables("production_buy_list")
    yield


def test_save_and_load_latest_buy_list_round_trip(tenant):
    storage.save_latest_buy_list([(36, 21100000.0), (38, 321000.0)])

    loaded = storage.load_latest_buy_list()
    assert loaded == {36: 21100000.0, 38: 321000.0}


def test_save_latest_buy_list_wholesale_replaces_previous_rows(tenant):
    storage.save_latest_buy_list([(36, 100.0), (38, 50.0)])
    storage.save_latest_buy_list([(39, 14.0)])

    assert storage.load_latest_buy_list() == {39: 14.0}


def test_save_latest_buy_list_empty_clears_previous_rows(tenant):
    storage.save_latest_buy_list([(36, 100.0)])
    storage.save_latest_buy_list([])

    assert storage.load_latest_buy_list() == {}


def test_load_latest_buy_list_empty_when_never_saved(tenant):
    assert storage.load_latest_buy_list() == {}


def test_buy_list_of_tenant_a_is_invisible_to_tenant_b(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.save_latest_buy_list([(36, 100.0)])
        assert storage.load_latest_buy_list() == {36: 100.0}

    with storage.tenant_context(tenant_b):
        assert storage.load_latest_buy_list() == {}
        storage.save_latest_buy_list([(38, 7.0)])
        assert storage.load_latest_buy_list() == {38: 7.0}

    with storage.tenant_context(tenant_a):
        assert storage.load_latest_buy_list() == {36: 100.0}
