"""Character Management phase 2 (docs/CHARACTER_MANAGEMENT_PLAN.md): skills,
attributes, SP totals, skill queue, the SDE skill catalogue, the accessor
shapes for `char_skills`, and the Skills actions + router."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.auth import TokenRecord
from eve_trader.character_management import skills_actions
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG
from eve_trader.esi_client import ESIError
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.access import AccessorError, read_esi
from eve_trader.esi_data.orchestrator import do_sync_for_tool
from eve_trader.production import sde

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
BOB = 1002
INDUSTRY = 3380
MINING = 3386
SKILLS_SCOPE = "esi-skills.read_skills.v1"
QUEUE_SCOPE = "esi-skills.read_skillqueue.v1"

_TABLES = (
    "character_skills", "character_attributes", "character_skillqueue", "character_slots",
    "esi_sharing", "esi_freshness", "tenant_tokens",
)
_SDE = ("sde_types", "sde_groups", "sde_skill_meta", "sde_skill_requirements")


@pytest.fixture(scope="session", autouse=True)
def _sde_tables_exist(_apply_doctrine_schema, _apply_refining_schema):
    """replace_sde_data touches every sde_* table, including sde_type_slots
    (doctrine_schema.sql) and sde_type_materials (refining_schema.sql) - they
    only exist once those schema fixtures have run, which is order-dependent
    when this module runs early in a full suite."""


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES, *_SDE)
    storage.get_sde_type.cache_clear()
    orchestrator._in_flight.clear()
    yield
    pg_helpers.wipe_tables(*_TABLES, *_SDE)
    storage.get_sde_type.cache_clear()


def _share(owner_id, kind, tool="char_skills"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (owner_id, kind, tool),
        )


def _token(cid, name, scopes=f"{SKILLS_SCOPE} {QUEUE_SCOPE}"):
    role = f"esi:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=scopes,
    )))


def _seed_sde():
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO sde_groups (group_id, category_id, group_name) VALUES (?,?,?)",
            [(268, 16, "Industry"), (1216, 16, "Spaceship Command")],
        )
        conn.executemany(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published, "
            "market_group_id, meta_level, meta_group_id, portion_size) VALUES (?,?,?,?,?,?,?,?,?)",
            [(INDUSTRY, 268, "Industry", 0.01, 1, None, None, None, 1),
             (MINING, 1216, "Mining", 0.01, 1, None, None, None, 1)],
        )
        conn.executemany(
            "INSERT INTO sde_skill_meta (skill_id, rank, primary_attribute, secondary_attribute) VALUES (?,?,?,?)",
            [(INDUSTRY, 1.0, 165, 166), (MINING, 2.0, 165, 166)],
        )


class FakeClient:
    def __init__(self):
        self.calls: list[str] = []
        self.attributes_error = False

    def character_skills(self, cid, auth_role):
        self.calls.append("skills")
        return {
            "skills": [
                {"skill_id": INDUSTRY, "active_skill_level": 5, "trained_skill_level": 5,
                 "skillpoints_in_skill": 256000},
                {"skill_id": MINING, "active_skill_level": 3, "trained_skill_level": 4,
                 "skillpoints_in_skill": 50000},
            ],
            "total_sp": 5_500_000, "unallocated_sp": 1234,
        }

    def character_attributes(self, cid, auth_role):
        self.calls.append("attributes")
        if self.attributes_error:
            raise ESIError("HTTP 500 for attributes")
        return {"charisma": 19, "intelligence": 27, "memory": 21, "perception": 20, "willpower": 22,
                "bonus_remaps": 1, "last_remap_date": "2026-01-01T00:00:00Z"}

    def character_skillqueue(self, cid, auth_role):
        self.calls.append("skillqueue")
        return [
            {"queue_position": 0, "skill_id": MINING, "finished_level": 4,
             "start_date": "2026-09-29T08:00:00Z", "finish_date": "2099-01-01T00:00:00Z",
             "training_start_sp": 40000, "level_start_sp": 40000, "level_end_sp": 90510},
            {"queue_position": 1, "skill_id": INDUSTRY, "finished_level": 5},   # no dates
        ]


# ------------------------------------------------- SDE parsing (no Postgres)
class _FakeResponse:
    def __init__(self, lines, fail_after=None):
        self._lines, self._fail_after = lines, fail_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for i, line in enumerate(self._lines):
            if self._fail_after is not None and i >= self._fail_after:
                raise requests.exceptions.ChunkedEncodingError("connection reset")
            yield line


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        assert kwargs.get("stream") is True
        self.requested.append(url)
        return self.responses.pop(0)


_HEADER = "﻿typeID,attributeID,valueInt,valueFloat"


def _csv(*rows):
    return [_HEADER.encode("utf-8")] + [r.encode("utf-8") for r in rows]


def test_streamed_attribute_fetch_keeps_only_skill_attributes(monkeypatch):
    monkeypatch.setattr(sde.time, "sleep", lambda s: None)
    session = _FakeSession([_FakeResponse(_csv(
        "3380,275,,1.0",          # skillTimeConstant (rank), float column
        "3380,180,165,",          # primaryAttribute, int column
        "3380,181,166,",
        "3380,9999,5,",           # some unrelated attribute - dropped
        "999,182,3380,",          # requiredSkill1 of type 999 ...
        "999,277,3,",             # ... at level 3
        "",                        # blank line is skipped by DictReader
    ))])
    kept = sde._fetch_skill_attributes(session, "https://x/")
    assert kept == {3380: {275: 1.0, 180: 165.0, 181: 166.0}, 999: {182: 3380.0, 277: 3.0}}
    assert session.requested == ["https://x/dgmTypeAttributes.csv"]


def test_stream_reset_midway_restarts_instead_of_returning_a_truncated_table(monkeypatch):
    monkeypatch.setattr(sde.time, "sleep", lambda s: None)
    good = _csv("3380,275,,1.0", "3386,275,,2.0")
    session = _FakeSession([_FakeResponse(good, fail_after=2), _FakeResponse(good)])
    kept = sde._fetch_skill_attributes(session, "https://x/")
    assert set(kept) == {3380, 3386}          # the retry's complete data, not the first half
    assert len(session.requested) == 2


def test_stream_gives_up_after_three_failed_attempts(monkeypatch):
    monkeypatch.setattr(sde.time, "sleep", lambda s: None)
    bad = [_FakeResponse(_csv("3380,275,,1.0"), fail_after=1) for _ in range(3)]
    with pytest.raises(requests.RequestException):
        sde._fetch_skill_attributes(_FakeSession(bad), "https://x/")


def test_skill_rows_from_attributes():
    attrs = {
        # a ship requiring two skills, one without a level attribute (skipped)
        587: {182: 3330.0, 277: 1.0, 183: 3331.0, 278: 3.0, 184: 3332.0},
        # a skill: rank + attributes
        3380: {275: 1.0, 180: 165.0, 181: 166.0},
        # a skill missing its secondary attribute
        3386: {275: 2.0, 180: 165.0},
    }
    requirements, meta = sde.skill_rows_from_attributes(attrs)
    assert sorted(requirements) == [(587, 3330, 1), (587, 3331, 3)]
    assert sorted(meta) == [(3380, 1.0, 165, 166), (3386, 2.0, 165, None)]


def test_fetch_sde_carries_the_skill_rows_into_fetchedsde_and_apply_stores_them(tenant, monkeypatch):
    monkeypatch.setattr(sde, "_fetch_csv", lambda session, base, filename: [])
    monkeypatch.setattr(sde, "_dump_etag", lambda *a, **k: "etag")
    monkeypatch.setattr(sde, "_fetch_skill_attributes", lambda session, base: {
        587: {182: 3330.0, 277: 2.0}, 3380: {275: 1.0, 180: 165.0, 181: 166.0},
    })
    fetched = sde.fetch_sde()
    assert fetched.skill_requirements == [(587, 3330, 2)]
    assert fetched.skill_meta == [(3380, 1.0, 165, 166)]
    sde.apply_sde(fetched)
    counts = storage.sde_row_counts()
    assert counts["sde_skill_requirements"] == 1 and counts["sde_skill_meta"] == 1


# ------------------------------------------------------------ SDE storage
def test_replace_sde_data_stores_and_replaces_the_skill_tables(tenant):
    storage.replace_sde_data(
        types=[], groups=[], market_groups=[], blueprint_time=[], blueprint_materials=[],
        blueprint_products=[], skill_requirements=[(587, 3330, 1)], skill_meta=[(3380, 1.0, 165, 166)],
    )
    counts = storage.sde_row_counts()
    assert counts["sde_skill_requirements"] == 1 and counts["sde_skill_meta"] == 1
    storage.replace_sde_data(
        types=[], groups=[], market_groups=[], blueprint_time=[], blueprint_materials=[],
        blueprint_products=[],
    )
    counts = storage.sde_row_counts()
    assert counts["sde_skill_requirements"] == 0 and counts["sde_skill_meta"] == 0


def test_skill_catalog_joins_name_group_and_rank(tenant):
    _seed_sde()
    catalog = storage.get_skill_catalog([INDUSTRY, MINING, 424242])
    assert catalog[INDUSTRY] == {
        "name": "Industry", "group_id": 268, "group_name": "Industry",
        "rank": 1.0, "primary_attribute": 165, "secondary_attribute": 166,
    }
    assert catalog[MINING]["group_name"] == "Spaceship Command"
    assert 424242 not in catalog
    assert storage.get_skill_catalog([]) == {}


def test_skill_catalog_without_meta_still_gives_names(tenant):
    _seed_sde()
    with storage.connect() as conn:
        conn.execute("DELETE FROM sde_skill_meta")
    catalog = storage.get_skill_catalog([INDUSTRY])
    assert catalog[INDUSTRY]["name"] == "Industry"
    assert catalog[INDUSTRY]["rank"] is None


# ---------------------------------------------------------------- fetchers
def test_skills_sync_writes_slots_skills_totals_and_attributes(tenant):
    _token(ALICE, "Alice")
    _share(ALICE, "skills")
    client = FakeClient()
    result = do_sync_for_tool("char_skills", client=client)
    assert result["ok"] is True
    assert storage.load_character_slots()          # Production's slot row is still written
    skills = storage.load_character_skills([ALICE])
    assert [(r[1], r[2], r[3], r[4]) for r in skills] == [
        (INDUSTRY, 5, 5, 256000), (MINING, 3, 4, 50000),
    ]
    attrs = storage.load_character_attributes([ALICE])[0]
    assert attrs[1:3] == (5_500_000, 1234)                    # total_sp, unallocated_sp
    assert attrs[3:8] == (19, 27, 21, 20, 22)


def test_a_failing_attributes_call_never_fails_the_skills_sync(tenant):
    """The owner batch would roll back on an ESIError and take Production's
    job-slot sync with it - attributes are best-effort."""
    _token(ALICE, "Alice")
    _share(ALICE, "skills", "production")
    client = FakeClient()
    client.attributes_error = True
    result = do_sync_for_tool("production", client=client)
    assert result["ok"] is True
    assert storage.load_character_slots()
    attrs = storage.load_character_attributes([ALICE])[0]
    assert attrs[1] == 5_500_000            # totals survived
    assert attrs[3] is None                  # attributes did not


def test_skillqueue_sync_stores_entries_including_paused_ones_without_dates(tenant):
    _token(ALICE, "Alice")
    _share(ALICE, "skillqueue")
    do_sync_for_tool("char_skills", client=FakeClient())
    rows = storage.load_character_skillqueue([ALICE])
    assert [(r[1], r[2], r[3]) for r in rows] == [(0, MINING, 4), (1, INDUSTRY, 5)]
    assert rows[0][5].startswith("2099-01-01")
    assert rows[1][4] is None and rows[1][5] is None


def test_sync_reauth_when_the_queue_scope_is_missing(tenant):
    _token(ALICE, "Alice", scopes=SKILLS_SCOPE)
    _share(ALICE, "skills")
    _share(ALICE, "skillqueue")
    result = do_sync_for_tool("char_skills", client=FakeClient())
    kinds = result["owners"][0]["kinds"]
    assert kinds["skillqueue"].startswith("re-auth needed")
    assert isinstance(kinds["skills"], dict)


# ---------------------------------------------------------------- accessor
def test_read_esi_char_skills_shapes_and_gating(tenant):
    _token(ALICE, "Alice")
    _share(ALICE, "skills", "production")            # production only
    do_sync_for_tool("production", client=FakeClient())
    assert read_esi("skills", "char_skills") == []   # not shared with char_skills
    _share(ALICE, "skills")
    rows = read_esi("skills", "char_skills")
    assert {r["skill_id"] for r in rows} == {INDUSTRY, MINING}
    assert rows[0]["owner_type"] == "character" and rows[0]["owner_id"] == ALICE
    attrs = read_esi("skills", "char_skills", table="attributes")
    assert attrs[0]["total_sp"] == 5_500_000 and attrs[0]["intelligence"] == 27
    with pytest.raises(AccessorError, match="unknown skills table"):
        read_esi("skills", "char_skills", table="nope")


def test_production_still_reads_slot_rows_not_the_new_shape(tenant):
    _token(ALICE, "Alice")
    _share(ALICE, "skills", "production")
    do_sync_for_tool("production", client=FakeClient())
    rows = read_esi("skills", "production")
    assert "manufacturing_slots" in rows[0] and "skill_id" not in rows[0]


def test_read_esi_skillqueue_is_gated_per_tool(tenant):
    _token(ALICE, "Alice")
    _share(ALICE, "skillqueue")
    do_sync_for_tool("char_skills", client=FakeClient())
    assert read_esi("skillqueue", "char_skills")[0]["skill_id"] == MINING
    assert read_esi("skillqueue", "char_info") == []


# ------------------------------------------------------------ pure helpers
def test_extractable_estimate_and_sp_to_level_v():
    assert skills_actions.extractable_estimate(None) is None
    assert skills_actions.extractable_estimate(4_999_999) == 0
    assert skills_actions.extractable_estimate(5_000_000) == 0
    assert skills_actions.extractable_estimate(5_500_000) == 1
    assert skills_actions.extractable_estimate(10_400_000) == 10
    assert skills_actions.sp_to_level_v(None, 100) is None
    assert skills_actions.sp_to_level_v(2.0, 50_000) == 512_000 - 50_000
    assert skills_actions.sp_to_level_v(1.0, 999_999) == 0        # never negative


def test_queue_value_summary():
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

    def row(pos, sid, lvl, finish):
        return {"queue_position": pos, "skill_id": sid, "finished_level": lvl, "start_date": None,
                "finish_date": finish, "training_start_sp": None, "level_start_sp": None,
                "level_end_sp": None}

    catalog = {INDUSTRY: {"name": "Industry"}}
    running = skills_actions._queue_value(
        [row(1, MINING, 4, "2026-09-30T00:00:00Z"), row(0, INDUSTRY, 5, "2026-09-29T18:00:00Z")],
        catalog, now,
    )
    assert running["length"] == 2 and not running["paused"] and not running["empty"]
    assert running["current"]["name"] == "Industry"                # position 0, still in the future
    assert running["current"]["skill_id"] == INDUSTRY
    assert running["ends_at"].startswith("2026-09-30T00:00:00")
    assert [e["queue_position"] for e in running["entries"]] == [0, 1]

    assert skills_actions._queue_value([], {}, now)["empty"] is True
    paused = skills_actions._queue_value([row(0, INDUSTRY, 5, None)], catalog, now)
    assert paused["paused"] is True and paused["ends_at"] is None and paused["current"] is None
    stale = skills_actions._queue_value([row(0, INDUSTRY, 5, "2026-09-01T00:00:00Z")], catalog, now)
    assert stale["current"] is None and not stale["paused"]        # finished since the snapshot


# --------------------------------------------------------------- actions
def _sync_alice(tenant_scope=None):
    _token(ALICE, "Alice")
    _share(ALICE, "skills")
    _share(ALICE, "skillqueue")
    do_sync_for_tool("char_skills", client=FakeClient())


def test_overview_states_and_summary(tenant):
    _seed_sde()
    _token(ALICE, "Alice")
    _token(BOB, "Bob", scopes=SKILLS_SCOPE)
    _share(ALICE, "skills")
    _share(ALICE, "skillqueue")
    do_sync_for_tool("char_skills", client=FakeClient())
    rows = {c["character_id"]: c for c in skills_actions.do_skills_overview()["characters"]}

    alice = rows[ALICE]
    assert alice["summary"]["state"] == "ok"
    assert alice["summary"]["value"]["total_sp"] == 5_500_000
    assert alice["summary"]["value"]["extractable_estimate"] == 1
    assert alice["summary"]["value"]["attributes"]["intelligence"] == 27
    assert alice["queue"]["state"] == "ok"
    assert alice["queue"]["value"]["length"] == 2
    assert alice["queue"]["value"]["current"]["name"] == "Mining"
    assert alice["queue"]["value"]["ends_at"].startswith("2099-01-01")

    assert rows[BOB]["summary"]["state"] == "not_shared"
    assert rows[BOB]["queue"]["state"] == "not_shared"


def test_overview_reauth_and_not_synced(tenant):
    _token(ALICE, "Alice", scopes=SKILLS_SCOPE)         # no queue scope
    _share(ALICE, "skills")
    _share(ALICE, "skillqueue")
    alice = skills_actions.do_skills_overview()["characters"][0]
    assert alice["summary"]["state"] == "not_synced"    # shared + scope, never synced
    assert alice["queue"]["state"] == "reauth_needed"


def test_character_skills_groups_names_and_rank(tenant):
    _seed_sde()
    _sync_alice()
    detail = skills_actions.do_character_skills(ALICE)
    groups = {g["group_name"]: g for g in detail["skills"]["value"]}
    assert set(groups) == {"Industry", "Spaceship Command"}
    industry = groups["Industry"]["skills"][0]
    assert industry["name"] == "Industry" and industry["rank"] == 1.0
    assert industry["trained_level"] == 5 and industry["sp_to_level_v"] == 0
    mining = groups["Spaceship Command"]["skills"][0]
    assert mining["active_level"] == 3 and mining["trained_level"] == 4
    assert mining["sp_to_level_v"] == 512_000 - 50_000
    assert groups["Industry"]["maxed"] == 1 and groups["Spaceship Command"]["maxed"] == 0
    assert groups["Industry"]["total_sp"] == 256000
    assert detail["queue"]["value"]["entries"][0]["name"] == "Mining"


def test_character_skills_without_a_sde_falls_back_to_ids(tenant):
    _sync_alice()
    detail = skills_actions.do_character_skills(ALICE)
    groups = detail["skills"]["value"]
    assert [g["group_name"] for g in groups] == ["Unknown group"]
    names = {s["name"] for s in groups[0]["skills"]}
    assert names == {f"Skill {INDUSTRY}", f"Skill {MINING}"}
    assert all(s["rank"] is None and s["sp_to_level_v"] is None for s in groups[0]["skills"])


def test_character_skills_rejects_unknown_characters(tenant):
    with pytest.raises(ActionError, match="not registered"):
        skills_actions.do_character_skills(424242)
    with pytest.raises(ActionError):
        skills_actions.do_character_skills("abc")


def test_matrix_lists_shared_characters_and_reports_hidden_ones(tenant):
    _seed_sde()
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE, "skills")
    do_sync_for_tool("char_skills", client=FakeClient())
    matrix = skills_actions.do_skill_matrix()
    assert [c["character_id"] for c in matrix["characters"]] == [ALICE]
    assert [c["character_id"] for c in matrix["hidden_characters"]] == [BOB]
    by_name = {s["name"]: s for g in matrix["groups"] for s in g["skills"]}
    assert by_name["Industry"]["levels"] == {str(ALICE): {"active": 5, "trained": 5}}
    assert by_name["Mining"]["levels"][str(ALICE)] == {"active": 3, "trained": 4}


def test_matrix_flags_a_shared_character_that_needs_reauth(tenant):
    _token(ALICE, "Alice", scopes="")
    _share(ALICE, "skills")
    matrix = skills_actions.do_skill_matrix()
    assert matrix["reauth_needed"] == [ALICE]


def test_sync_action_summarises_via_the_shared_helper(tenant, monkeypatch):
    monkeypatch.setattr(skills_actions, "do_sync_for_tool", lambda tool_key: {
        "ok": True, "characters": {}, "owners": [{"owner_id": 7, "skipped": "in_flight", "ok": True}],
    })
    assert skills_actions.do_sync_char_skills() == {
        "ok": True, "characters": {}, "in_flight": [7], "failed": [],
    }


def test_one_tenants_skill_rows_are_invisible_to_another(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.replace_character_skills(ALICE, [(INDUSTRY, 5, 5, 256000)])
    with storage.tenant_context(b):
        assert storage.load_character_skills([ALICE]) == []


# ------------------------------------------------------------------ router
_TENANT = "00000000-0000-0000-0000-000000000fed"
_client = TestClient(create_app())


def _cookie():
    return {access_gate.SESSION_COOKIE_NAME:
            access_gate.create_session_token(1, "Some Character", _TENANT)}


@pytest.fixture
def _gate_tables():
    tables = ("tool_grants", "tenant_registry_entries", "character_session_revocations",
              "access_requests", "access_allowlist")
    _client.cookies.clear()
    pg_helpers.wipe_tables(*tables)
    yield
    _client.cookies.clear()
    pg_helpers.wipe_tables(*tables)


def test_char_skills_routes_need_the_char_skills_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    monkeypatch.setattr(skills_actions, "do_skills_overview", lambda: {"characters": []})
    storage.add_tenant_registry_entry(_TENANT, 1, character_name="Some Character")
    storage.set_tool_grant(1, "char_info", _TENANT)          # a sibling sub-tool is not enough
    assert _client.get("/api/char-skills/overview", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "char_skills", _TENANT)
    ok = _client.get("/api/char-skills/overview", cookies=_cookie())
    assert ok.status_code == 200 and ok.json() == {"characters": []}


def test_char_skills_router_converts_action_errors_to_400(monkeypatch):
    def boom(character_id):
        raise ActionError("nope")
    monkeypatch.setattr(skills_actions, "do_character_skills", boom)
    resp = _client.get("/api/char-skills/characters/1")
    assert resp.status_code == 400 and resp.json()["detail"] == "nope"


# ------------------------------------------------- queue guard (phase 5a)
from eve_trader.config import TRADING_CONFIG  # noqa: E402


@pytest.fixture
def warn_hours(monkeypatch):
    monkeypatch.setattr(TRADING_CONFIG, "char_skills_queue_warning_hours", 24.0)
    return TRADING_CONFIG


def _at(hours: float, now: datetime):
    from datetime import timedelta
    return now + timedelta(hours=hours)


def test_queue_warning_kinds_and_boundaries():
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    w = skills_actions.queue_warning
    assert w(empty=True, paused=False, ends_at=None, now=now, warn_hours=24) == {"kind": "empty", "hours_left": None}
    assert w(empty=False, paused=True, ends_at=None, now=now, warn_hours=24) == {"kind": "paused", "hours_left": None}
    assert w(empty=False, paused=False, ends_at=_at(-1, now), now=now, warn_hours=24) == {"kind": "ended", "hours_left": 0.0}
    assert w(empty=False, paused=False, ends_at=_at(5.55, now), now=now, warn_hours=24) == {"kind": "ends_soon", "hours_left": 5.5}
    assert w(empty=False, paused=False, ends_at=_at(23.99, now), now=now, warn_hours=24)["kind"] == "ends_soon"
    assert w(empty=False, paused=False, ends_at=_at(24, now), now=now, warn_hours=24) is None     # exactly at the threshold: fine
    assert w(empty=False, paused=False, ends_at=_at(200, now), now=now, warn_hours=24) is None
    assert w(empty=False, paused=False, ends_at=None, now=now, warn_hours=24) is None


def test_a_threshold_of_zero_switches_every_warning_off():
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    for kwargs in (dict(empty=True, paused=False, ends_at=None), dict(empty=False, paused=True, ends_at=None),
                   dict(empty=False, paused=False, ends_at=_at(-5, now))):
        assert skills_actions.queue_warning(now=now, warn_hours=0, **kwargs) is None


def test_queue_value_carries_the_warning(warn_hours):
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    soon = [{"queue_position": 0, "skill_id": INDUSTRY, "finished_level": 5, "start_date": None,
             "finish_date": "2026-09-30T00:00:00Z", "training_start_sp": None, "level_start_sp": None,
             "level_end_sp": None}]
    assert skills_actions._queue_value(soon, {}, now)["warning"] == {"kind": "ends_soon", "hours_left": 12.0}
    assert skills_actions._queue_value(soon, {}, now, warn_hours=6)["warning"] is None
    assert skills_actions._queue_value([], {}, now)["warning"]["kind"] == "empty"


def test_do_queue_warnings_counts_only_shared_synced_queues_that_need_attention(tenant, warn_hours):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _token(3003, "Carol", scopes=SKILLS_SCOPE)              # queue scope missing -> reauth, contributes nothing
    for cid in (ALICE, BOB, 3003):
        _share(cid, "skillqueue")
    storage.replace_character_skillqueue(ALICE, [])         # empty  -> warned
    storage.replace_character_skillqueue(BOB, [(0, INDUSTRY, 5, None, "2099-01-01T00:00:00Z", None, None, None)])   # fine
    for cid in (ALICE, BOB):
        storage.upsert_esi_freshness("character", cid, "skillqueue", success=True)
    out = skills_actions.do_queue_warnings()
    assert out["count"] == 1
    assert out["characters"] == [{"character_id": ALICE, "character_name": "Alice", "kind": "empty", "hours_left": None}]
    assert out["queue_warning_hours"] == 24.0


def test_do_queue_warnings_is_empty_when_warnings_are_off(tenant, warn_hours):
    _token(ALICE, "Alice")
    _share(ALICE, "skillqueue")
    storage.replace_character_skillqueue(ALICE, [])
    storage.upsert_esi_freshness("character", ALICE, "skillqueue", success=True)
    assert skills_actions.do_queue_warnings()["count"] == 1
    skills_actions.do_set_queue_warning_hours(0)
    assert skills_actions.do_queue_warnings()["count"] == 0


def test_the_warning_threshold_is_validated_persisted_and_applied(tenant, warn_hours):
    assert skills_actions.do_get_skills_settings() == {"queue_warning_hours": 24.0}
    assert skills_actions.do_set_queue_warning_hours(48) == {"queue_warning_hours": 48.0}
    assert TRADING_CONFIG.char_skills_queue_warning_hours == 48.0
    assert storage.load_tenant_settings("trading")["char_skills_queue_warning_hours"] == 48.0
    for bad in (-1, 24 * 60 + 1, "soon", None):
        with pytest.raises(ActionError):
            skills_actions.do_set_queue_warning_hours(bad)
    assert TRADING_CONFIG.char_skills_queue_warning_hours == 48.0       # a rejected value changed nothing


def test_queue_guard_routes(monkeypatch):
    seen = {}
    monkeypatch.setattr(skills_actions, "do_queue_warnings", lambda: {"count": 0, "characters": [], "queue_warning_hours": 24})
    monkeypatch.setattr(skills_actions, "do_get_skills_settings", lambda: {"queue_warning_hours": 24})
    monkeypatch.setattr(skills_actions, "do_set_queue_warning_hours", lambda hours: seen.update(hours=hours) or {"queue_warning_hours": hours})
    assert _client.get("/api/char-skills/warnings").json()["count"] == 0
    assert _client.get("/api/char-skills/settings").json() == {"queue_warning_hours": 24}
    assert _client.post("/api/char-skills/settings", json={"queue_warning_hours": 36}).json() == {"queue_warning_hours": 36}
    assert seen == {"hours": 36.0}
    assert _client.post("/api/char-skills/settings", json={"queue_warning_hours": "x"}).status_code == 422
