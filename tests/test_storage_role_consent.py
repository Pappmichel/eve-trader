"""Tests for storage.has_role_consent/record_role_consent (docs/
role_consent_schema.sql's tenant_role_consents) - the login-role
data-access confirmation tracking.
"""
import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_role_consent_schema, tenant, tenant_pair  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def test_has_role_consent_is_false_before_any_record(tenant):
    assert storage.has_role_consent("producer") is False


def test_record_role_consent_then_has_role_consent_is_true(tenant):
    storage.record_role_consent("producer")
    assert storage.has_role_consent("producer") is True


def test_record_role_consent_is_scoped_per_role_prefix(tenant):
    storage.record_role_consent("buyer")
    assert storage.has_role_consent("buyer") is True
    assert storage.has_role_consent("seller") is False


def test_record_role_consent_is_idempotent(tenant):
    storage.record_role_consent("doctrine")
    storage.record_role_consent("doctrine")  # must not raise (ON CONFLICT DO NOTHING)
    assert storage.has_role_consent("doctrine") is True


def test_role_consent_is_isolated_per_tenant(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.record_role_consent("producer")

    with storage.tenant_context(tenant_b):
        assert storage.has_role_consent("producer") is False

    with storage.tenant_context(tenant_a):
        assert storage.has_role_consent("producer") is True
