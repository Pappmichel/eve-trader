"""Asset Manager routes: query parameters reach the do_* kwargs, errors map
to HTTP codes, exports carry the right content type. Actions are
monkeypatched on the module object (CLAUDE.md testing conventions)."""
from fastapi.testclient import TestClient

from eve_trader.actions import ActionError, ConflictError
from eve_trader.api.app import _required_tool_for_path, create_app
from eve_trader.asset_manager import actions

client = TestClient(create_app())


def test_items_passes_repeated_query_params(monkeypatch):
    captured = {}

    def _capture(**params):
        captured.update(params)
        return {"rows": [], "total_rows": 0}
    monkeypatch.setattr(actions, "do_list_items", _capture)

    resp = client.get("/api/asset-manager/items", params=[
        ("q", "trit"), ("owners", "character:1"), ("owners", "corporation:2"),
        ("type_ids", "34"), ("sources", "esi"), ("stock_only", "true"), ("page", "2"),
    ])

    assert resp.status_code == 200
    assert captured["q"] == "trit"
    assert captured["owners"] == ["character:1", "corporation:2"]
    assert captured["type_ids"] == [34]
    assert captured["stock_only"] is True
    assert captured["page"] == 2
    assert captured["group_by"] == "type_location"


def test_items_action_error_is_400(monkeypatch):
    def _raise(**_params):
        raise ActionError("sort must be one of value")
    monkeypatch.setattr(actions, "do_list_items", _raise)

    resp = client.get("/api/asset-manager/items", params={"sort": "nope"})

    assert resp.status_code == 400
    assert "sort" in resp.json()["detail"]


def test_sync_conflict_is_409(monkeypatch):
    def _raise():
        raise ConflictError("Sync assets is already running.")
    monkeypatch.setattr(actions, "do_start_sync", _raise)

    assert client.post("/api/asset-manager/sync").status_code == 409


def test_csv_export_is_an_attachment(monkeypatch):
    monkeypatch.setattr(actions, "do_export_csv", lambda **_p: "Type,Quantity\nTritanium,5\n")

    resp = client.get("/api/asset-manager/export.csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]


def test_routes_require_the_asset_manager_grant():
    assert _required_tool_for_path("/api/asset-manager/items", "GET") == "asset_manager"
    assert _required_tool_for_path("/api/asset-manager/sync", "POST") == "asset_manager"
