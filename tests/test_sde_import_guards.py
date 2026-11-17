"""SDE import plausibility checks, shrink confirmation, constant drift and the
daily freshness check job."""
from __future__ import annotations


import pytest
import requests
from fastapi.testclient import TestClient

from eve_trader import admin, scheduler, storage
from eve_trader.actions import ActionError
from eve_trader.production import constant_drift, constants, sde, sde_diff

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401


# ------------------------------------------------------------ fetch checks
class _Resp:
    def __init__(self, body: bytes):
        self.content = body

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, body: bytes):
        self.body = body

    def get(self, url, **kwargs):
        return _Resp(self.body)


def _fetch(filename: str, body: str):
    return sde._fetch_csv(_Session(body.encode("utf-8")), "https://x/", filename)


@pytest.fixture
def small_spec(monkeypatch):
    monkeypatch.setitem(sde._CSV_SPEC, "mapRegions.csv", (("regionID", "regionName"), 2))


def test_valid_file_passes(small_spec):
    rows = _fetch("mapRegions.csv", "regionID,regionName\n1,Forge\n2,Delve\n")
    assert [r["regionName"] for r in rows] == ["Forge", "Delve"]


@pytest.mark.parametrize("body, message", [
    ("", "empty"),
    ("<html><body>Maintenance</body></html>", "missing column"),
    ("regionID\n1\n2\n", "missing column"),
    ("regionID,regionName\n1,Forge\n2\n", "malformed row"),
    ("regionID,regionName\n1,Forge\n2,Delve,extra\n", "malformed row"),
    ("regionID,regionName\n1,Forge\n", "only 1 rows"),
])
def test_bad_files_are_rejected(small_spec, body, message):
    with pytest.raises(sde.SdeDumpInvalid, match=message):
        _fetch("mapRegions.csv", body)


def test_three_row_invtypes_dump_is_rejected():
    body = "typeID,groupID,typeName,volume,published,marketGroupID,metaLevel,portionSize\n" + "\n".join(
        f"{i},1,Type {i},1,1,,0,1" for i in range(3))
    with pytest.raises(sde.SdeDumpInvalid, match="only 3 rows"):
        _fetch("invTypes.csv", body)


class _StreamResp:
    def __init__(self, lines):
        self.lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StreamSession:
    def __init__(self, lines):
        self.lines = lines

    def get(self, url, **kwargs):
        return _StreamResp([line.encode("utf-8") for line in self.lines])


def test_streamed_file_with_a_cut_off_row_is_rejected():
    session = _StreamSession(["typeID,attributeID,valueInt,valueFloat", "3380,275,,1.0", "3380,180"])
    with pytest.raises(sde.SdeDumpInvalid, match="malformed row"):
        sde._fetch_skill_attributes(session, "https://x/")


def test_streamed_planets_file_without_its_columns_is_rejected():
    with pytest.raises(sde.SdeDumpInvalid, match="missing column"):
        sde._fetch_pi_planets(_StreamSession(["<html>Maintenance</html>"]), "https://x/")


def test_streamed_row_minimums_reject_a_tiny_filtered_result(monkeypatch):
    monkeypatch.setattr(sde, "_fetch_csv", lambda session, base, filename: [])
    monkeypatch.setattr(sde, "_dump_etag", lambda *a, **k: "etag")
    monkeypatch.setattr(sde, "_fetch_attribute_ids", lambda session, base: {})
    monkeypatch.setattr(sde, "_fetch_skill_attributes", lambda *a, **k: ({3380: {275: 1.0}}, {}, {}))
    monkeypatch.setattr(sde, "_fetch_pi_planets", lambda session, base: [])
    with pytest.raises(sde.SdeDumpInvalid, match="dgmTypeAttributes.csv \\(skills\\)"):
        sde.fetch_sde()


