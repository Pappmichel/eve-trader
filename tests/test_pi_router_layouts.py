"""PI router: layout/template/system-analysis/characters/production-demand
routes. Actions are monkeypatched on the module object."""
from __future__ import annotations

from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.pi import actions as pi_actions

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
)
from .test_char_info import _TENANT, _cookie, _enable_gate, _gate_tables, _provision  # noqa: F401

client = TestClient(create_app())


def _boom(*_a, **_k):
    raise ActionError("nope")


def test_characters(monkeypatch):
    monkeypatch.setattr(pi_actions, "do_characters", lambda: {"total_slots": 6})
    assert client.get("/api/pi/characters").json() == {"total_slots": 6}
    monkeypatch.setattr(pi_actions, "do_characters", _boom)
    r = client.get("/api/pi/characters")
    assert r.status_code == 400 and r.json()["detail"] == "nope"


def test_system_analysis(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_system_analysis", lambda **kw: seen.update(kw) or {"plan": {}})
    assert client.post("/api/pi/systems/30000142/analysis", json={}).status_code == 200
    assert seen == {"solar_system_id": 30000142, "slots": None, "characters": None, "cc_level": None,
                    "owner_tax_rate": None}
    client.post("/api/pi/systems/5/analysis", json={"slots": 4, "characters": 2, "cc_level": 5, "owner_tax_rate": 0.1})
    assert seen["slots"] == 4 and seen["characters"] == 2 and seen["cc_level"] == 5 and seen["owner_tax_rate"] == 0.1
    assert client.post("/api/pi/systems/5/analysis", json={"slots": 0}).status_code == 422
    assert client.post("/api/pi/systems/5/analysis", json={"cc_level": 6}).status_code == 422
    monkeypatch.setattr(pi_actions, "do_system_analysis", _boom)
    assert client.post("/api/pi/systems/5/analysis", json={}).status_code == 400


def test_validate_layout(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_validate_layout", lambda **kw: seen.update(kw) or {"analysis": {"ok": True}})
    r = client.post("/api/pi/layouts/validate", json={"template": "{}", "radius_km": 3000})
    assert r.status_code == 200 and r.json() == {"analysis": {"ok": True}}
    assert seen == {"template": "{}", "planet_id": None, "radius_km": 3000, "yield_per_head": None}
    assert client.post("/api/pi/layouts/validate", json={"template": {}, "radius_km": -1}).status_code == 422
    assert client.post("/api/pi/layouts/validate", json={}).status_code == 422
    monkeypatch.setattr(pi_actions, "do_validate_layout", _boom)
    assert client.post("/api/pi/layouts/validate", json={"template": "x"}).status_code == 400


def test_generate_layout(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_generate_layout", lambda **kw: seen.update(kw) or {"template_json": "{}"})
    r = client.post("/api/pi/layouts/generate", json={
        "chain": "P1-P2", "product_type_id": 3645, "planet_type_id": 2016, "radius_km": 5000,
        "shape": "star", "comment": "hi", "owner_tax_rate": 0.1, "freight_per_m3": 5})
    assert r.status_code == 200
    assert seen["shape"] == "star" and seen["comment"] == "hi" and seen["chain"] == "P1-P2"
    assert "owner_tax_rate" not in seen and "freight_per_m3" not in seen
    assert client.post("/api/pi/layouts/generate", json={"chain": "x", "product_type_id": 1,
                                                         "comment": "x" * 201}).status_code == 422
    monkeypatch.setattr(pi_actions, "do_generate_layout", _boom)
    assert client.post("/api/pi/layouts/generate", json={"chain": "x", "product_type_id": 1}).status_code == 400


def test_retarget_layout(monkeypatch):
    seen = {}
    monkeypatch.setattr(pi_actions, "do_retarget_template", lambda **kw: seen.update(kw) or {"ok": 1})
    r = client.post("/api/pi/layouts/retarget", json={"template": "{}", "planet_type_id": 13})
    assert r.status_code == 200
    assert seen["planet_type_id"] == 13 and seen["product_type_id"] is None
    monkeypatch.setattr(pi_actions, "do_retarget_template", _boom)
    assert client.post("/api/pi/layouts/retarget", json={"template": "{}"}).status_code == 400


def test_templates_crud_and_export(monkeypatch):
    calls = []
    monkeypatch.setattr(pi_actions, "do_list_templates", lambda: [{"template_id": 1}])
    monkeypatch.setattr(pi_actions, "do_get_template",
                        lambda **kw: calls.append(("get", kw)) or {"template_id": kw["template_id"]})
    monkeypatch.setattr(pi_actions, "do_save_template",
                        lambda **kw: calls.append(("save", kw)) or {"template_id": kw.get("template_id") or 9})
    monkeypatch.setattr(pi_actions, "do_delete_template", lambda template_id: {"deleted": template_id})
    monkeypatch.setattr(pi_actions, "do_export_template",
                        lambda template_id, pretty: {"name": "n", "json": f"{template_id}:{pretty}"})
    assert client.get("/api/pi/templates").json() == [{"template_id": 1}]
    assert client.get("/api/pi/templates/4?planet_id=7&radius_km=3000").json() == {"template_id": 4}
    assert calls[-1] == ("get", {"template_id": 4, "planet_id": 7, "radius_km": 3000.0})
    assert client.post("/api/pi/templates", json={"template": "{}", "name": "a"}).json() == {"template_id": 9}
    assert calls[-1] == ("save", {"template": "{}", "name": "a", "source": "paste"})
    assert client.put("/api/pi/templates/3", json={"template": "{}", "source": "generated"}).json() == {"template_id": 3}
    assert calls[-1][1]["template_id"] == 3 and calls[-1][1]["source"] == "generated"
    assert client.post("/api/pi/templates", json={"template": "{}", "name": "x" * 101}).status_code == 422
    assert client.delete("/api/pi/templates/3").json() == {"deleted": 3}
    assert client.get("/api/pi/templates/3/export?pretty=true").json() == {"name": "n", "json": "3:True"}
    assert client.get("/api/pi/templates/3/export").json()["json"] == "3:False"
    for attr, call in (
        ("do_list_templates", lambda: client.get("/api/pi/templates")),
        ("do_get_template", lambda: client.get("/api/pi/templates/1")),
        ("do_save_template", lambda: client.post("/api/pi/templates", json={"template": "{}"})),
        ("do_delete_template", lambda: client.delete("/api/pi/templates/1")),
        ("do_export_template", lambda: client.get("/api/pi/templates/1/export")),
    ):
        monkeypatch.setattr(pi_actions, attr, _boom)
        r = call()
        assert r.status_code == 400 and r.json()["detail"] == "nope", attr
    monkeypatch.setattr(pi_actions, "do_save_template", _boom)
    assert client.put("/api/pi/templates/1", json={"template": "{}"}).status_code == 400


def test_production_demand_gate_off_is_allowed(monkeypatch):
    monkeypatch.setattr(pi_actions, "do_production_demand", lambda: {"rows": []})
    r = client.get("/api/pi/production-demand")
    assert r.status_code == 200 and r.json() == {"rows": []}
    monkeypatch.setattr(pi_actions, "do_production_demand", _boom)
    assert client.get("/api/pi/production-demand").status_code == 400


@pg_helpers.postgres_required()
def test_production_demand_needs_production_grant(monkeypatch, _gate_tables, _apply_admin_schema):  # noqa: F811
    monkeypatch.setattr(pi_actions, "do_production_demand", lambda: {"rows": []})
    _enable_gate(monkeypatch)
    _provision(tools=("characters", "pi"))
    assert client.get("/api/pi/production-demand", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "production", _TENANT)
    assert client.get("/api/pi/production-demand", cookies=_cookie()).status_code == 200
