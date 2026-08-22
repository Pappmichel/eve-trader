import uuid
from pathlib import Path

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_OBSERVABILITY_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "observability_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_observability_schema(_apply_phase1_schema):
    """docs/observability_schema.sql, applied once per session via the
    owner role - same pattern as test_doctrine_storage.py's own
    _apply_doctrine_schema. error_log is unscoped (like tool_grants), so
    unlike most other per-tenant schema files this only needs
    _apply_phase1_schema (the eve_trader_app role its GRANT targets), not
    phase2/phase3 too."""
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_OBSERVABILITY_SCHEMA_SQL.read_text(encoding="utf-8"))


# error_log has no per-tenant/per-test isolation to piggyback on (it's
# deliberately unscoped, see docs/observability_schema.sql) and nothing
# ever deletes from it - unlike tenant_pair's fresh uuids giving RLS tables
# free isolation, a fixed message string here would collide with the same
# test's own rows from an earlier run against the same persistent dev
# Postgres. A random suffix per test keeps each run's assertions accurate
# regardless of how many times this file has run before.
def _unique(label: str) -> str:
    return f"{label} {uuid.uuid4()}"


def test_log_error_and_list_errors_round_trip(tenant):
    message = _unique("TypeError: boom")
    storage.log_error("frontend", message, "at foo.tsx:12", "/trading")

    rows = storage.list_errors(limit=200)

    matching = [r for r in rows if r["message"] == message]
    assert len(matching) == 1
    row = matching[0]
    assert row["source"] == "frontend"
    assert row["detail"] == "at foo.tsx:12"
    assert row["path"] == "/trading"
    assert row["tenant_id"] == tenant
    assert row["created_at"] is not None


def test_log_error_allows_null_detail_and_path():
    message = _unique("unhandled exception")
    storage.log_error("backend", message, None, None)

    rows = storage.list_errors(limit=200)
    matching = [r for r in rows if r["message"] == message]
    assert len(matching) == 1
    assert matching[0]["detail"] is None
    assert matching[0]["path"] is None


def test_list_errors_respects_limit():
    for i in range(5):
        storage.log_error("frontend", _unique(f"error {i}"), None, None)

    assert len(storage.list_errors(limit=2)) == 2


def test_list_errors_is_cross_tenant_not_scoped_to_the_ambient_tenant(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    message_a, message_b = _unique("error from tenant A"), _unique("error from tenant B")
    with storage.tenant_context(tenant_a):
        storage.log_error("frontend", message_a, None, None)
    with storage.tenant_context(tenant_b):
        storage.log_error("frontend", message_b, None, None)

    # Reading has no ambient-tenant filtering at all - error_log is
    # deliberately unscoped (see docs/observability_schema.sql), same as
    # tool_grants/tenants, since the Admin tool's own "Recent Errors" view
    # is a cross-tenant operator concern by design.
    with storage.tenant_context(tenant_a):
        rows = storage.list_errors(limit=200)
    messages = {r["message"] for r in rows}
    assert message_a in messages
    assert message_b in messages