def test_preview_turns_an_invalid_dump_into_an_action_error(monkeypatch):
    def boom(cfg=None, progress_callback=None):
        raise sde.SdeDumpInvalid("invTypes.csv: the file is empty")

    monkeypatch.setattr(sde, "fetch_sde", boom)
    admin._staged_sde = None
    with pytest.raises(ActionError, match="rejected"):
        admin.do_preview_sde()
    assert admin._staged_sde is None


# ------------------------------------------------------------ shrink check
def _types(n: int) -> list[tuple]:
    return [(i, 1, f"Type {i}", 1.0, 1, None, None, None, 1) for i in range(n)]


def test_shrink_warning_over_the_limit_only():
    fetched = sde.FetchedSde(types=_types(75))
    warnings = sde_diff.shrink_warnings(fetched, {"sde_types": 100})
    assert warnings == [{"table": "sde_types", "old": 100, "new": 75, "drop_percent": 25.0}]
    assert sde_diff.shrink_warnings(sde.FetchedSde(types=_types(85)), {"sde_types": 100}) == []
    # A first fill of an empty table is not a drop; a growing table neither.
    assert sde_diff.shrink_warnings(sde.FetchedSde(types=_types(10)), {"sde_types": 0}) == []
    assert sde_diff.shrink_warnings(sde.FetchedSde(types=_types(120)), {"sde_types": 100}) == []


def test_build_diff_carries_the_warnings_and_a_drift_block():
    snapshot = {"sde_types": _types(100)}
    diff = sde_diff.build_diff(sde.FetchedSde(types=_types(70)), snapshot)
    assert [w["table"] for w in diff["shrink_warnings"]] == ["sde_types"]
    assert set(diff["constant_drift"]) == {"checked", "drifts", "unchecked"}
    normal = sde_diff.build_diff(sde.FetchedSde(types=_types(100)), snapshot)
    assert normal["shrink_warnings"] == []


@pytest.fixture
def apply_env(monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 100})
    monkeypatch.setattr(storage, "get_sde_equivalence_class_map", lambda: {})
    monkeypatch.setattr(sde, "apply_sde", lambda staged: calls.append(staged) or {"sde_types": len(staged.types)})
    monkeypatch.setattr(admin, "invalidate_discover_cache", lambda all_tenants=False: None)
    monkeypatch.setattr(admin, "invalidate_ship_margin_cache", lambda all_tenants=False: None)
    monkeypatch.setattr(admin.dynamic_targets, "mark_all_invalidated", lambda: None)
    yield calls
    admin._staged_sde = None


def test_apply_without_confirmation_is_refused_and_keeps_the_staged_dump(apply_env):
    staged = sde.FetchedSde(types=_types(75))
    admin._staged_sde = staged
    with pytest.raises(ActionError, match="sde_types 100 -> 75"):
        admin.do_apply_sde()
    assert apply_env == []
    assert admin._staged_sde is staged


def test_apply_with_confirmation_writes(apply_env):
    admin._staged_sde = sde.FetchedSde(types=_types(75))
    assert admin.do_apply_sde(confirm_shrink=True) == {"sde_types": 75}
    assert len(apply_env) == 1 and admin._staged_sde is None


def test_normal_apply_needs_no_confirmation(apply_env):
    admin._staged_sde = sde.FetchedSde(types=_types(95))
    assert admin.do_apply_sde() == {"sde_types": 95}


def test_apply_route_passes_the_confirmation(monkeypatch):
    from eve_trader.api.main import app

    seen = []
    monkeypatch.setattr(admin, "do_apply_sde", lambda confirm_shrink=False: seen.append(confirm_shrink) or {})
    client = TestClient(app)
    client.post("/api/admin/sde/apply")
    client.post("/api/admin/sde/apply", json={"confirm_shrink": True})
    assert seen == [False, True]


# ------------------------------------------------------------ constant drift
_ATTR_IDS = {name: 9000 + i for i, name in enumerate(sorted(constant_drift.wanted_attribute_names()))}


