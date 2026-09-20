"""P5-06 / P51H-01: every HTTP /api route is classified against the live
routing table, including mounted sub-applications.

Does not force business-logic tests through the access gate. Default suite
posture remains gate-off (see tests/conftest.py). This file asserts that a
newly added /api route cannot silently miss a gate classification.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount

from eve_trader.api.app import (
    _GATE_EXEMPT_PATHS,
    _TOOL_PATH_PREFIXES,
    _is_auth_role_gated_path,
    _is_docs_path,
    _is_session_only_api_path,
    _required_tool_for_path,
    create_app,
)
from eve_trader.config import ACCESS_CONFIG


def _join_prefix(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path:
        return prefix
    return prefix.rstrip("/") + (path if path.startswith("/") else "/" + path)


def _included_router_children(route):
    """FastAPI 0.141 keeps include_router() as a private `_IncludedRouter`.

    Isolated here so the walker can stay version-tolerant: public
    `Mount` / `APIRoute` attributes are preferred; this is the one
    compatibility hook for included routers.
    """
    nested = getattr(route, "original_router", None)
    ctx = getattr(route, "include_context", None)
    if nested is None or ctx is None:
        return None
    return nested.routes, ctx.prefix or ""


def _iter_http_routes(app):
    """Walk FastAPI/Starlette's live route graph.

    Supports APIRoute, nested/included routers, Mount (including mounted
    FastAPI/Starlette sub-apps), and dynamic path templates.
    """
    yield from _walk_routes(app.routes)


def _walk_routes(routes, prefix: str = ""):
    for route in routes:
        included = _included_router_children(route)
        if included is not None:
            child_routes, child_prefix = included
            yield from _walk_routes(child_routes, _join_prefix(prefix, child_prefix))
            continue
        if isinstance(route, Mount):
            mount_prefix = _join_prefix(prefix, route.path or "")
            child_routes = getattr(route.app, "routes", None)
            if child_routes is not None:
                yield from _walk_routes(child_routes, mount_prefix)
            else:
                # Opaque ASGI mount: still emit the mount point so an
                # /api/... sub-app cannot vanish from coverage.
                yield "MOUNT", mount_prefix
            continue
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods is None or path is None:
            continue
        full = _join_prefix(prefix, path)
        for method in sorted(methods):
            if method in {"HEAD", "OPTIONS"}:
                continue
            yield method, full


def _classify(method: str, path: str) -> str | None:
    if _is_docs_path(path) or not path.startswith("/api/"):
        return "non_gated"
    if path in _GATE_EXEMPT_PATHS:
        return "exempt"
    tool = _required_tool_for_path(path, method)
    if tool is not None:
        return f"tool:{tool}"
    if _is_auth_role_gated_path(path):
        return "auth_role_tool"
    if _is_session_only_api_path(path):
        return "session_only"
    return None


def _app_with_mounted_api_probe() -> FastAPI:
    inner = FastAPI()

    @inner.get("/leaked")
    def leaked():
        return {"ok": True}

    app = create_app()
    app.mount("/api/coverage-probe", inner)
    return app


def test_every_api_route_has_a_gate_classification():
    unclassified = []
    classified = []
    for method, path in _iter_http_routes(create_app()):
        bucket = _classify(method, path)
        if bucket is None:
            unclassified.append((method, path))
        elif bucket != "non_gated":
            classified.append((method, path, bucket))
    assert unclassified == [], (
        "New /api routes must be classified before they ship: add a "
        "_TOOL_PATH_PREFIXES entry, a _GATE_EXEMPT_PATHS entry, an "
        "_SESSION_ONLY_API_PREFIXES "
        f"prefix. Unclassified: {unclassified}"
    )
    assert any(bucket == "session_only" for _, _, bucket in classified)
    assert any(bucket == "exempt" for _, _, bucket in classified)
    assert any(bucket.startswith("tool:") for _, _, bucket in classified)
    assert classified, "expected the live routing table to contain /api routes"


def test_tool_prefixes_are_the_classification_source_for_prefixed_routes():
    for prefix, tool_key in _TOOL_PATH_PREFIXES.items():
        assert _required_tool_for_path(prefix + "settings", "GET") == tool_key


def test_unclassified_api_path_is_rejected_by_the_matrix():
    path = "/api/brand-new-ungated/widget"
    assert _required_tool_for_path(path, "GET") is None
    assert path not in _GATE_EXEMPT_PATHS
    assert not _is_session_only_api_path(path)
    assert not _is_auth_role_gated_path(path)
    assert _classify("GET", path) is None


def test_walker_sees_normal_and_dynamic_and_method_specific_routes():
    paths = {(method, path) for method, path in _iter_http_routes(create_app())}
    assert ("GET", "/api/trading/settings") in paths
    assert ("DELETE", "/api/trading/auth/character/{role_key}") in paths
    assert ("GET", "/api/portfolio/backups") in paths
    assert ("POST", "/api/portfolio/backups") in paths
    assert _classify("GET", "/api/portfolio/backups") == "tool:portfolio"
    assert _classify("POST", "/api/portfolio/backups") == "tool:admin"


def test_walker_sees_included_auth_router_templates():
    paths = {(method, path) for method, path in _iter_http_routes(create_app())}
    assert ("GET", "/api/auth/gate/start") in paths
    assert ("GET", "/api/auth/callback") in paths
    assert ("GET", "/api/characters/sharing") in paths
    assert ("GET", "/api/auth/{role_prefix}/start") not in paths
    assert ("GET", "/api/auth/{role_prefix}/access-preview") not in paths
    assert _classify("GET", "/api/auth/gate/start") == "exempt"
    assert _classify("GET", "/api/characters/sharing") == "tool:characters"
    assert _classify("GET", "/api/characters/reauth/start") == "tool:characters"


def test_walker_sees_nested_router_include():
    inner = APIRouter()

    @inner.get("/nested-leaf")
    def nested_leaf():
        return {"ok": True}

    outer = APIRouter()
    outer.include_router(inner, prefix="/nested")
    app = FastAPI()
    app.include_router(outer, prefix="/api/coverage-nested")
    paths = {(method, path) for method, path in _iter_http_routes(app)}
    assert ("GET", "/api/coverage-nested/nested/nested-leaf") in paths
    assert _classify("GET", "/api/coverage-nested/nested/nested-leaf") is None


def test_walker_sees_mounted_fastapi_subapp():
    """P51H-01: a Mount under /api must not disappear from classification."""
    app = _app_with_mounted_api_probe()
    paths = {(method, path) for method, path in _iter_http_routes(app)}
    assert ("GET", "/api/coverage-probe/leaked") in paths
    assert _classify("GET", "/api/coverage-probe/leaked") is None


def test_walker_emits_opaque_api_mount_instead_of_dropping_it():
    app = FastAPI()

    async def bare_asgi(scope, receive, send):
        raise AssertionError("opaque mount must not be invoked by the walker")

    app.mount("/api/opaque-probe", bare_asgi)
    paths = list(_iter_http_routes(app))
    assert ("MOUNT", "/api/opaque-probe") in paths
    assert _classify("MOUNT", "/api/opaque-probe") is None


@pytest.mark.gate_enforced
def test_mounted_api_subapp_unauthenticated_is_401_at_runtime():
    """Classification gap ≠ auth bypass: middleware still 401s the mount."""
    assert ACCESS_CONFIG.access_gate_enabled is True
    client = TestClient(_app_with_mounted_api_probe())
    resp = client.get("/api/coverage-probe/leaked")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}
