"""Character Management phase 5b (docs/CHARACTER_MANAGEMENT_PLAN.md): which
characters can fly which doctrine fitting - SDE skill requirements, transitive
prerequisites, per-character gaps and a training-time estimate."""
from __future__ import annotations

from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.auth import TokenRecord
from eve_trader.character_management import skill_check
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE, BOB, CAROL = 1001, 1002, 1003
SKILLS_SCOPE = "esi-skills.read_skills.v1"

# skills
GUNNERY, SMALL_PROJ, FRIGATE = 3300, 3301, 3330
SPEC_DRONES, MECH = 3436, 3392
# types
RIFTER, AUTOCANNON, EMP_S, HAMMERHEAD = 587, 2929, 185, 2185

_TABLES = (
    "character_skills", "character_attributes", "esi_sharing", "esi_freshness", "tenant_tokens",
    "doctrine_fitting_items", "doctrine_fittings", "doctrines",
)
_SDE = ("sde_types", "sde_groups", "sde_skill_meta", "sde_skill_requirements")


@pytest.fixture(scope="session", autouse=True)
def _tables_exist(_apply_doctrine_schema, _apply_refining_schema):
    """replace_sde_data / the doctrine tables only exist once those schema
    fixtures ran (order-dependent when this module runs early)."""


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES, *_SDE)
    storage.get_sde_type.cache_clear()
    yield
    pg_helpers.wipe_tables(*_TABLES, *_SDE)
    storage.get_sde_type.cache_clear()


def _seed_sde(requirements=None):
    """Rifter needs Frigate I; the autocannon needs Small Projectile Turret III,
    which itself needs Gunnery II (a prerequisite, not a direct requirement)."""
    reqs = requirements if requirements is not None else [
        (RIFTER, FRIGATE, 1), (AUTOCANNON, SMALL_PROJ, 3), (SMALL_PROJ, GUNNERY, 2),
        (EMP_S, SMALL_PROJ, 1), (HAMMERHEAD, SPEC_DRONES, 2),
    ]
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published, market_group_id, "
            "meta_level, meta_group_id, portion_size) VALUES (?,?,?,?,?,?,?,?,?)",
            [(t, 1, n, 1.0, 1, None, None, None, 1) for t, n in [
                (RIFTER, "Rifter"), (AUTOCANNON, "200mm AutoCannon I"), (EMP_S, "EMP S"), (HAMMERHEAD, "Hammerhead I"),
                (GUNNERY, "Gunnery"), (SMALL_PROJ, "Small Projectile Turret"), (FRIGATE, "Minmatar Frigate"),
                (SPEC_DRONES, "Drone Specialization"),
            ]],
        )
        conn.executemany("INSERT INTO sde_skill_requirements (type_id, skill_id, level) VALUES (?,?,?)", reqs)
        conn.executemany(
            "INSERT INTO sde_skill_meta (skill_id, rank, primary_attribute, secondary_attribute) VALUES (?,?,?,?)",
            [(GUNNERY, 1.0, 168, 165), (SMALL_PROJ, 2.0, 165, 166), (FRIGATE, 1.0, 165, 166), (SPEC_DRONES, 3.0, 165, 166)],
        )
    storage.get_sde_type.cache_clear()


def _token(cid, name):
    role = f"esi:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=SKILLS_SCOPE,
    )))


def _share(cid):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, 'skills', 'char_skills') ON CONFLICT DO NOTHING", (cid,),
        )


def _skills(cid, rows, attrs=(27, 21)):
    storage.replace_character_skills(cid, rows)          # (skill, active, trained, sp)
    storage.upsert_character_skill_totals(cid, 5_000_000, 0)
    if attrs:
        storage.upsert_character_attributes(cid, {
            "intelligence": attrs[0], "memory": attrs[1], "perception": 20, "willpower": 22, "charisma": 19,
        })


def _fitting(name="Rifter AC", hull=RIFTER, items=((AUTOCANNON, 1), (EMP_S, 100)), active=True, doctrine_id=None):
    doctrine_id = doctrine_id or storage.create_doctrine("Frigate doctrine", None)
    fid = storage.create_fitting(doctrine_id, name, hull, "[raw eft]", None, 1, 1, None)
    storage.replace_fitting_items(fid, [(i, "high", tid, qty, False) for i, (tid, qty) in enumerate(items, start=1)])
    if not active:
        storage.update_fitting(fid, {"active": False})
    return doctrine_id, fid


# --------------------------------------------------------- pure helpers
def test_sp_for_level_matches_the_games_numbers():
    assert [skill_check.sp_for_level(1, lv) for lv in range(1, 6)] == [250, 1415, 8000, 45255, 256000]
    assert [skill_check.sp_for_level(3, lv) for lv in range(1, 6)] == [750, 4243, 24000, 135765, 768000]


def test_required_skills_expands_prerequisites_and_keeps_the_highest_level(tenant):
    _seed_sde()
    need = skill_check.required_skills({RIFTER, AUTOCANNON, EMP_S})
    # the autocannon needs Small Projectile III (EMP S only I -> the higher wins);
    # Small Projectile itself needs Gunnery II, which nothing lists directly
    assert need == {FRIGATE: 1, SMALL_PROJ: 3, GUNNERY: 2}


