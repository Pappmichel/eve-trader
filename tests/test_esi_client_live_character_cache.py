"""R16 (docs/CHARACTER_MANAGEMENT_PLAN.md): live authenticated per-character
reads are cached in process memory only, keyed by (tenant_id, kind,
character_id). Two tenants can hold a token for the same character; a
character-id-only key would serve one tenant's fetch to the other."""
from __future__ import annotations

import uuid

import pytest

from eve_trader import storage
from eve_trader.esi_client import ESIClient


class _Counting(ESIClient):
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def _get(self, path_or_url, params=None, auth_role=None, retries=3):
        self.calls.append((path_or_url, auth_role))
        return {"solar_system_id": 30000142, "role": auth_role}


def test_second_read_in_same_tenant_is_served_from_cache():
    esi = _Counting()
    with storage.tenant_context(str(uuid.uuid4())):
        first = esi.character_location(1001, "esi:1001")
        second = esi.character_location(1001, "esi:1001")
    assert first == second
    assert len(esi.calls) == 1


def test_two_tenants_with_the_same_character_never_share_a_cache_entry():
    esi = _Counting()
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    with storage.tenant_context(tenant_a):
        a = esi.character_location(1001, "role-of-a")
    with storage.tenant_context(tenant_b):
        b = esi.character_location(1001, "role-of-b")
    # B must have made its own call with its own token, not reused A's row.
    assert len(esi.calls) == 2
    assert a["role"] == "role-of-a" and b["role"] == "role-of-b"


def test_location_ship_and_online_are_separate_cache_keys():
    esi = _Counting()
    with storage.tenant_context(str(uuid.uuid4())):
        esi.character_location(1001, "r")
        esi.character_ship(1001, "r")
        esi.character_online(1001, "r")
    assert [c[0] for c in esi.calls] == [
        "/characters/1001/location/", "/characters/1001/ship/", "/characters/1001/online/",
    ]


def test_missing_tenant_fails_closed():
    esi = _Counting()
    with pytest.raises(RuntimeError, match="tenant"):
        esi.character_location(1001, "r")
    assert esi.calls == []


def test_expired_entry_is_refetched(monkeypatch):
    esi = _Counting()
    now = [1000.0]
    monkeypatch.setattr("eve_trader.esi_client.time.time", lambda: now[0])
    with storage.tenant_context(str(uuid.uuid4())):
        esi.character_online(1001, "r")
        now[0] += ESIClient._LIVE_CHARACTER_CACHE_TTL + 1
        esi.character_online(1001, "r")
    assert len(esi.calls) == 2
