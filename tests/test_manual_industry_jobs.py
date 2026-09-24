"""Tests for storage.py's manual_industry_jobs CRUD/engine-helper functions
(docs/MANUAL_TRACKING_PLAN.md phase 6), Postgres-backed."""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

PRODUCT_TYPE_ID = 587  # Rifter
LOCATION_A = 1000000000001
LOCATION_B = 1000000000002


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("sde_types", "manual_stock")
    yield


def _insert_sde_type(type_id: int, name: str) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, type_name, published) VALUES (?, ?, 1) "
            "ON CONFLICT (type_id) DO UPDATE SET type_name = excluded.type_name",
            (type_id, name),
        )


def test_insert_then_get_manual_industry_job(tenant):
    with storage.tenant_context(tenant):
        job_id = storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)

        row = storage.get_manual_industry_job(job_id)

    assert row == (job_id, PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)


def test_get_manual_industry_job_missing_returns_none(tenant):
    with storage.tenant_context(tenant):
        assert storage.get_manual_industry_job(999999) is None


def test_update_manual_industry_job(tenant):
    with storage.tenant_context(tenant):
        job_id = storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)

        storage.update_manual_industry_job(job_id, 100.0, 20, LOCATION_B, None)

        row = storage.get_manual_industry_job(job_id)
    assert row == (job_id, PRODUCT_TYPE_ID, 1, 100.0, 20, LOCATION_B, None)


def test_delete_manual_industry_job(tenant):
    with storage.tenant_context(tenant):
        job_id = storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)
        storage.delete_manual_industry_job(job_id)

        assert storage.get_manual_industry_job(job_id) is None


def test_load_manual_industry_jobs_includes_product_name(tenant):
    _insert_sde_type(PRODUCT_TYPE_ID, "Rifter")
    with storage.tenant_context(tenant):
        job_id = storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)

        rows = storage.load_manual_industry_jobs()

    assert rows == [(job_id, PRODUCT_TYPE_ID, "Rifter", 1, 50.0, 10, LOCATION_A, None)]


def test_manual_incoming_qty_sums_across_jobs(tenant):
    with storage.tenant_context(tenant):
        storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)
        storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 30.0, None, LOCATION_B, None)
        storage.insert_manual_industry_job(999, 1, 999.0, None, LOCATION_A, None)  # a different product

        assert storage.manual_incoming_qty(PRODUCT_TYPE_ID) == 80.0


def test_manual_incoming_qty_zero_when_no_jobs(tenant):
    with storage.tenant_context(tenant):
        assert storage.manual_incoming_qty(PRODUCT_TYPE_ID) == 0.0


def test_complete_manual_job_deletes_job_and_credits_manual_stock(tenant):
    with storage.tenant_context(tenant):
        job_id = storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)
        storage.upsert_manual_stock(PRODUCT_TYPE_ID, 20.0, LOCATION_A)  # pre-existing stock at that location

        storage.complete_manual_job(job_id, LOCATION_A)

        assert storage.get_manual_industry_job(job_id) is None
        assert storage.manual_stock_at_location(PRODUCT_TYPE_ID, LOCATION_A) == 70.0  # 20 + 50


def test_complete_manual_job_credits_a_different_location_than_the_job_s_own(tenant):
    with storage.tenant_context(tenant):
        job_id = storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)

        storage.complete_manual_job(job_id, LOCATION_B)

        assert storage.manual_stock_at_location(PRODUCT_TYPE_ID, LOCATION_A) == 0.0
        assert storage.manual_stock_at_location(PRODUCT_TYPE_ID, LOCATION_B) == 50.0


def test_complete_manual_job_is_a_no_op_for_a_missing_job(tenant):
    with storage.tenant_context(tenant):
        storage.complete_manual_job(999999, LOCATION_A)  # must not raise
        assert storage.manual_stock_at_location(PRODUCT_TYPE_ID, LOCATION_A) == 0.0


def test_manual_industry_jobs_are_tenant_isolated(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.insert_manual_industry_job(PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)
    with storage.tenant_context(tenant_b):
        assert storage.load_manual_industry_jobs() == []