def test_required_skills_of_types_without_requirements_is_empty(tenant):
    _seed_sde()
    assert skill_check.required_skills({999999}) == {}
    assert skill_check.required_skills(set()) == {}


def test_required_skills_survives_a_cycle_in_the_data(tenant):
    _seed_sde([(RIFTER, GUNNERY, 1), (GUNNERY, SMALL_PROJ, 1), (SMALL_PROJ, GUNNERY, 2)])
    assert skill_check.required_skills({RIFTER}) == {GUNNERY: 2, SMALL_PROJ: 1}


def test_training_seconds_needs_attributes_rank_and_both_attribute_ids():
    catalog = {SMALL_PROJ: {"rank": 2.0, "primary_attribute": 165, "secondary_attribute": 166}}
    missing = [{"skill_id": SMALL_PROJ, "sp_remaining": 16000}]
    attrs = {"intelligence": 27, "memory": 21}
    assert skill_check.training_seconds(missing, catalog, attrs) == 25600        # 16000 / 37.5 per minute
    assert skill_check.training_seconds([], catalog, attrs) == 0
    assert skill_check.training_seconds(missing, catalog, None) is None          # attributes not synced
    assert skill_check.training_seconds(missing, {SMALL_PROJ: {"rank": None, "primary_attribute": 165, "secondary_attribute": 166}}, attrs) is None
    assert skill_check.training_seconds(missing, {SMALL_PROJ: {"rank": 2.0, "primary_attribute": None, "secondary_attribute": 166}}, attrs) is None
    assert skill_check.training_seconds(missing, catalog, {"intelligence": 27}) is None


# ------------------------------------------------------------- the check
def test_without_sde_requirements_it_says_so_instead_of_calling_everything_flyable(tenant):
    _token(ALICE, "Alice")
    _share(ALICE)
    _fitting()
    assert skill_check.do_doctrine_skill_check() == {
        "sde_ready": False, "fittings": [], "characters": [], "hidden_characters": [],
    }


