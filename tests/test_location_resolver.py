"""Central location registry, reader and resolver
(docs/LOCATION_RESOLUTION_PLAN.md phases 2-3), against a real database and a
fake ESI client."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.esi_client import ESIError
from eve_trader.locations import reader, resolver, store

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CORP = 94555
# Ids no other test seeds (SDE tables are shared by every test on a worker).
STATION, SYSTEM, REGION = 60099555, 30099555, 10099555
OFFICE, STRUCT, STRUCT2, STRUCT3 = 1050055429001, 1049865184001, 1049865184002, 1049865184003
BOX = 1049865184900
CHARS = [("producer:1", 1, "Alpha"), ("buyer:2", 2, "Beta")]
OFFICE_TYPE = storage.OFFICE_TYPE_ID


@pytest.fixture(autouse=True)
def _clean(_apply_esi_access_schema):
    pg_helpers.wipe_tables("global_structure_names", "sde_stations")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO sde_regions VALUES (%s,%s) ON CONFLICT DO NOTHING", (REGION, "Test Region"))
        conn.execute("INSERT INTO sde_solar_systems VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (SYSTEM, "Testsys", 0.5, REGION))
        conn.execute("INSERT INTO sde_stations VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                     (STATION, SYSTEM, "Test Station"))


class FakeESI:
    def __init__(self, names=None, errors=None, corp_lists=None):
        self.names = names or {}            # structure id -> (name, system id)
        self.errors = errors or {}          # (structure id, role) -> "403"/"404"/"502"/"net"
        self.corp_lists = corp_lists or {}  # corporation id -> [{"structure_id", "name", "solar_system_id"}]
        self.calls: list[tuple[int, str]] = []

    def get_structure_name(self, structure_id, auth_role):
        self.calls.append((structure_id, auth_role))
        code = self.errors.get((structure_id, auth_role))
        if code == "net":
            raise ESIError("Request failed for https://esi: timeout")
        if code:
            raise ESIError(f"HTTP {code} for https://esi/universe/structures/{structure_id}/: x")
        if structure_id in self.names:
            name, system = self.names[structure_id]
            return {"name": name, "solar_system_id": system}
        raise ESIError(f"HTTP 403 for https://esi/universe/structures/{structure_id}/: x")

    def corporation_structures(self, corporation_id, auth_role):
        self.calls.append((-corporation_id, auth_role))
        if corporation_id not in self.corp_lists:
            raise ESIError("HTTP 403 for https://esi/corporations/: x")
        return self.corp_lists[corporation_id]


def _seed(*, with_box=False):
    rows = [
        (OFFICE, OFFICE_TYPE, STATION, "OfficeFolder", 1, 0, "Corp (corp)", True),
        (7, 34, OFFICE, "CorpSAG1", 5, 0, "Corp (corp)"),
        (8, 34, STRUCT, "Hangar", 5, 0, "Corp (corp)"),
        (9, 34, STRUCT2, "Hangar", 5, 0, "Corp (corp)"),
        (10, 34, STRUCT3, "Hangar", 5, 0, "Corp (corp)"),
    ]
    if with_box:
        rows += [(BOX, 17, STRUCT, "Hangar", 1, 0, "Corp (corp)", True), (11, 34, BOX, "Unlocked", 2, 0, "Corp (corp)")]
    storage.replace_assets("corp_assets", rows, owner_corporation_id=CORP, owner_name="Corp (corp)")


def _run(client, **kw):
    return resolver.resolve_pending(client=client, characters=CHARS, corp_roles=kw.pop("corp_roles", {}),
                                    now=kw.pop("now", NOW), **kw)


def test_register_classifies_and_skips_stations(tenant):
    _seed(with_box=True)
    storage.set_cached_structure_name(STRUCT, "Known Fort", 30099555)
    storage.upsert_global_structure_name(STRUCT2, "Shared Fort", 30099555)

    result = resolver.register_seen(NOW)

    rows = store.load([OFFICE, BOX, STRUCT, STRUCT2, STRUCT3, STATION])
    assert STATION not in rows  # NPC stations come from the SDE
    assert rows[OFFICE]["kind"] == "office" and rows[OFFICE]["parent_location_id"] == STATION
    assert rows[BOX]["kind"] == "container" and rows[BOX]["parent_location_id"] == STRUCT
    assert rows[STRUCT]["status"] == "resolved" and rows[STRUCT]["name"] == "Known Fort"
    assert rows[STRUCT2]["status"] == "resolved" and rows[STRUCT2]["name"] == "Shared Fort"
    assert rows[STRUCT3]["status"] == "pending"
    assert result["registered"] == 4  # STRUCT was already recorded by set_cached_structure_name


def test_offices_and_containers_never_reach_esi(tenant):
    _seed(with_box=True)
    esi = FakeESI(names={STRUCT3: ("Fort Three", 30099555)})

    _run(esi)

    assert OFFICE not in {c[0] for c in esi.calls} and BOX not in {c[0] for c in esi.calls}


def test_success_writes_registry_and_the_older_tables(tenant):
    _seed()
    esi = FakeESI(names={STRUCT3: ("Fort Three", 30099555)})

    summary = _run(esi)

    row = store.load([STRUCT3])[STRUCT3]
    assert row["status"] == "resolved" and row["name"] == "Fort Three" and row["solar_system_id"] == 30099555
    assert row["resolved_via"] == "producer:1" and row["attempts"] == 0
    assert storage.get_cached_structure_name(STRUCT3) == (True, "Fort Three")
    assert storage.get_global_structure_names([STRUCT3])[STRUCT3][0] == "Fort Three"
    assert summary["resolved"] == 1


def test_the_character_that_succeeded_before_goes_first(tenant):
    _seed()
    esi = FakeESI(names={STRUCT3: ("Fort Three", None)})
    _run(esi)
    esi.calls.clear()
    _run(esi, force=True)

    assert [c for c in esi.calls if c[0] == STRUCT3][0] == (STRUCT3, "producer:1")
    # a refresh that first succeeded through "buyer:2" would start there next time
    store.record_success(STRUCT3, "Fort Three", None, "buyer:2", NOW)
    esi.calls.clear()
    _run(esi, force=True)
    assert [c for c in esi.calls if c[0] == STRUCT3][0] == (STRUCT3, "buyer:2")


def test_forbidden_waits_seven_days_and_retries_when_characters_change(tenant):
    _seed()
    esi = FakeESI()  # every lookup is a 403

    summary = _run(esi)

    row = store.load([STRUCT3])[STRUCT3]
    assert row["status"] == "forbidden" and row["last_error"] == "HTTP 403"
    assert row["next_retry_at"] == NOW + timedelta(days=7)
    assert summary["forbidden"] == 3  # all three unnamed structures
    esi.calls.clear()
    _run(esi, now=NOW + timedelta(days=1))
    assert esi.calls == []  # not due yet
    resolver.resolve_pending(client=esi, characters=CHARS[:1], corp_roles={}, now=NOW + timedelta(days=1))
    assert esi.calls  # a different set of capable characters retries at once
    esi.calls.clear()
    _run(esi, now=NOW + timedelta(days=8))
    assert esi.calls  # the date passed


def test_not_found_waits_thirty_days(tenant):
    _seed()
    esi = FakeESI(errors={(s, r): "404" for s in (STRUCT, STRUCT2, STRUCT3) for r in ("producer:1", "buyer:2")})

    _run(esi)

    row = store.load([STRUCT3])[STRUCT3]
    assert row["status"] == "not_found" and row["next_retry_at"] == NOW + timedelta(days=30)


def test_transient_backs_off_and_a_good_name_survives(tenant):
    _seed()
    store.register([{"location_id": STRUCT, "kind": "structure", "name": "Old Name", "status": "resolved",
                    "resolved_at": NOW - timedelta(days=40)}])
    errors = {(s, r): "502" for s in (STRUCT, STRUCT2, STRUCT3) for r in ("producer:1", "buyer:2")}
    esi = FakeESI(errors=errors)

    _run(esi)

    unnamed = store.load([STRUCT3])[STRUCT3]
    assert unnamed["status"] == "transient" and unnamed["next_retry_at"] == NOW + timedelta(minutes=15)
    named = store.load([STRUCT])[STRUCT]
    assert named["name"] == "Old Name" and named["status"] == "resolved"  # refresh failed, name kept
    _run(esi, now=NOW + timedelta(minutes=16))
    assert store.load([STRUCT3])[STRUCT3]["next_retry_at"] == NOW + timedelta(minutes=16) + timedelta(minutes=30)
    # a transient error never turns into the long forbidden wait
    assert resolver.retry_after("transient", 20, NOW) == NOW + timedelta(hours=6)


def test_network_error_is_transient(tenant):
    _seed()
    esi = FakeESI(errors={(STRUCT3, "producer:1"): "net", (STRUCT3, "buyer:2"): "net"})

    _run(esi)

    row = store.load([STRUCT3])[STRUCT3]
    assert row["status"] == "transient" and row["last_error"] == "network error"


def test_budget_defers_the_rest(tenant):
    _seed()
    esi = FakeESI()

    summary = _run(esi, budget=2)

    assert summary["calls"] == 2 and summary["deferred"] == 2
    again = _run(esi, budget=100)  # the ones not reached are still due
    assert again["due"] >= 2


def test_corporation_structure_list_resolves_without_per_id_calls(tenant):
    _seed()
    esi = FakeESI(corp_lists={CORP: [
        {"structure_id": STRUCT3, "name": "Listed Fort", "solar_system_id": 30099555}]})

    summary = _run(esi, corp_roles={CORP: "producer:1"})

    assert store.load([STRUCT3])[STRUCT3]["name"] == "Listed Fort"
    assert (STRUCT3, "producer:1") not in esi.calls and summary["resolved"] == 1


def test_a_name_another_tenant_found_costs_no_call(tenant):
    _seed()
    resolver.register_seen(NOW)  # STRUCT3 is pending, nobody has named it yet
    storage.upsert_global_structure_name(STRUCT3, "Shared Fort", 30099555)
    esi = FakeESI()

    summary = _run(esi)

    assert STRUCT3 not in {c[0] for c in esi.calls}
    assert store.load([STRUCT3])[STRUCT3]["name"] == "Shared Fort" and summary["from_global"] == 1


def test_nothing_due_builds_no_client(tenant):
    _seed()
    _run(FakeESI(names={s: (f"F{s}", None) for s in (STRUCT, STRUCT2, STRUCT3)}))

    summary = resolver.resolve_pending(characters=CHARS, now=NOW + timedelta(hours=1))  # no client given

    assert summary["due"] == 0 and summary["calls"] == 0


def test_manual_names_are_never_looked_up(tenant):
    _seed()
    with storage.connect() as conn:
        conn.execute("INSERT INTO manual_location_names (location_id, name) VALUES (?, ?)", (STRUCT3, "My Fort"))
    esi = FakeESI()

    _run(esi)

    assert STRUCT3 not in {c[0] for c in esi.calls}


def test_a_second_run_for_the_same_tenant_is_skipped(tenant):
    _seed()
    tenant_id = storage.get_current_tenant()
    resolver._running.add(tenant_id)
    try:
        assert resolver.resolve_pending(characters=CHARS, now=NOW) == {"skipped": "already running"}
    finally:
        resolver._running.discard(tenant_id)


def test_describe_office_shows_its_structure_and_manual_is_the_last_tier(tenant):
    _seed()
    resolver.register_seen(NOW)
    storage.set_cached_structure_name(STRUCT, "Known Fort", SYSTEM)
    with storage.connect() as conn:
        conn.execute("INSERT INTO manual_location_names (location_id, name) VALUES (?, ?)", (STRUCT, "Manual"))
        conn.execute("INSERT INTO manual_location_names (location_id, name) VALUES (?, ?)", (STRUCT2, "Manual Two"))

    info = reader.describe([OFFICE, STRUCT, STRUCT2, STATION, SYSTEM, 5])

    assert info[STATION].name == "Test Station" and info[STATION].kind == "npc_station"
    assert info[STATION].region_name == "Test Region"
    assert info[OFFICE].kind == "office" and info[OFFICE].name == "Test Station"  # parent is the station
    assert info[OFFICE].effective_id == STATION and info[OFFICE].solar_system_name == "Testsys"
    assert info[STRUCT].name == "Known Fort"        # the resolved name beats the manual one
    assert info[STRUCT2].name == "Manual Two"
    assert info[SYSTEM].name == "Testsys" and info[SYSTEM].kind == "solar_system"
    assert info[5].name is None
    assert storage.get_location_names([STRUCT, 5]) == {STRUCT: "Known Fort", 5: None}


def test_report_suggests_characters_that_own_assets_but_did_not_tick_the_capability(tenant, monkeypatch):
    from types import SimpleNamespace
    from eve_trader import admin
    from eve_trader.auth import TokenManager
    from eve_trader.esi_data import selector

    storage.replace_assets("character_assets", [(1, 34, STRUCT, "Hangar", 5, 0, "Alt")],
                           owner_character_id=777, owner_name="Alt")
    storage.replace_assets("character_assets", [(2, 34, STRUCT2, "Hangar", 5, 0, "Capable")],
                           owner_character_id=888, owner_name="Capable")
    monkeypatch.setattr(selector, "select_auth_role", lambda cid, scope, tokens=None: f"producer:{cid}")
    monkeypatch.setattr(TokenManager, "get_record", lambda self, role: SimpleNamespace(character_name=f"Name {role}"))
    report = {"unresolved": [
        {"location_id": STRUCT, "kind": "structure"}, {"location_id": STRUCT2, "kind": "structure"},
        {"location_id": OFFICE, "kind": "office"},
    ]}

    suggested = admin._suggest_resolution_characters(report, [("producer:888", 888, "Capable")])

    assert suggested == [{"character_id": 777, "character_name": "Name producer:777", "structures": 1}]


def test_planetary_goods_only_flags_locations_holding_nothing_else(tenant):
    from eve_trader import admin
    PI_TYPE, SHIP_TYPE = 990901, 990902
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO sde_categories VALUES (43, 'Planetary Commodities') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO sde_categories VALUES (6, 'Ship') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO sde_groups VALUES (990043, 43, 'Basic Commodities') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO sde_groups VALUES (990006, 6, 'Frigate') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO sde_types (type_id, group_id, type_name, volume, published) VALUES (%s,990043,'Test Oxygen',0.38,1) ON CONFLICT (type_id) DO NOTHING", (PI_TYPE,))
        conn.execute("INSERT INTO sde_types (type_id, group_id, type_name, volume, published) VALUES (%s,990006,'Test Ship',1,1) ON CONFLICT (type_id) DO NOTHING", (SHIP_TYPE,))
    storage.replace_assets("character_assets", [
        (1, PI_TYPE, STRUCT, "Hangar", 10, 0, "Alt"),
        (2, PI_TYPE, STRUCT2, "Hangar", 10, 0, "Alt"),
        (3, SHIP_TYPE, STRUCT2, "Hangar", 1, 0, "Alt"),
    ], owner_character_id=777, owner_name="Alt")

    assert admin._planetary_goods_only({STRUCT, STRUCT2, STRUCT3}) == {STRUCT}
    assert admin._planetary_goods_only(set()) == set()


def test_resolve_ids_registers_resolves_and_remembers_a_refusal(tenant):
    _seed()
    esi = FakeESI(names={STRUCT3: ("Fort Three", 30099555)})

    first = resolver.resolve_ids([STRUCT3, STRUCT2], client=esi, characters=CHARS, corp_roles={}, now=NOW)

    assert first["ids"][STRUCT3] == {"name": "Fort Three", "status": "resolved", "attempted": True}
    assert first["ids"][STRUCT2]["name"] is None and first["ids"][STRUCT2]["status"] == "forbidden"
    assert first["ids"][STRUCT2]["attempted"] is True
    # only the asked-for ids were registered and looked up
    assert STRUCT not in store.load([STRUCT]) and OFFICE not in {c[0] for c in esi.calls}

    esi.calls.clear()
    later = resolver.resolve_ids([STRUCT2], client=esi, characters=CHARS, corp_roles={}, now=NOW + timedelta(days=1))
    assert esi.calls == [] and later["ids"][STRUCT2]["attempted"] is False  # remembered, not retried

    forced = resolver.resolve_ids([STRUCT2], force=True, client=esi, characters=CHARS, corp_roles={},
                                  now=NOW + timedelta(days=1))
    assert esi.calls and forced["ids"][STRUCT2]["attempted"] is True


def test_record_resolved_registers_an_id_found_outside_the_resolver(tenant):
    resolver.record_resolved(STRUCT3, "Operator Fort", 30099555, now=NOW)

    row = store.load([STRUCT3])[STRUCT3]
    assert row["kind"] == "structure" and row["status"] == "resolved" and row["name"] == "Operator Fort"
    assert storage.get_global_structure_names([STRUCT3])[STRUCT3][0] == "Operator Fort"
