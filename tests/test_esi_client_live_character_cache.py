"""R16 (docs/CHARACTER_MANAGEMENT_PLAN.md): live authenticated per-character
reads are cached in process memory only, keyed by (tenant_id, kind,
character_id). Two tenants can hold a token for the same character; a
character-id-only key would serve one tenant's fetch to the other."""
from __future__ import annotations

import uuid

import pytest

from eve_trader import storage
from eve_trader.esi_client import ESIClient, ESIError


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


# ------------------------------------------------- mail reads (phase 3)
def test_mail_pages_are_separate_cache_entries_per_label_and_cursor():
    esi = _Counting()
    with storage.tenant_context(str(uuid.uuid4())):
        esi.character_mail_headers(1001, "r")
        esi.character_mail_headers(1001, "r", labels=[1])
        esi.character_mail_headers(1001, "r", labels=[1], last_mail_id=500)
        esi.character_mail_headers(1001, "r", labels=[1], last_mail_id=500)     # cache hit
    assert len(esi.calls) == 3


def test_mail_headers_send_labels_and_cursor_as_query_params():
    seen = {}

    class Recording(ESIClient):
        def __init__(self):
            pass

        def _get(self, path_or_url, params=None, auth_role=None, retries=3):
            seen.update(path=path_or_url, params=params)
            return []

    with storage.tenant_context(str(uuid.uuid4())):
        Recording().character_mail_headers(1001, "r", labels=[8, 1], last_mail_id=77)
    assert seen["path"] == "/characters/1001/mail/"
    assert seen["params"] == {"datasource": "tranquility", "labels": "8,1", "last_mail_id": 77}


def test_uncached_mail_reads_bypass_and_never_fill_the_cache():
    esi = _Counting()
    with storage.tenant_context(str(uuid.uuid4())):
        esi.character_mail_headers(1001, "r", cache=False)
        esi.character_mail_headers(1001, "r", cache=False)
        assert esi.calls.__len__() == 2
        assert ESIClient._live_character_cache == {}


def test_two_tenants_never_share_a_cached_mail_body():
    esi = _Counting()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    with storage.tenant_context(a):
        esi.character_mail_body(1001, "role-a", 42)
    with storage.tenant_context(b):
        second = esi.character_mail_body(1001, "role-b", 42)
    assert len(esi.calls) == 2 and second["role"] == "role-b"


def test_invalidate_drops_only_that_characters_mail_entries():
    esi = _Counting()
    tenant = str(uuid.uuid4())
    with storage.tenant_context(tenant):
        esi.character_mail_headers(1001, "r")
        esi.character_mail_labels(1001, "r")
        esi.character_mail_headers(1002, "r")
        esi.character_location(1001, "r")
        ESIClient.invalidate_live_character_caches(tenant, 1001, prefix="mail_")
        esi.calls.clear()
        esi.character_mail_headers(1001, "r")       # dropped -> refetched
        esi.character_mail_labels(1001, "r")        # dropped -> refetched
        esi.character_mail_headers(1002, "r")       # other character: still cached
        esi.character_location(1001, "r")           # other prefix: still cached
    assert [c[0] for c in esi.calls] == ["/characters/1001/mail/", "/characters/1001/mail/labels/"]


def test_expired_entries_are_pruned_once_the_cache_grows(monkeypatch):
    esi = _Counting()
    now = [1000.0]
    monkeypatch.setattr("eve_trader.esi_client.time.time", lambda: now[0])
    with storage.tenant_context(str(uuid.uuid4())):
        for i in range(300):
            esi.character_mail_body(1001, "r", i)
        assert len(ESIClient._live_character_cache) == 300
        now[0] += ESIClient._MAIL_BODY_CACHE_TTL + 1
        esi.character_mail_body(1001, "r", 9999)       # the write that triggers pruning
        assert len(ESIClient._live_character_cache) == 1


def test_resolve_names_cached_only_asks_for_unknown_ids(monkeypatch):
    calls: list[list[int]] = []

    class Names(ESIClient):
        def __init__(self):
            pass

        def resolve_names(self, ids):
            calls.append(list(ids))
            return {i: f"N{i}" for i in ids}

    esi = Names()
    assert esi.resolve_names_cached([1, 2, 2, 0]) == {1: "N1", 2: "N2"}
    assert esi.resolve_names_cached([2, 3]) == {2: "N2", 3: "N3"}
    assert calls == [[1, 2], [3]]
    assert esi.resolve_names_cached([]) == {}


def test_esi_failure_keeps_only_the_status():
    from eve_trader.character_management.fields import esi_failure
    err = ESIError("HTTP 403 for https://esi/x/mail/1/: {\"error\":\"secret subject line\"}")
    assert esi_failure(err) == "ESI returned HTTP 403"
    assert "secret" not in esi_failure(err)
    assert esi_failure(ESIError("Request failed for https://esi/x: timeout")) == "ESI request failed (network error)"
