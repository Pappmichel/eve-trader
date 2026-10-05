"""PI router: colonies / calibration routes. Actions monkeypatched on the module object."""
from __future__ import annotations

from fastapi.testclient import TestClient

from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.pi import actions as pi_actions

client = TestClient(create_app())


def test_colonies_and_calibration(monkeypatch):
    monkeypatch.setattr(pi_actions, "do_colonies", lambda: {"characters": [], "shared": False})
    monkeypatch.setattr(pi_actions, "do_calibration", lambda: {"zones": []})
    monkeypatch.setattr(pi_actions, "do_sync_colonies", lambda: {"ok": True})
    assert client.get("/api/pi/colonies").json() == {"characters": [], "shared": False}
    assert client.get("/api/pi/calibration").json() == {"zones": []}
    assert client.post("/api/pi/colonies/sync").json() == {"ok": True}


def test_colony_template_passes_body_and_maps_errors(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_colony_template", lambda **kw: seen.update(kw) or {"ok": 1})
    assert client.post("/api/pi/colonies/7/40000001/template", json={}).status_code == 200
    assert seen == {"character_id": 7, "planet_id": 40000001, "save": False, "name": None}
    client.post("/api/pi/colonies/7/40000001/template", json={"save": True, "name": "X"})
    assert seen["save"] is True and seen["name"] == "X"

    def boom(**_k):
        raise ActionError("nope")
    monkeypatch.setattr(pi_actions, "do_colony_template", boom)
    r = client.post("/api/pi/colonies/7/1/template", json={})
    assert r.status_code == 400 and r.json()["detail"] == "nope"
