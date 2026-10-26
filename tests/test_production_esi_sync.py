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


def test_corp_roles_for_characters_dedupes_by_corporation(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {
        "corporation_id": 99 if character_id in (1, 2) else 100,
    })

    result = esi_sync.corp_roles_for_characters(
        ESIClient(), [("esi:1", 1, "Alice"), ("esi:2", 2, "Bob"), ("esi:3", 3, "Carol")],
    )

    assert result == {99: "esi:1", 100: "esi:3"}  # first character per corp wins


def test_corp_roles_for_characters_skips_characters_whose_lookup_fails(monkeypatch):
    def _lookup(self, character_id):
        if character_id == 1:
            raise ESIError("down")
        return {"corporation_id": 100}
    monkeypatch.setattr(ESIClient, "character_public_info", _lookup)

    result = esi_sync.corp_roles_for_characters(ESIClient(), [("esi:1", 1, "Alice"), ("esi:2", 2, "Bob")])

    assert result == {100: "esi:2"}


def test_resolve_structure_ids_tries_corp_structures_before_per_character(monkeypatch):
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role: [
        {"structure_id": 1049588174021, "name": "C-J Keepstar", "solar_system_id": 30000142},
    ])
    monkeypatch.setattr(ESIClient, "get_structure_name", lambda self, structure_id, auth_role:
                         pytest.fail("tier 1 already resolved this id - tier 2 must not run"))

    result = esi_sync.resolve_structure_ids(ESIClient(), {1049588174021}, {500: "producer:1"}, lambda: [("esi:1", 1, "Alice")])

    assert result == {1049588174021: ("C-J Keepstar", 30000142)}


def test_resolve_structure_ids_never_calls_get_characters_when_tier_1_resolves_everything(monkeypatch):
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role: [
        {"structure_id": 1049588174021, "name": "C-J Keepstar", "solar_system_id": 30000142},
    ])

    def _fail_if_called():
        pytest.fail("get_characters must not be called when tier 1 already resolved everything")

    esi_sync.resolve_structure_ids(ESIClient(), {1049588174021}, {500: "producer:1"}, _fail_if_called)


def test_resolve_structure_ids_leaves_unresolved_ids_out_of_the_result(monkeypatch):
    monkeypatch.setattr(ESIClient, "corporation_structures", lambda self, corporation_id, auth_role: [])

    def _fail(self, structure_id, auth_role):
        raise ESIError("no docking access")
    monkeypatch.setattr(ESIClient, "get_structure_name", _fail)

    result = esi_sync.resolve_structure_ids(ESIClient(), {1049588174021}, {500: "producer:1"}, lambda: [("esi:1", 1, "Alice")])

    assert result == {}


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
