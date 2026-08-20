from eve_trader import esi_client, storage
from eve_trader.production import actions, esi_sync


def test_do_resolve_structure_name_captures_solar_system_id_from_corp_structures(monkeypatch):
    # GitHub issue #12/#21: solar_system_id (needed for per-category cost
    # index lookups) used to be discarded entirely - only `name` was ever
    # read out of either ESI path's response.
    monkeypatch.setattr(esi_sync, "list_producer_characters",
                         lambda: [("producer:1", 1, "Alice")])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [
        {"structure_id": 60000000001, "name": "C-J Keepstar", "solar_system_id": 30000142},
    ])
    captured = {}
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: captured.update(
                             location_id=loc_id, name=name, solar_system_id=solar_system_id))

    result = actions.do_resolve_structure_name(60000000001)

    assert result == {"location_id": 60000000001, "name": "C-J Keepstar", "cached": False}
    assert captured == {"location_id": 60000000001, "name": "C-J Keepstar", "solar_system_id": 30000142}


def test_do_resolve_structure_name_captures_solar_system_id_from_docking_history_fallback(monkeypatch):
    # Path 1 (corp structures) fails to find it - falls back to path 2
    # (ESIClient.get_structure_name, now returning the raw dict instead of
    # just a name string) - solar_system_id must still be captured there.
    monkeypatch.setattr(esi_sync, "list_producer_characters",
                         lambda: [("producer:1", 1, "Alice")])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [])
    monkeypatch.setattr(esi_client.ESIClient, "get_structure_name", lambda self, loc_id, auth_role: {
        "name": "Some Other Structure", "solar_system_id": 30000144,
    })
    captured = {}
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: captured.update(
                             location_id=loc_id, name=name, solar_system_id=solar_system_id))

    result = actions.do_resolve_structure_name(60000000002)

    assert result == {"location_id": 60000000002, "name": "Some Other Structure", "cached": False}
    assert captured["solar_system_id"] == 30000144
