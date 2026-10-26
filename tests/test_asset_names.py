"""Container/ship names and corp division names (docs/ASSET_MANAGER_PLAN.md
phase 4, D6/D7, R-14)."""
from __future__ import annotations

import pytest

from eve_trader import esi_client, storage
from eve_trader.esi_data import fetchers


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _asset(item_id, type_id, location_id, singleton):
    return {"item_id": item_id, "type_id": type_id, "location_id": location_id,
            "location_flag": "Hangar", "quantity": 1, "is_singleton": singleton}


SHIP, BOX, LOOSE, ORE = 1, 2, 3, 4


@pytest.fixture
def written(monkeypatch):
    out = {}
    monkeypatch.setattr(storage, "_ship_type_ids", lambda ids: {t for t in ids if t == SHIP})
    monkeypatch.setattr(storage, "load_asset_item_names", lambda table, owner: {10: "Old ship"})
    monkeypatch.setattr(
        storage, "replace_assets",
        lambda table, rows, **kw: out.update(table=table, rows=rows, kw=kw),
    )
    return out


class _Client:
    def __init__(self, assets, names=None, error=None):
        self.assets, self.names, self.error, self.asked = assets, names or [], error, []

    def character_assets(self, owner_id, auth_role):
        return self.assets

    corporation_assets = character_assets

    def character_asset_names(self, owner_id, ids, auth_role):
        self.asked.append(list(ids))
        if self.error:
            raise self.error
        return self.names

    corporation_asset_names = character_asset_names


def test_only_singleton_ships_and_containers_are_asked_for_names(written):
    assets = [
        _asset(10, SHIP, 100, True),
        _asset(11, BOX, 100, True),     # singleton holding item 12 -> container
        _asset(12, ORE, 11, False),
        _asset(13, LOOSE, 100, True),   # singleton, empty, not a ship -> nothing to name
        _asset(14, ORE, 100, False),
    ]
    client = _Client(assets, names=[{"item_id": 10, "name": "Rifter 1"}, {"item_id": 11, "name": "Box"}])

    fetchers.fetch_character_assets(client, 7, "role", "Alice")

    assert client.asked == [[10, 11]]
    names = {row[0]: row[8] for row in written["rows"]}
    assert names == {10: "Rifter 1", 11: "Box", 12: None, 13: None, 14: None}


def test_none_and_empty_names_are_stored_as_no_name(written):
    assets = [_asset(10, SHIP, 100, True), _asset(11, BOX, 100, True), _asset(12, ORE, 11, False)]
    client = _Client(assets, names=[{"item_id": 10, "name": "None"}, {"item_id": 11, "name": "  "}])

    fetchers.fetch_corporation_assets(client, 9, "role", "Corp")

    assert [row[8] for row in written["rows"]] == [None, None, None]
    assert written["table"] == "corp_assets"


def test_name_fetch_failure_keeps_the_sync_green_and_previous_names(written):
    assets = [_asset(10, SHIP, 100, True), _asset(11, BOX, 100, True), _asset(12, ORE, 11, False)]
    client = _Client(assets, error=esi_client.ESIError("boom"))

    result = fetchers.fetch_character_assets(client, 7, "role", "Alice")

    assert result["written"] == 3
    assert {row[0]: row[8] for row in written["rows"]} == {10: "Old ship", 11: None, 12: None}


def test_no_nameable_asset_makes_no_names_call(written):
    client = _Client([_asset(14, ORE, 100, False)])

    fetchers.fetch_character_assets(client, 7, "role", "Alice")

    assert client.asked == []


def test_client_chunks_names_by_1000_and_skips_a_404_chunk(monkeypatch):
    client = esi_client.ESIClient.__new__(esi_client.ESIClient)
    calls = []

    def write(method, path, *, auth_role, json_body, idempotent, expect):
        calls.append((method, path, len(json_body), idempotent))
        if len(calls) == 2:
            raise esi_client.ESIHTTPError(404, "", "HTTP 404")
        return _Resp([{"item_id": json_body[0], "name": "n"}])

    monkeypatch.setattr(client, "_write", write, raising=False)

    out = client.character_asset_names(5, list(range(2500)), "role")

    assert [c[2] for c in calls] == [1000, 1000, 500]
    assert all(c[0] == "POST" and c[1] == "/characters/5/assets/names/" and c[3] for c in calls)
    assert [n["item_id"] for n in out] == [0, 2000]


def test_client_other_http_errors_propagate(monkeypatch):
    client = esi_client.ESIClient.__new__(esi_client.ESIClient)

    def write(*a, **k):
        raise esi_client.ESIHTTPError(403, "", "HTTP 403")

    monkeypatch.setattr(client, "_write", write, raising=False)

    with pytest.raises(esi_client.ESIHTTPError):
        client.corporation_asset_names(5, [1], "role")


def test_divisions_fetcher_stores_hangar_and_wallet_names(monkeypatch):
    stored = {}
    monkeypatch.setattr(storage, "replace_corp_divisions", lambda corp, rows: stored.update(corp=corp, rows=rows))

    class Client:
        def corporation_divisions(self, corp, auth_role):
            return {"hangar": [{"division": 1, "name": " Ore "}, {"division": 2}, {"division": 9, "name": "x"}],
                    "wallet": [{"division": 1, "name": "Main"}]}

    result = fetchers.fetch_corporation_divisions(Client(), 55, "role", "Corp")

    assert result == {"written": 3}
    assert stored == {"corp": 55, "rows": [("hangar", 1, "Ore"), ("hangar", 2, None), ("wallet", 1, "Main")]}


def test_divisions_fetcher_is_registered_for_corporations_only():
    assert fetchers.fetcher_for("corp_divisions", "corporation") is fetchers.fetch_corporation_divisions
    with pytest.raises(KeyError):
        fetchers.fetcher_for("corp_divisions", "character")


def test_ids_esi_did_not_answer_keep_their_stored_name(written):
    assets = [_asset(10, SHIP, 100, True), _asset(11, BOX, 100, True), _asset(12, ORE, 11, False)]
    # A 404 chunk comes back empty: id 10 has a stored name, 11 was answered "None".
    client = _Client(assets, names=[{"item_id": 11, "name": "None"}])

    fetchers.fetch_character_assets(client, 7, "role", "Alice")

    assert {row[0]: row[8] for row in written["rows"]} == {10: "Old ship", 11: None, 12: None}