def _dogma_values() -> dict[int, dict[int, float]]:
    """Dogma values that match every constant (bonuses negative in dogma, a
    decryptor modifier a delta on the invention baseline)."""
    values: dict[int, dict[int, float]] = {}
    for check in constant_drift.checks():
        value = -check.expected if check.magnitude_only else check.expected
        values.setdefault(check.type_id, {})[_ATTR_IDS[check.attribute]] = value
    return values


def test_no_drift_when_constants_match():
    result = constant_drift.compare(_ATTR_IDS, _dogma_values(), {})
    assert result["drifts"] == [] and result["unchecked"] == []
    assert result["checked"] == len(constant_drift.checks())


def test_changed_decryptor_probability_is_reported():
    values = _dogma_values()
    accelerant = constants.DECRYPTORS["Accelerant"].type_id
    values[accelerant][_ATTR_IDS[constant_drift.ATTR_DECRYPTOR_PROBABILITY]] = 1.25
    result = constant_drift.compare(_ATTR_IDS, values, {accelerant: "Accelerant Decryptor"})
    assert [(d["constant"], d["expected"], d["actual"]) for d in result["drifts"]] == [
        ("DECRYPTORS[Accelerant].probability_multiplier", 1.2, 1.25)]
    assert result["drifts"][0]["type_name"] == "Accelerant Decryptor"


def test_decryptor_stats_are_compared_as_deltas_on_the_baseline():
    # Accelerant is ME 4 / TE 14 absolute: dogma says +2 / +10.
    values = _dogma_values()
    accelerant = constants.DECRYPTORS["Accelerant"].type_id
    assert values[accelerant][_ATTR_IDS[constant_drift.ATTR_DECRYPTOR_ME]] == 2
    assert values[accelerant][_ATTR_IDS[constant_drift.ATTR_DECRYPTOR_TE]] == 10


def test_float4_noise_is_not_drift():
    values = _dogma_values()
    skill = constants.SKILL_INDUSTRY
    attr = _ATTR_IDS[constant_drift.ATTR_SKILL_INDUSTRY]
    values[skill][attr] = -4.0 * (1 + 3e-8)   # what a float4 round trip can do
    assert constant_drift.compare(_ATTR_IDS, values, {})["drifts"] == []


def test_unknown_attribute_names_are_listed_not_guessed():
    result = constant_drift.compare({}, {}, {})
    assert result["checked"] == 0 and result["drifts"] == []
    assert any("manufacturingTimeBonus" in line for line in result["unchecked"])


def test_stream_filter_keeps_only_drift_types_and_attributes():
    skill = constants.SKILL_INDUSTRY
    attr = _ATTR_IDS[constant_drift.ATTR_SKILL_INDUSTRY]
    sink: dict = {}
    rows = [
        {"typeID": str(skill), "attributeID": str(attr), "valueInt": "", "valueFloat": "-4.0"},
        {"typeID": str(skill), "attributeID": "55555", "valueInt": "1", "valueFloat": ""},
        {"typeID": "123456789", "attributeID": str(attr), "valueInt": "1", "valueFloat": ""},
    ]
    sde.split_attribute_rows(rows, frozenset(), frozenset(), frozenset(),
                             drift_type_ids=constant_drift.wanted_type_ids(),
                             drift_attribute_ids=frozenset(_ATTR_IDS.values()), drift_sink=sink)
    assert sink == {skill: {attr: -4.0}}


def test_attribute_id_lookup_failure_does_not_fail_the_preview():
    class Down:
        def get(self, url, **kwargs):
            raise requests.RequestException("down")

    assert sde._fetch_attribute_ids(Down(), "https://x/") == {}


def test_attribute_id_lookup_reads_names():
    body = "attributeID,attributeName\n77,manufacturingTimeBonus\n78,somethingElse\n"
    assert sde._fetch_attribute_ids(_Session(body.encode()), "https://x/") == {"manufacturingTimeBonus": 77}


