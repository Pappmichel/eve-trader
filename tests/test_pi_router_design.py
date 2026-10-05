"""PI design routes (/api/pi/design/*): marshaling and ActionError -> 400.
Actions are monkeypatched on the module object (CLAUDE.md testing
conventions)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from eve_trader.actions import ActionError
from eve_trader.api.app import _required_tool_for_path, create_app
from eve_trader.pi import design_actions

client = TestClient(create_app())


def _boom(*_a, **_k):
    raise ActionError("nope")


def test_design_routes_are_gated_on_pi():
    assert _required_tool_for_path("/api/pi/design/ways") == "pi"


def _capture(monkeypatch, name):
    seen = {}
    monkeypatch.setattr(design_actions, name, lambda **kw: seen.update(kw) or {"ok": True})
    return seen


def test_ways(monkeypatch):
    seen = _capture(monkeypatch, "do_ways_to_build")
    r = client.post("/api/pi/design/ways", json={"product_type_id": 2869, "planet_type_id": 2016, "radius_km": 5000})
    assert r.status_code == 200 and seen["product_type_id"] == 2869 and seen["planet_type_id"] == 2016


def test_mixed_p2(monkeypatch):
    seen = _capture(monkeypatch, "do_mixed_p2")
    r = client.post("/api/pi/design/mixed-p2", json={"assignments": {"9832": 12, "3689": 12}, "planet_type_id": 2016})
    assert r.status_code == 200 and seen["assignments"] == {9832: 12, 3689: 12} and seen["launchpads"] == 1


def test_storage_and_grow(monkeypatch):
    seen = _capture(monkeypatch, "do_storage_suggestion")
    r = client.post("/api/pi/design/storage-suggestion",
                    json={"chain": "P1-P2", "product_type_id": 9832, "planet_type_id": 2016, "interval_hours": 48})
    assert r.status_code == 200 and seen["interval_hours"] == 48
    seen = _capture(monkeypatch, "do_grow_to_supply")
    r = client.post("/api/pi/design/grow", json={"chain": "P0-P1", "product_type_id": 2393, "planet_type_id": 2016,
                                                 "yield_per_head": 6000})
    assert r.status_code == 200 and seen["yield_per_head"] == 6000


def test_edit(monkeypatch):
    seen = _capture(monkeypatch, "do_edit_layout")
    r = client.post("/api/pi/design/edit", json={"template": {"P": [], "Pln": 2016}, "edit": {"op": "move", "pin": 1}})
    assert r.status_code == 200 and seen["edit"] == {"op": "move", "pin": 1}


def test_limits_and_errors(monkeypatch):
    r = client.post("/api/pi/design/ways", json={"product_type_id": 1, "cc_level": 9})
    assert r.status_code == 422
    monkeypatch.setattr(design_actions, "do_edit_layout", _boom)
    r = client.post("/api/pi/design/edit", json={"template": "x", "edit": {"op": "move"}})
    assert r.status_code == 400 and r.json()["detail"] == "nope"
