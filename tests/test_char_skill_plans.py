"""Character Management phase 9: skill plans (storage, expansion, import/export,
progress, tenant isolation, route grant)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.character_management import skill_plan_actions as pa
from eve_trader.esi_data.access import read_esi

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant, tenant_pair,
)
from .test_char_info import _TENANT, _cookie, _enable_gate, _gate_tables, _provision  # noqa: F401
from .test_char_skill_check import (  # noqa: F401
    ALICE, BOB, FRIGATE, GUNNERY, SMALL_PROJ, _seed_sde, _skills, _tables_exist, _token,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

_TABLES = ("skill_plan_items", "skill_plans", "character_skills", "character_attributes", "esi_sharing",
           "esi_freshness", "tenant_tokens")
_SDE = ("sde_types", "sde_groups", "sde_skill_meta", "sde_skill_requirements")
_client = TestClient(create_app())


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES, *_SDE)
    storage.get_sde_type.cache_clear()
    yield
    pg_helpers.wipe_tables(*_TABLES, *_SDE)
    storage.get_sde_type.cache_clear()


def _share(cid, tool="char_skill_plans"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, 'skills', ?) ON CONFLICT DO NOTHING", (cid, tool))


def _names(plan):
    return [(s["name"], s["level"]) for s in plan["steps"]]


# ------------------------------------------------------------------- CRUD
def test_create_rename_list_and_delete(tenant):
    plan = pa.do_create_plan("  Frigates  ", "  desc ")
    assert plan["name"] == "Frigates" and plan["description"] == "desc" and plan["steps"] == []
    pid = plan["plan_id"]
    assert pa.do_update_plan(pid, "Better", "x")["name"] == "Better"
    listed = pa.do_list_plans()["plans"]
    assert [(p["plan_id"], p["name"], p["step_count"]) for p in listed] == [(pid, "Better", 0)]
    assert pa.do_delete_plan(pid) == {"deleted": pid}
    assert pa.do_list_plans()["plans"] == []
    with pytest.raises(ActionError, match="does not exist"):
        pa.do_get_plan(pid)


@pytest.mark.parametrize("name,description", [("", ""), ("   ", ""), ("x" * 101, ""), ("ok", "y" * 501)])
def test_invalid_names_and_descriptions_are_rejected(tenant, name, description):
    with pytest.raises(ActionError):
        pa.do_create_plan(name, description)


def test_the_plan_count_is_limited(tenant, monkeypatch):
    monkeypatch.setattr(pa, "MAX_PLANS", 2)
    pa.do_create_plan("a")
    pa.do_create_plan("b")
    with pytest.raises(ActionError, match="already 2 plans"):
        pa.do_create_plan("c")


def test_plans_are_tenant_isolated_and_deleting_cascades(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        _seed_sde()
        pid = pa.do_create_plan("mine")["plan_id"]
        pa.do_add_skill(pid, GUNNERY, 2)
    with storage.tenant_context(b):
        assert pa.do_list_plans()["plans"] == []
        with pytest.raises(ActionError, match="does not exist"):
            pa.do_get_plan(pid)
        with pytest.raises(ActionError):
            pa.do_delete_plan(pid)
    with storage.tenant_context(a):
        assert len(pa.do_get_plan(pid)["steps"]) == 2
        pa.do_delete_plan(pid)
        assert storage.load_skill_plan_items(pid) == []


# ------------------------------------------------------------------ steps
def test_adding_a_skill_pulls_in_levels_and_prerequisites(tenant):
    _seed_sde()
    pid = pa.do_create_plan("p")["plan_id"]
    out = pa.do_add_skill(pid, SMALL_PROJ, 3)
    assert _names(out) == [("Gunnery", 1), ("Gunnery", 2), ("Small Projectile Turret", 1),
                           ("Small Projectile Turret", 2), ("Small Projectile Turret", 3)]
    assert out["added"] == 5 and out["steps"][0]["level_label"] == "I" and out["steps"][2]["rank"] == 2.0
    again = pa.do_add_skill(pid, GUNNERY, 2)
    assert again["added"] == 0 and len(again["steps"]) == 5


def test_add_validation(tenant):
    with pytest.raises(ActionError, match="not loaded"):
        pa.do_add_skill(pa.do_create_plan("p")["plan_id"], GUNNERY, 1)
    _seed_sde()
    pid = pa.do_list_plans()["plans"][0]["plan_id"]
    for skill, level in ((GUNNERY, 0), (GUNNERY, 6), (GUNNERY, "x"), (99999, 1), ("x", 1), (587, 1)):    # 587 is a ship
        with pytest.raises(ActionError):
            pa.do_add_skill(pid, skill, level)
    with pytest.raises(ActionError, match="does not exist"):
        pa.do_add_skill(424242, GUNNERY, 1)


def test_the_step_limit_is_enforced(tenant, monkeypatch):
    _seed_sde()
    pid = pa.do_create_plan("p")["plan_id"]
    monkeypatch.setattr(pa.logic, "MAX_STEPS", 3)
    with pytest.raises(ActionError, match="at most 3 steps"):
        pa.do_add_skill(pid, SMALL_PROJ, 3)
    assert pa.do_get_plan(pid)["steps"] == []                 # nothing half-written


def test_removing_a_step_removes_what_needed_it(tenant):
    _seed_sde()
    pid = pa.do_create_plan("p")["plan_id"]
    pa.do_add_skill(pid, SMALL_PROJ, 3)
    out = pa.do_remove_step(pid, SMALL_PROJ, 2)
    assert _names(out) == [("Gunnery", 1), ("Gunnery", 2), ("Small Projectile Turret", 1)] and out["removed"] == 2
    out = pa.do_remove_step(pid, GUNNERY, 2)                   # Small Projectile I needed Gunnery II
    assert _names(out) == [("Gunnery", 1)] and out["removed"] == 2
    with pytest.raises(ActionError, match="not in the plan"):
        pa.do_remove_step(pid, GUNNERY, 5)


def test_reorder_accepts_only_valid_permutations(tenant):
    _seed_sde()
    pid = pa.do_create_plan("p")["plan_id"]
    pa.do_add_skill(pid, SMALL_PROJ, 1)
    pa.do_add_skill(pid, FRIGATE, 1)
    steps = [{"skill_id": s["skill_id"], "level": s["level"]} for s in pa.do_get_plan(pid)["steps"]]
    assert [(s["skill_id"], s["level"]) for s in steps] == [(GUNNERY, 1), (GUNNERY, 2), (SMALL_PROJ, 1), (FRIGATE, 1)]
    frigate_first = [steps[3], *steps[:3]]
    assert [s["skill_id"] for s in pa.do_reorder(pid, frigate_first)["steps"]][0] == FRIGATE
    with pytest.raises(ActionError, match="before something it needs"):
        pa.do_reorder(pid, [steps[2], steps[0], steps[1], steps[3]])
    with pytest.raises(ActionError, match="exactly the plan's steps"):
        pa.do_reorder(pid, steps[:3])
    with pytest.raises(ActionError):
        pa.do_reorder(pid, [{"skill_id": "x"}])
    with pytest.raises(ActionError):
        pa.do_reorder(pid, "nope")


# ----------------------------------------------------------- import / export
def test_import_resolves_names_adds_prerequisites_and_reports_the_rest(tenant):
    _seed_sde()
    out = pa.do_create_plan("Imported", text="small projectile turret III\nMinmatar Frigate 1\nBogus Skill V\nnot a line\n")
    assert _names(out) == [("Gunnery", 1), ("Gunnery", 2), ("Small Projectile Turret", 1),
                           ("Small Projectile Turret", 2), ("Small Projectile Turret", 3), ("Minmatar Frigate", 1)]
    assert sorted(out["unresolved"]) == ["Bogus Skill V", "not a line"]
    assert out["steps_added_for_prerequisites"] == 4          # Gunnery I/II and Small Projectile I/II were not named


def test_import_needs_the_sde_and_is_bounded(tenant):
    with pytest.raises(ActionError, match="not loaded"):
        pa.do_create_plan("x", text="Gunnery I")
    _seed_sde()
    with pytest.raises(ActionError, match="too long"):
        pa.do_create_plan("x", text="Gunnery I\n" * 10_000)
    assert pa.do_list_plans()["plans"] == []                  # a refused import leaves no empty plan behind


def test_export_round_trips_through_import(tenant):
    _seed_sde()
    pid = pa.do_create_plan("p", text="Small Projectile Turret V")["plan_id"]
    exported = pa.do_export_plan(pid)
    assert exported["text"].splitlines()[0] == "Gunnery I" and exported["text"].splitlines()[-1] == "Small Projectile Turret V"
    again = pa.do_create_plan("copy", text=exported["text"])
    assert _names(again) == _names(pa.do_get_plan(pid)) and again["unresolved"] == []


def test_skill_search_matches_names_prefix_first_and_escapes_wildcards(tenant):
    _seed_sde()
    assert pa.do_search_skills("g") == {"skills": []}                          # too short to search
    found = [s["name"] for s in pa.do_search_skills("gun")["skills"]]
    assert found == ["Gunnery"]
    both = [s["name"] for s in pa.do_search_skills("in")["skills"]]
    assert both[0] == "Minmatar Frigate" or "Minmatar Frigate" in both        # substring match works
    assert pa.do_search_skills("%%")["skills"] == [] and pa.do_search_skills("g_")["skills"] == []
    assert [s["name"] for s in pa.do_search_skills("MINMATAR")["skills"]] == ["Minmatar Frigate"]     # case-insensitive


# ---------------------------------------------------------------- progress
def test_progress_counts_done_steps_and_estimates_the_rest(tenant, monkeypatch):
    _seed_sde()
    monkeypatch.setattr(pa.fields, "token_characters", lambda: [
        {"character_id": ALICE, "character_name": "Alice"}, {"character_id": BOB, "character_name": "Bob"}])
    pid = pa.do_create_plan("p", text="Small Projectile Turret II")["plan_id"]       # Gunnery I,II + SP I,II
    _token(ALICE, "Alice")
    _share(ALICE)
    _skills(ALICE, [(GUNNERY, 2, 2, 1415), (SMALL_PROJ, 1, 1, 500)])               # Gunnery II done, SP I done, SP II open
    out = pa.do_plan_progress(pid)
    (alice,) = out["characters"]
    assert alice["steps_total"] == 4 and alice["steps_done"] == 3
    # Small Projectile II, rank 2: SP(2) = 2829, the character has 500 (below SP(1)=500? equal) -> 2829 - 500
    assert alice["sp_remaining"] == 2829 - 500
    assert alice["train_seconds"] == int(-(-(alice["sp_remaining"] / (27 + 21 / 2)) * 60 // 1))
    assert [n["name"] for n in alice["next_steps"]] == ["Small Projectile Turret"]
    assert [h["character_id"] for h in out["hidden_characters"]] == [BOB]


def test_progress_reads_only_what_is_shared_with_skill_plans(tenant, monkeypatch):
    _seed_sde()
    monkeypatch.setattr(pa.fields, "token_characters", lambda: [{"character_id": ALICE, "character_name": "Alice"}])
    pid = pa.do_create_plan("p", text="Gunnery I")["plan_id"]
    _token(ALICE, "Alice")
    _share(ALICE, tool="char_skills")                        # Skills sharing does not open Skill Plans
    _skills(ALICE, [(GUNNERY, 1, 1, 250)])
    assert read_esi("skills", "char_skill_plans") == []
    out = pa.do_plan_progress(pid)
    assert out["characters"] == [] and [h["character_id"] for h in out["hidden_characters"]] == [ALICE]


def test_progress_marks_a_shared_but_unsynced_character(tenant, monkeypatch):
    _seed_sde()
    monkeypatch.setattr(pa.fields, "token_characters", lambda: [{"character_id": ALICE, "character_name": "Alice"}])
    pid = pa.do_create_plan("p", text="Gunnery I")["plan_id"]
    _token(ALICE, "Alice")
    _share(ALICE)
    assert pa.do_plan_progress(pid)["characters"][0]["synced"] is False


def test_progress_has_no_estimate_without_attributes_or_ranks(tenant, monkeypatch):
    _seed_sde()
    monkeypatch.setattr(pa.fields, "token_characters", lambda: [{"character_id": ALICE, "character_name": "Alice"}])
    pid = pa.do_create_plan("p", text="Gunnery II")["plan_id"]
    _token(ALICE, "Alice")
    _share(ALICE)
    _skills(ALICE, [], attrs=None)
    row = pa.do_plan_progress(pid)["characters"][0]
    assert row["steps_done"] == 0 and row["train_seconds"] is None and row["sp_remaining"] == 1415
    with storage.connect() as conn:
        conn.execute("DELETE FROM sde_skill_meta WHERE skill_id = ?", (GUNNERY,))
    row = pa.do_plan_progress(pid)["characters"][0]
    assert row["sp_remaining"] is None and row["train_seconds"] is None


def test_a_finished_plan_needs_no_time(tenant, monkeypatch):
    _seed_sde()
    monkeypatch.setattr(pa.fields, "token_characters", lambda: [{"character_id": ALICE, "character_name": "Alice"}])
    pid = pa.do_create_plan("p", text="Gunnery II")["plan_id"]
    _token(ALICE, "Alice")
    _share(ALICE)
    _skills(ALICE, [(GUNNERY, 5, 5, 256000)])
    row = pa.do_plan_progress(pid)["characters"][0]
    assert (row["steps_done"], row["sp_remaining"], row["train_seconds"], row["next_steps"]) == (2, 0, 0, [])


# ------------------------------------------------------------------ routes
def test_the_routes_need_the_char_skill_plans_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(pa, "do_list_plans", lambda: {"plans": []})
    _provision(tools=("characters", "char_skills"))
    assert _client.get("/api/char-skill-plans/plans", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "char_skill_plans", _TENANT)
    ok = _client.get("/api/char-skill-plans/plans", cookies=_cookie())
    assert ok.status_code == 200 and ok.json() == {"plans": []}


def test_routes_pass_arguments_and_convert_action_errors(monkeypatch):
    seen = {}
    monkeypatch.setattr(pa, "do_create_plan", lambda **kw: seen.update(create=kw) or {"plan_id": 1})
    monkeypatch.setattr(pa, "do_add_skill", lambda **kw: seen.update(add=kw) or {})
    monkeypatch.setattr(pa, "do_remove_step", lambda **kw: seen.update(remove=kw) or {})
    monkeypatch.setattr(pa, "do_reorder", lambda **kw: seen.update(order=kw) or {})
    assert _client.post("/api/char-skill-plans/plans", json={"name": "n", "text": "Gunnery I"}).status_code == 200
    assert _client.post("/api/char-skill-plans/plans/3/steps", json={"skill_id": 5, "level": 2}).status_code == 200
    assert _client.delete("/api/char-skill-plans/plans/3/steps/5/2").status_code == 200
    assert _client.put("/api/char-skill-plans/plans/3/order", json={"order": [{"skill_id": 5, "level": 2}]}).status_code == 200
    assert seen == {
        "create": {"name": "n", "description": "", "text": "Gunnery I"},
        "add": {"plan_id": 3, "skill_id": 5, "level": 2}, "remove": {"plan_id": 3, "skill_id": 5, "level": 2},
        "order": {"plan_id": 3, "order": [{"skill_id": 5, "level": 2}]},
    }

    def boom(**kw):
        raise ActionError("nope")
    monkeypatch.setattr(pa, "do_get_plan", boom)
    r = _client.get("/api/char-skill-plans/plans/1")
    assert r.status_code == 400 and r.json()["detail"] == "nope"
    assert _client.post("/api/char-skill-plans/plans", json={"name": "x" * 500}).status_code == 422