# ------------------------------------------------------------ daily check job
@pytest.fixture
def job_env(monkeypatch):
    from contextlib import contextmanager

    state = {"stored": ("old-etag", "2000-01-01T00:00:00+00:00"), "set": [], "etag": "new-etag"}

    @contextmanager
    def _enter(tenant_id):
        yield

    monkeypatch.setattr(scheduler.tenant_scope, "enter_tenant", _enter)
    monkeypatch.setattr(storage, "get_sde_remote_check", lambda: state["stored"])

    def _set(etag, at):
        state["set"].append(etag)
        state["stored"] = (etag, at)

    monkeypatch.setattr(storage, "set_sde_remote_check", _set)
    monkeypatch.setattr(sde, "_dump_etag", lambda session, base: state["etag"])
    scheduler._sde_check_status.clear()
    scheduler._running_jobs.clear()
    yield state
    scheduler._sde_check_status.clear()


def test_job_stores_the_remote_etag_and_does_not_rerun_within_a_day(job_env):
    scheduler._check_and_run_sde_check_job()
    assert job_env["set"] == ["new-etag"]
    assert scheduler._sde_check_status["error"] is None
    scheduler._check_and_run_sde_check_job()
    assert job_env["set"] == ["new-etag"]    # checked_at is fresh: not due


def test_failed_head_backs_off_and_stores_nothing(job_env):
    job_env["etag"] = None
    scheduler._check_and_run_sde_check_job()
    assert job_env["set"] == []
    assert "ETag" in scheduler._sde_check_status["error"]
    job_env["etag"] = "new-etag"
    scheduler._check_and_run_sde_check_job()   # inside the failure backoff
    assert job_env["set"] == []


def test_job_is_skipped_when_switched_off_or_the_sde_was_never_applied(job_env, monkeypatch):
    monkeypatch.setattr(scheduler.SCHEDULER_OPERATOR_CONFIG, "sde_check_job_enabled", False)
    scheduler._check_and_run_sde_check_job()
    monkeypatch.setattr(scheduler.SCHEDULER_OPERATOR_CONFIG, "sde_check_job_enabled", True)
    job_env["stored"] = None
    scheduler._check_and_run_sde_check_job()
    assert job_env["set"] == []


def test_freshness_check_falls_back_to_the_stored_remote_etag(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: ("2026-07-15T10:00:00+00:00", "applied"))
    monkeypatch.setattr(sde, "_dump_etag", lambda session, base: None)
    monkeypatch.setattr(storage, "get_sde_remote_check", lambda: ("newer", "2026-07-16T00:00:00+00:00"))
    result = sde.check_for_newer_sde()
    assert result["newer_sde_available"] is True and result["remote_checked_at"] == "2026-07-16T00:00:00+00:00"
    monkeypatch.setattr(storage, "get_sde_remote_check", lambda: ("applied", "2026-07-16T00:00:00+00:00"))
    assert sde.check_for_newer_sde()["newer_sde_available"] is False


@pg_helpers.postgres_required()
def test_remote_check_columns_round_trip(tenant):
    pg_helpers.wipe_tables("sde_refresh_state")
    assert storage.get_sde_remote_check() is None
    storage.set_sde_refresh_state("2026-07-16T10:00:00+00:00", "etag-1")
    assert storage.get_sde_remote_check() == (None, None)
    storage.set_sde_remote_check("etag-2", "2026-07-17T00:00:00+00:00")
    assert storage.get_sde_remote_check() == ("etag-2", "2026-07-17T00:00:00+00:00")
    # An apply keeps the stored remote ETag (it is compared with the new applied one).
    storage.set_sde_refresh_state("2026-07-18T10:00:00+00:00", "etag-2")
    assert storage.get_sde_remote_check() == ("etag-2", "2026-07-17T00:00:00+00:00")
