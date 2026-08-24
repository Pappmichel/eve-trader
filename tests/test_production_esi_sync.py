import pytest

from eve_trader import storage
from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.production import actions as production_actions
from eve_trader.production import esi_sync
from eve_trader.production.constants import ACTIVITY_REACTION


def _sell_order(order_id, type_id, volume_remain, location_id, region_id=1000):
    return {"order_id": order_id, "type_id": type_id, "volume_remain": volume_remain,
            "location_id": location_id, "region_id": region_id, "is_buy_order": False}


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
    rows = esi_sync._industry_job_rows([_job(1, 9), _job(2, 1)], installer_names={1: "TestChar"})
    activity_ids = [r[1] for r in rows]
    assert activity_ids == [ACTIVITY_REACTION, 1]


def test_sync_esi_merges_personal_and_corp_sell_orders(monkeypatch):
    # Confirmed real bug: corp-hangar stock is often listed via a corp sell
    # order, not a personal one - sync_esi must persist both, not just
    # personal orders, so Marktstatus's "listed" counts aren't wrong for
    # anything sold through the corp.
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda tm=None: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, cfg=None: setattr(self, "cfg", cfg))
    monkeypatch.setattr(TokenManager, "get_token", lambda self, role: TokenRecord(
        role=role, character_id=1, character_name="TestChar", access_token="x",
        refresh_token="y", expires_at=0.0, scopes="",
    ))

    monkeypatch.setattr(ESIClient, "character_assets", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_industry_jobs", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_blueprints", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_skills", lambda self, character_id, auth_role: {"skills": []})
    monkeypatch.setattr(ESIClient, "character_orders",
                         lambda self, character_id, auth_role: [_sell_order(1, 100, 10, 55)])
    monkeypatch.setattr(ESIClient, "character_public_info",
                         lambda self, character_id: {"corporation_id": 500})
    monkeypatch.setattr(ESIClient, "corporation_public_info",
                         lambda self, corporation_id: {"name": "Test Corp"})
    monkeypatch.setattr(ESIClient, "corporation_assets", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_industry_jobs", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_blueprints", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_orders",
                         lambda self, corporation_id, auth_role: [_sell_order(2, 200, 20, 55)])
    monkeypatch.setattr(ESIClient, "resolve_names", lambda self, ids: {})

    saved_orders = []
    monkeypatch.setattr(storage, "replace_sell_orders", lambda rows: saved_orders.extend(rows))
    monkeypatch.setattr(storage, "replace_assets", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_industry_jobs", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_blueprints", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_character_slots", lambda rows: None)

    result = esi_sync.sync_esi()

    saved_type_ids = {row[1] for row in saved_orders}
    assert saved_type_ids == {100, 200}
    assert result["corporations"]["Test Corp"]["corp_sell_orders"] == 1


def test_sync_esi_corp_orders_role_failure_does_not_block_personal_orders(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda tm=None: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, cfg=None: setattr(self, "cfg", cfg))
    monkeypatch.setattr(TokenManager, "get_token", lambda self, role: TokenRecord(
        role=role, character_id=1, character_name="TestChar", access_token="x",
        refresh_token="y", expires_at=0.0, scopes="",
    ))

    monkeypatch.setattr(ESIClient, "character_assets", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_industry_jobs", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_blueprints", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_skills", lambda self, character_id, auth_role: {"skills": []})
    monkeypatch.setattr(ESIClient, "character_orders",
                         lambda self, character_id, auth_role: [_sell_order(1, 100, 10, 55)])
    monkeypatch.setattr(ESIClient, "character_public_info",
                         lambda self, character_id: {"corporation_id": 500})
    monkeypatch.setattr(ESIClient, "corporation_public_info",
                         lambda self, corporation_id: {"name": "Test Corp"})
    monkeypatch.setattr(ESIClient, "corporation_assets", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_industry_jobs", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_blueprints", lambda self, corporation_id, auth_role: [])

    def fail_corp_orders(self, corporation_id, auth_role):
        raise ESIError("missing Accountant/Trader role")
    monkeypatch.setattr(ESIClient, "corporation_orders", fail_corp_orders)
    monkeypatch.setattr(ESIClient, "resolve_names", lambda self, ids: {})

    saved_orders = []
    monkeypatch.setattr(storage, "replace_sell_orders", lambda rows: saved_orders.extend(rows))
    monkeypatch.setattr(storage, "replace_assets", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_industry_jobs", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_blueprints", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_character_slots", lambda rows: None)

    result = esi_sync.sync_esi()

    saved_type_ids = {row[1] for row in saved_orders}
    assert saved_type_ids == {100}
    assert "corp_sell_orders" not in result["corporations"].get("Test Corp", {})


def test_sync_esi_character_public_info_failure_does_not_discard_the_whole_run(monkeypatch):
    # Real bug confirmed live (2026-08-16): character_public_info/
    # corporation_public_info (used only to attribute corp-level data to a
    # corp name) used to be the one unguarded ESI call in this whole loop -
    # every other call here (assets/jobs/blueprints/skills/orders, corp
    # assets/jobs/blueprints, corp orders) is individually wrapped in
    # try/except ESIError. A transient failure on this one call propagated
    # out of sync_esi() entirely, *before* storage.replace_assets/... ever
    # ran - discarding every already-successfully-fetched character's data
    # for the whole run, not just this one character's corp enrichment.
    # Two characters: the first's public-info lookup fails, the second's
    # succeeds - both characters' own (already-fetched) assets must still
    # reach storage.replace_assets.
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda tm=None: [
        ("producer:1", 1, "TestChar"), ("producer:2", 2, "TestChar2"),
    ])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, cfg=None: setattr(self, "cfg", cfg))
    monkeypatch.setattr(TokenManager, "get_token", lambda self, role: TokenRecord(
        role=role, character_id=int(role.split(":")[1]), character_name=role, access_token="x",
        refresh_token="y", expires_at=0.0, scopes="",
    ))

    def fake_assets(self, character_id, auth_role):
        return [{"item_id": character_id, "type_id": 100 + character_id, "location_id": 1,
                  "location_flag": "Hangar", "quantity": 1, "is_blueprint_copy": False}]
    monkeypatch.setattr(ESIClient, "character_assets", fake_assets)
    monkeypatch.setattr(ESIClient, "character_industry_jobs", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_blueprints", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_skills", lambda self, character_id, auth_role: {"skills": []})
    monkeypatch.setattr(ESIClient, "character_orders", lambda self, character_id, auth_role: [])

    def fake_public_info(self, character_id):
        if character_id == 1:
            raise ESIError("transient hiccup")
        return {"corporation_id": 500}
    monkeypatch.setattr(ESIClient, "character_public_info", fake_public_info)
    monkeypatch.setattr(ESIClient, "corporation_public_info", lambda self, corporation_id: {"name": "Test Corp"})
    monkeypatch.setattr(ESIClient, "corporation_assets", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_industry_jobs", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_blueprints", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_orders", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "resolve_names", lambda self, ids: {})

    saved_char_assets = []
    monkeypatch.setattr(storage, "replace_assets",
                         lambda table, rows: saved_char_assets.extend(rows) if table == "character_assets" else None)
    monkeypatch.setattr(storage, "replace_industry_jobs", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_blueprints", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_sell_orders", lambda rows: None)
    monkeypatch.setattr(storage, "replace_character_slots", lambda rows: None)

    result = esi_sync.sync_esi()

    saved_type_ids = {row[1] for row in saved_char_assets}
    assert saved_type_ids == {101, 102}  # both characters' assets survived, not just the second's
    assert "skipped (public info fetch failed" in result["characters"]["TestChar"]["corp"]


def test_sync_esi_fetches_characters_own_data_concurrently(monkeypatch):
    # Real perf gap confirmed via cProfile (2026-08-16): each character's own
    # assets/jobs/blueprints/skills/orders/public-info fetch used to run
    # strictly one character at a time even though none of it depends on any
    # other character - a textbook case for parallelizing, and (with 18
    # registered characters on the real account this was profiled against)
    # the dominant cost of a full sync. Four characters, each with an
    # artificial per-request delay on character_assets - must overlap
    # (finish in roughly one delay's worth of wall time, not four serialized
    # ones), same style of proof used for goonmetrics_client.py's per-market
    # lock fix.
    import time as time_module

    DELAY = 0.2
    chars = [(f"producer:{i}", i, f"char{i}") for i in range(1, 5)]
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda tm=None: chars)
    monkeypatch.setattr(TokenManager, "__init__", lambda self, cfg=None: setattr(self, "cfg", cfg))
    monkeypatch.setattr(TokenManager, "get_token", lambda self, role: TokenRecord(
        role=role, character_id=1, character_name=role, access_token="x",
        refresh_token="y", expires_at=0.0, scopes="",
    ))

    def slow_assets(self, character_id, auth_role):
        time_module.sleep(DELAY)
        return []
    monkeypatch.setattr(ESIClient, "character_assets", slow_assets)
    monkeypatch.setattr(ESIClient, "character_industry_jobs", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_blueprints", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_skills", lambda self, character_id, auth_role: {"skills": []})
    monkeypatch.setattr(ESIClient, "character_orders", lambda self, character_id, auth_role: [])

    def fail_public_info(self, character_id):
        raise ESIError("no corp for this test")
    monkeypatch.setattr(ESIClient, "character_public_info", fail_public_info)
    monkeypatch.setattr(ESIClient, "resolve_names", lambda self, ids: {})

    monkeypatch.setattr(storage, "replace_assets", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_industry_jobs", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_blueprints", lambda table, rows: None)
    monkeypatch.setattr(storage, "replace_sell_orders", lambda rows: None)
    monkeypatch.setattr(storage, "replace_character_slots", lambda rows: None)

    start = time_module.monotonic()
    result = esi_sync.sync_esi()
    elapsed = time_module.monotonic() - start

    assert len(result["characters"]) == 4
    assert elapsed < DELAY * 4  # ran concurrently, not serialized one-at-a-time


# --------------------------------------------- structure-name discovery
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
