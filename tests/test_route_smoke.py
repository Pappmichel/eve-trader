"""Route smoke test: every operation in the OpenAPI schema is called once with
dummy parameters (gate off, a throwaway tenant, the test database) and must
never answer 500 and never the router's bare 404 (a path that is not wired). A 500
means an unhandled error on a path no other test reaches. 400/401/403/409/422
and redirects are fine: `_wrap` turns ActionError into 400, and a dummy body
usually fails validation with 422 before the action runs.

Network and background work is stubbed through the module objects (see the
fixture), so no ESI/Goonmetrics/SDE request is made and no worker thread is
started. Bodies contain only the schema's required fields, so settings routes
get an empty override set and leave the shared config instances alone.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
import requests
import requests.adapters
from fastapi.testclient import TestClient

from eve_trader import esi_client, storage
from eve_trader.api.app import create_app
from eve_trader.config import ACCESS_CONFIG

from .pg_helpers import OWNER_DSN, _apply_phase1_schema, postgres_required  # noqa: F401

pytestmark = postgres_required()

_APP = create_app()
_SCHEMA = _APP.openapi()
_METHODS = {"get", "post", "put", "patch", "delete"}

# Routes another branch deletes (audit package S-1). They still exist here, so
# they are skipped rather than asserted on; drop this set once that merges.
_REMOVED_IN_PARALLEL_BRANCH = {
    ("GET", "/api/admin/access-requests/count"),
    ("GET", "/api/production/alchemy-compare/{product_name}"),
    ("POST", "/api/production/stock-targets/dynamic-preview"),
    ("GET", "/api/production/stock-targets/{type_id}/dynamic-history"),
}

# Routes that cannot be called safely even with stubs, with the reason.
_EXCLUDED = {
    # OAuth redirects to the real EVE SSO / Discord (build a URL only, but the
    # callbacks exchange a code over the network).
    ("GET", "/api/auth/callback"): "SSO code exchange",
    ("GET", "/api/char-alerts/discord/callback"): "Discord code exchange",
}


# Real 500s this test found. Pinned as strict xfails (not fixed in the commit
# that added the test) so the fix flips them to a failure that asks for the
# marker to be removed.
_KNOWN_500 = {
    ("POST", "/api/production/manual-build-buy"):
        "an unknown decision reaches storage's bare `assert decision in (Build, Buy)`",
    ("POST", "/api/trading/universe/build"):
        "ESIError while ESI is unreachable is not mapped to a 400/502",
}


def _operations():
    for path, item in sorted(_SCHEMA["paths"].items()):
        for method, op in sorted(item.items()):
            if method in _METHODS:
                yield method.upper(), path, op


_PARAMS = [
    pytest.param(method, path, op, id=f"{method} {path}", marks=(
        [pytest.mark.xfail(reason=_KNOWN_500[(method, path)], strict=True)]
        if (method, path) in _KNOWN_500 else []))
    for method, path, op in _operations()
    if (method, path) not in _REMOVED_IN_PARALLEL_BRANCH and (method, path) not in _EXCLUDED
]


def _resolve(schema: dict) -> dict:
    ref = schema.get("$ref")
    if ref:
        node = _SCHEMA
        for part in ref.removeprefix("#/").split("/"):
            node = node[part]
        return _resolve(node)
    return schema


def _dummy(schema: dict, name: str = "", depth: int = 0):
    schema = _resolve(schema)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if _resolve(o).get("type") != "null"]
            return _dummy(options[0], name, depth) if options else None
    if "allOf" in schema:
        return _dummy(schema["allOf"][0], name, depth)
    kind = schema.get("type")
    fmt = schema.get("format")
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "number":
        return max(1.0, float(schema.get("minimum", 1.0)))
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object" or "properties" in schema:
        if depth > 4:
            return {}
        required = schema.get("required", [])
        props = schema.get("properties", {})
        return {k: _dummy(props[k], k, depth + 1) for k in required if k in props}
    if fmt == "uuid":
        return str(uuid.uuid4())
    if fmt == "date":
        return "2026-01-01"
    if fmt == "date-time":
        return "2026-01-01T00:00:00Z"
    return "x"


def _request_kwargs(path: str, op: dict) -> tuple[str, dict]:
    url = path
    query = {}
    for param in op.get("parameters", []):
        value = _dummy(param.get("schema", {}), param["name"])
        if param["in"] == "path":
            if value == "x":
                # A valid uuid also passes for plain string ids; several storage
                # lookups (doctrine, fittings, special orders) cast the id to
                # uuid in SQL, and "x" there is an unhandled 500 (reported
                # separately, not asserted here).
                value = str(uuid.uuid4())
            url = url.replace("{" + param["name"] + "}", str(value))
        elif param["in"] == "query" and param.get("required"):
            query[param["name"]] = value
    kwargs: dict = {"params": query}
    content = op.get("requestBody", {}).get("content", {})
    if "application/json" in content:
        kwargs["json"] = _dummy(content["application/json"].get("schema", {}))
    return url, kwargs


def _delete_tenant_rows(tenant_id: str) -> None:
    with psycopg.connect(OWNER_DSN, autocommit=True) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT c.table_name FROM information_schema.columns c "
            "JOIN information_schema.tables t USING (table_schema, table_name) "
            "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' "
            "AND t.table_type = 'BASE TABLE'").fetchall()]
        conn.execute("SET session_replication_role = replica")
        for table in tables:
            conn.execute(f'DELETE FROM "{table}" WHERE tenant_id = %s', (tenant_id,))
        conn.execute("SET session_replication_role = DEFAULT")


@pytest.fixture(scope="module")
def smoke_tenant():
    """A throwaway tenant standing in for DEFAULT_TENANT_ID, so mutating routes
    never leave rows in the real default tenant for another test to trip over."""
    tenant_id = storage.create_tenant("route-smoke")
    yield tenant_id
    _delete_tenant_rows(tenant_id)
    with psycopg.connect(OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))


@pytest.fixture
def smoke_client(monkeypatch, smoke_tenant):
    from eve_trader import pipeline_runner

    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(storage, "DEFAULT_TENANT_ID", smoke_tenant)
    # The network is down: every outbound request (ESI, Goonmetrics, SDE host)
    # fails like an outage, so routes exercise their real error handling and
    # no retry backoff sleeps. Replaces the blanket block of conftest's
    # `_no_real_http` for this module only; nothing is ever sent.
    def _offline(self, request, *args, **kwargs):
        raise requests.ConnectionError(f"offline in route smoke test: {request.url}")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _offline)
    monkeypatch.setattr(esi_client.time, "sleep", lambda seconds: None)
    # No worker threads: every background job reports as started.
    monkeypatch.setattr(pipeline_runner, "start_job", lambda tool, job_name, label, worker: {
        "run_id": "smoke", "status": "running", "job_name": job_name, "tool": tool})
    return TestClient(_APP)


@pytest.mark.parametrize("method, path, op", _PARAMS)
def test_route_is_wired_and_does_not_500(smoke_client, method, path, op):
    url, kwargs = _request_kwargs(path, op)
    try:
        resp = smoke_client.request(method, url, follow_redirects=False, **kwargs)
    except Exception as exc:  # an unhandled error inside the app
        pytest.fail(f"{method} {url} raised {type(exc).__name__}: {exc}")
    assert resp.status_code != 500, f"{method} {url}: {resp.text[:300]}"
    # A domain 404 ("plan not found") carries its own detail; only the router's
    # bare "Not Found" means the path is not wired.
    assert not (resp.status_code == 404 and resp.json() == {"detail": "Not Found"}), f"{method} {url} is not routed"