def test_can_fly_missing_skills_sp_and_training_time(tenant):
    _seed_sde()
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)
    _share(BOB)
    _skills(ALICE, [(FRIGATE, 1, 1, 250), (SMALL_PROJ, 3, 3, 24000), (GUNNERY, 2, 2, 1415)])
    # Bob: Frigate I, Gunnery I only, and Small Projectile part-way (2,000 of the 24,000 SP for III)
    _skills(BOB, [(FRIGATE, 1, 1, 250), (GUNNERY, 1, 1, 250), (SMALL_PROJ, 1, 1, 2000)])
    _fitting()

    out = skill_check.do_doctrine_skill_check()
    assert out["sde_ready"] is True
    (fitting,) = out["fittings"]
    assert fitting["name"] == "Rifter AC" and fitting["hull_name"] == "Rifter"
    assert fitting["doctrine_name"] == "Frigate doctrine" and fitting["required_skills"] == 3
    by_char = {c["character_id"]: c for c in fitting["characters"]}

    assert by_char[ALICE]["can_fly"] is True and by_char[ALICE]["missing"] == []
    assert by_char[ALICE]["train_seconds"] == 0

    bob = by_char[BOB]
    assert bob["can_fly"] is False
    missing = {m["skill_id"]: m for m in bob["missing"]}
    assert set(missing) == {SMALL_PROJ, GUNNERY}
    assert (missing[SMALL_PROJ]["needed"], missing[SMALL_PROJ]["have"]) == (3, 1)
    assert missing[SMALL_PROJ]["sp_remaining"] == 16000 - 2000                    # rank 2, level III = 16000 SP, minus SP already in it
    assert missing[GUNNERY]["sp_remaining"] == 1415 - 250
    assert missing[SMALL_PROJ]["name"] == "Small Projectile Turret"
    # (14000 / (27 + 21/2) + 1165 / (22 + 27/2)) minutes, rounded up to whole seconds
    expected_minutes = 14000 / 37.5 + 1165 / (22 + 27 / 2)
    assert bob["train_seconds"] == int(-(-expected_minutes * 60 // 1))


def test_training_time_is_unknown_without_synced_attributes(tenant):
    _seed_sde()
    _token(BOB, "Bob")
    _share(BOB)
    _skills(BOB, [(FRIGATE, 1, 1, 250)], attrs=None)
    _fitting()
    (bob,) = skill_check.do_doctrine_skill_check()["fittings"][0]["characters"]
    assert bob["can_fly"] is False and bob["train_seconds"] is None
    assert {m["skill_id"] for m in bob["missing"]} == {SMALL_PROJ, GUNNERY}


def test_a_skill_without_a_known_rank_is_listed_but_gets_no_estimate(tenant):
    _seed_sde()
    with storage.connect() as conn:
        conn.execute("DELETE FROM sde_skill_meta WHERE skill_id = ?", (GUNNERY,))
    _token(BOB, "Bob")
    _share(BOB)
    _skills(BOB, [(FRIGATE, 1, 1, 250), (SMALL_PROJ, 3, 3, 24000)])
    _fitting()
    (bob,) = skill_check.do_doctrine_skill_check()["fittings"][0]["characters"]
    (gunnery,) = bob["missing"]
    assert gunnery["skill_id"] == GUNNERY and gunnery["sp_remaining"] is None
    assert bob["train_seconds"] is None


def test_only_characters_shared_with_skills_are_checked_and_the_rest_are_reported(tenant):
    _seed_sde()
    _token(ALICE, "Alice")
    _token(CAROL, "Carol")
    _share(ALICE)
    _skills(ALICE, [])
    _skills(CAROL, [(FRIGATE, 5, 5, 256000)])          # data exists, but Carol is not shared with Skills
    _fitting()
    out = skill_check.do_doctrine_skill_check()
    assert [c["character_id"] for c in out["characters"]] == [ALICE]
    assert [c["character_id"] for c in out["hidden_characters"]] == [CAROL]
    assert [c["character_id"] for c in out["fittings"][0]["characters"]] == [ALICE]


def test_inactive_fittings_and_other_doctrines_are_filtered(tenant):
    _seed_sde()
    _token(ALICE, "Alice")
    _share(ALICE)
    _skills(ALICE, [])
    d1, _ = _fitting("Active one")
    _fitting("Inactive one", active=False, doctrine_id=d1)
    d2, _ = _fitting("Other doctrine fit")
    names = {f["name"] for f in skill_check.do_doctrine_skill_check()["fittings"]}
    assert names == {"Active one", "Other doctrine fit"}
    only = skill_check.do_doctrine_skill_check(doctrine_id=d2)["fittings"]
    assert [f["name"] for f in only] == ["Other doctrine fit"]
    with pytest.raises(ActionError, match="does not exist"):
        skill_check.do_doctrine_skill_check(doctrine_id="00000000-0000-0000-0000-000000000abc")


def test_drones_and_hull_only_fittings_are_covered(tenant):
    _seed_sde()
    _token(ALICE, "Alice")
    _share(ALICE)
    _skills(ALICE, [(FRIGATE, 1, 1, 250)])
    _fitting("Drone boat", items=((HAMMERHEAD, 5),))
    _fitting("Bare hull", items=())
    by_name = {f["name"]: f for f in skill_check.do_doctrine_skill_check()["fittings"]}
    assert by_name["Bare hull"]["characters"][0]["can_fly"] is True                 # Frigate I is all it needs
    drone = by_name["Drone boat"]["characters"][0]
    assert drone["can_fly"] is False and {m["skill_id"] for m in drone["missing"]} == {SPEC_DRONES}


def test_an_untrained_skill_counts_as_level_zero_with_all_its_sp_missing(tenant):
    _seed_sde()
    _token(ALICE, "Alice")
    _share(ALICE)
    _skills(ALICE, [])
    _fitting("Bare hull", items=())
    (alice,) = skill_check.do_doctrine_skill_check()["fittings"][0]["characters"]
    (m,) = alice["missing"]
    assert (m["skill_id"], m["have"], m["needed"], m["sp_remaining"]) == (FRIGATE, 0, 1, 250)


# ------------------------------------------------------------------ route
_TENANT = "00000000-0000-0000-0000-000000000ee1"
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


def test_the_route_needs_both_the_skills_and_the_doctrine_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    seen = {}
    monkeypatch.setattr(skill_check, "do_doctrine_skill_check",
                        lambda doctrine_id=None: seen.update(d=doctrine_id) or {"sde_ready": True, "fittings": []})
    storage.add_tenant_registry_entry(_TENANT, 1, character_name="Some Character")
    assert _client.get("/api/char-skills/doctrine-check", cookies=_cookie()).status_code == 403   # neither grant
    storage.set_tool_grant(1, "doctrine", _TENANT)
    assert _client.get("/api/char-skills/doctrine-check", cookies=_cookie()).status_code == 403   # doctrine alone
    storage.set_tool_grant(1, "char_skills", _TENANT)
    ok = _client.get("/api/char-skills/doctrine-check", params={"doctrine_id": "abc"}, cookies=_cookie())
    assert ok.status_code == 200 and seen == {"d": "abc"}
    storage.revoke_tool_grant(1, "doctrine")
    assert _client.get("/api/char-skills/doctrine-check", cookies=_cookie()).status_code == 403   # skills alone


def test_the_route_works_with_the_gate_off_and_reports_action_errors(monkeypatch):
    monkeypatch.setattr(skill_check, "do_doctrine_skill_check", lambda doctrine_id=None: {"sde_ready": True, "fittings": []})
    assert _client.get("/api/char-skills/doctrine-check").status_code == 200

    def boom(doctrine_id=None):
        raise ActionError("nope")
    monkeypatch.setattr(skill_check, "do_doctrine_skill_check", boom)
    resp = _client.get("/api/char-skills/doctrine-check")
    assert resp.status_code == 400 and resp.json()["detail"] == "nope"
