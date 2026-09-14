"""P5-06: every HTTP /api route is classified against the live routing table.

Does not force business-logic tests through the access gate. Default suite
posture remains gate-off (see tests/conftest.py). This file only asserts
that a newly added /api route cannot silently miss a gate classification.
"""
from __future__ import annotations

from eve_trader.api.app import (
    _GATE_EXEMPT_PATHS,
    _TOOL_PATH_PREFIXES,
    _is_auth_role_gated_path,
    _is_docs_path,
    _is_session_only_api_path,
    _required_tool_for_path,
    create_app,
)


def _join_prefix(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path:
        return prefix
    return prefix.rstrip("/") + (path if path.startswith("/") else "/" + path)


def _iter_http_routes(app):
    """Walk FastAPI's live route graph, including included routers.

    FastAPI 0.141 keeps included routers as `_IncludedRouter` wrappers
    rather than flattening `APIRoute` objects onto `app.routes`.
    """
    yield from _walk_routes(app.routes)


def _walk_routes(routes, prefix: str = ""):
    for route in routes:
        nested = getattr(route, "original_router", None)
        ctx = getattr(route, "include_context", None)
        if nested is not None and ctx is not None:
            yield from _walk_routes(nested.routes, _join_prefix(prefix, ctx.prefix or ""))
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
        # FastAPI template /api/auth/{role_prefix}/start|consent — tool is
        # resolved from ROLE_PREFIX_TOOL at request time, not from the
        # template string.
        return "auth_role_tool"
    if _is_session_only_api_path(path):
        return "session_only"
    return None


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
        "_AUTH_GATED_SUFFIXES match, or a _SESSION_ONLY_API_PREFIXES "
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
