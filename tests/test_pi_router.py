"""PI router: marshaling, ActionError -> 400, and the `pi` grant gate. The
actions are monkeypatched on the module object (CLAUDE.md testing
conventions); no Postgres needed except for the grant test."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import _TOOL_PATH_PREFIXES, _required_tool_for_path, create_app
from eve_trader.pi import actions as pi_actions

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
)
from .test_char_info import _TENANT, _cookie, _enable_gate, _gate_tables, _provision  # noqa: F401

client = TestClient(create_app())


def _boom(*_a, **_k):
    raise ActionError("nope")


def test_prefix_requires_pi_tool():
    assert _TOOL_PATH_PREFIXES["/api/pi/"] == "pi"
    assert _required_tool_for_path("/api/pi/meta") == "pi"


def test_get_meta_and_error(monkeypatch):
    monkeypatch.setattr(pi_actions, "do_get_meta", lambda: {"chains": ["P0-P1"]})
    r = client.get("/api/pi/meta")
    assert r.status_code == 200 and r.json() == {"chains": ["P0-P1"]}
    monkeypatch.setattr(pi_actions, "do_get_meta", _boom)
    r = client.get("/api/pi/meta")
    assert r.status_code == 400 and r.json()["detail"] == "nope"


def test_profitability_passes_query(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_profitability", lambda **kw: seen.update(kw) or {"rows": []})
    r = client.get("/api/pi/profitability?zone=lowsec&cc_level=4")
    assert r.status_code == 200
    assert seen == {"zone": "lowsec", "cc_level": 4}
    monkeypatch.setattr(pi_actions, "do_profitability", _boom)
    assert client.get("/api/pi/profitability").status_code == 400


def test_planner_passes_body_and_validates(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_planner", lambda **kw: seen.update(kw) or {"ok": True})
    r = client.post("/api/pi/planner", json={"chain": "P0-P1", "product_type_id": 2389, "planet_type_id": 11,
                                             "radius_km": 4000})
    assert r.status_code == 200
    assert seen["chain"] == "P0-P1" and seen["product_type_id"] == 2389 and seen["radius_km"] == 4000
    assert seen["design"] is None and seen["planet_id"] is None
    assert client.post("/api/pi/planner", json={"chain": "P0-P1", "product_type_id": 1, "cc_level": 9}).status_code == 422
    monkeypatch.setattr(pi_actions, "do_planner", _boom)
    assert client.post("/api/pi/planner", json={"chain": "x", "product_type_id": 1}).status_code == 400


def test_chain_and_systems(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_chain", lambda **kw: seen.update(chain=kw) or {})
    monkeypatch.setattr(pi_actions, "do_search_systems", lambda query: seen.update(q=query) or [])
    monkeypatch.setattr(pi_actions, "do_system_planets", lambda solar_system_id: seen.update(sid=solar_system_id) or {})
    assert client.get("/api/pi/chain/2389?zone=nullsec").status_code == 200
    assert seen["chain"] == {"product_type_id": 2389, "zone": "nullsec", "cc_level": None, "per_hour": None}
    assert client.get("/api/pi/systems?q=jita").status_code == 200 and seen["q"] == "jita"
    assert client.get("/api/pi/systems/30000142").status_code == 200 and seen["sid"] == 30000142
    monkeypatch.setattr(pi_actions, "do_system_planets", _boom)
    assert client.get("/api/pi/systems/1").status_code == 400


def test_plans_crud(monkeypatch):
    calls = []
    monkeypatch.setattr(pi_actions, "do_list_plans", lambda: [{"plan_id": 1}])
    monkeypatch.setattr(pi_actions, "do_save_plan", lambda plan, plan_id=None: calls.append((plan, plan_id)) or {"plan_id": 7})
    monkeypatch.setattr(pi_actions, "do_delete_plan", lambda plan_id: {"deleted": plan_id})
    assert client.get("/api/pi/plans").json() == [{"plan_id": 1}]
    assert client.post("/api/pi/plans", json={"name": "a"}).json() == {"plan_id": 7}
    assert client.put("/api/pi/plans/7", json={"name": "b"}).status_code == 200
    assert calls == [({"name": "a"}, None), ({"name": "b"}, 7)]
    assert client.delete("/api/pi/plans/7").json() == {"deleted": 7}
    monkeypatch.setattr(pi_actions, "do_save_plan", _boom)
    assert client.post("/api/pi/plans", json={}).status_code == 400
    monkeypatch.setattr(pi_actions, "do_delete_plan", _boom)
    assert client.delete("/api/pi/plans/1").status_code == 400


def test_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_get_settings", lambda: {"pi_zone": "highsec"})
    monkeypatch.setattr(pi_actions, "do_update_settings", lambda updates: seen.update(u=updates) or {"pi_zone": "lowsec"})
    assert client.get("/api/pi/settings").json() == {"pi_zone": "highsec"}
    assert client.put("/api/pi/settings", json={"pi_zone": "lowsec"}).json() == {"pi_zone": "lowsec"}
    assert seen["u"] == {"pi_zone": "lowsec"}
    monkeypatch.setattr(pi_actions, "do_update_settings", _boom)
    assert client.put("/api/pi/settings", json={"x": 1}).status_code == 400


@pg_helpers.postgres_required()
def test_routes_need_the_pi_grant(monkeypatch, _gate_tables, _apply_admin_schema):  # noqa: F811
    monkeypatch.setattr(pi_actions, "do_get_settings", lambda: {})
    _enable_gate(monkeypatch)
    _provision(tools=("characters", "char_info"))
    assert client.get("/api/pi/settings", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "pi", _TENANT)
    assert client.get("/api/pi/settings", cookies=_cookie()).status_code == 200
