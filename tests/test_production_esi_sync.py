import pytest

from eve_trader import storage
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.production import actions as production_actions
from eve_trader.production import esi_sync
from eve_trader.production.constants import ACTIVITY_REACTION


def _job(job_id, activity_id, blueprint_type_id=1, product_type_id=2, runs=1,
         installer_id=1, status="active", end_date="2026-01-01T00:00:00Z"):
    return {"job_id": job_id, "activity_id": activity_id, "blueprint_type_id": blueprint_type_id,
            "product_type_id": product_type_id, "runs": runs, "installer_id": installer_id,
            "status": status, "end_date": end_date}


def test_live_reaction_activity_id_is_normalized_to_the_sde_one():
    # Confirmed real CCP data inconsistency: the live ESI industry-jobs
    # endpoint reports Reactions as activity_id 9, but the SDE (and every
    # other place in this codebase, e.g. constants.ACTIVITY_REACTION) files
    # Reaction recipes under 11 - live jobs never actually use 11. Left
    # unnormalized, this silently broke three things downstream: the
    # Quantity column (SDE product-quantity lookup keyed by activity_id),
    # the Character Slots "used" count (ACTIVITY_SLOT_CATEGORY lookup), and
    # the Activity column showing the raw "9" instead of "Reaction".
    # production/esi_sync._industry_job_rows is the historical helper;
    # fetchers._industry_job_rows is the Phase 3a ingestion path.
    from eve_trader.esi_data import fetchers

    rows = esi_sync._industry_job_rows([_job(1, 9), _job(2, 1)], installer_names={1: "TestChar"})
    activity_ids = [r[1] for r in rows]
    assert activity_ids == [ACTIVITY_REACTION, 1]
    fetcher_rows = fetchers._industry_job_rows(
        [_job(1, 9), _job(2, 1)], installer_names={1: "TestChar"},
    )
    assert [r[1] for r in fetcher_rows] == [ACTIVITY_REACTION, 1]


def _asset(location_id):
    return {"location_id": location_id}


def test_discover_structure_names_skips_ids_below_structure_range(monkeypatch):
    # Jita 4-4 (a real NPC station id, 60003760) must never be attempted as
    # a structure resolve - no ESI call for it at all.
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role:
                         pytest.fail("must not resolve a station id as a structure"))
    monkeypatch.setattr(ESIClient, "get_structure_name", lambda self, structure_id, auth_role:
                         pytest.fail("must not resolve a station id as a structure"))

    result = esi_sync._discover_structure_names(ESIClient(), [_asset(60003760)], {})

    assert result == {"candidates": 0, "resolved": 0}


def test_discover_structure_names_skips_already_cached_ids(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_structure_names",
                         lambda ids: {i: (True, "Already Known") for i in ids})
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role:
                         pytest.fail("must not call ESI for an already-cached id"))
    monkeypatch.setattr(ESIClient, "get_structure_name", lambda self, structure_id, auth_role:
                         pytest.fail("must not call ESI for an already-cached id"))

    result = esi_sync._discover_structure_names(ESIClient(), [_asset(1049588174021)], {500: "producer:1"})

    assert result == {"candidates": 1, "resolved": 0}


def test_discover_structure_names_resolves_via_corporation_structures(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_structure_names", lambda ids: {i: (False, None) for i in ids})
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role: [
        {"structure_id": 1049588174021, "name": "C-J Keepstar", "solar_system_id": 30000142},
    ])
    monkeypatch.setattr(ESIClient, "get_structure_name", lambda self, structure_id, auth_role:
                         pytest.fail("tier 1 already resolved this id - tier 2 must not run"))
    cached = {}
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: cached.__setitem__(loc_id, (name, solar_system_id)))

    result = esi_sync._discover_structure_names(ESIClient(), [_asset(1049588174021)], {500: "producer:1"})

    assert result == {"candidates": 1, "resolved": 1}
    assert cached == {1049588174021: ("C-J Keepstar", 30000142)}


def test_discover_structure_names_falls_back_to_per_character_resolve(monkeypatch):
    # tier 1 (corporation_structures) doesn't know about this structure -
    # e.g. it belongs to a different corp than any registered character's,
    # only reachable via one character's own docking history.
    monkeypatch.setattr(storage, "get_cached_structure_names", lambda ids: {i: (False, None) for i in ids})
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "get_structure_name", lambda self, structure_id, auth_role:
                         {"name": "Someone Else's Citadel", "solar_system_id": 30002187})
    cached = {}
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: cached.__setitem__(loc_id, (name, solar_system_id)))

    result = esi_sync._discover_structure_names(ESIClient(), [_asset(1049588174021)], {500: "producer:1"})

    assert result == {"candidates": 1, "resolved": 1}
    assert cached == {1049588174021: ("Someone Else's Citadel", 30002187)}


def test_discover_structure_names_unresolvable_id_is_cached_as_failed(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_structure_names", lambda ids: {i: (False, None) for i in ids})
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role: [])

    def _fail(self, structure_id, auth_role):
        raise ESIError("no docking access")
    monkeypatch.setattr(ESIClient, "get_structure_name", _fail)
    cached = {}
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: cached.__setitem__(loc_id, (name, solar_system_id)))

    result = esi_sync._discover_structure_names(ESIClient(), [_asset(1049588174021)], {500: "producer:1"})

    assert result == {"candidates": 1, "resolved": 0}
    assert cached == {1049588174021: (None, None)}


def test_do_sync_esi_invalidates_discover_cache(monkeypatch):
    # Owned-BPO ME/TE (feeds discover_build_candidates' build-cost calc) comes
    # from character_blueprints/corp_blueprints, exactly what sync_esi just
    # refreshed - a stale cached scan must not survive a sync.
    monkeypatch.setattr(esi_sync, "sync_esi", lambda: {"characters": {}, "corporations": {}})
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda scope, ts: None)
    invalidated = []
    monkeypatch.setattr(production_actions, "invalidate_discover_cache", lambda: invalidated.append(1))

    production_actions.do_sync_esi()

    assert invalidated == [1]
