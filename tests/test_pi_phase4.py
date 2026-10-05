"""PI phase 4 (real colonies, monitor, calibration, alerts) - the pure parts:
planets fetcher, registry wiring, colony views/actions, calibrated yields and
the alert decisions. Storage, accessor and runner are in
test_pi_phase4_storage.py (Postgres)."""
import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.alerts import logic
from eve_trader.esi_client import ESIError
from eve_trader.esi_data import access, fetchers, stale
from eve_trader.esi_data.registry import OWNED_DATA_KINDS
from eve_trader.pi import actions as pa
from eve_trader.pi import static
from eve_trader.pi.config import PiConfig

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def sd():
    return static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})


def _iso(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


def _esi_colony(expiry_hours=10.0, program_hours=48.0, last_update_hours=-2.0):
    """A small Barren colony as ESI returns it: CC, launchpad, one ECU."""
    return {
        "pins": [
            {"pin_id": 1, "type_id": 2524, "latitude": 1.0, "longitude": 1.0},
            {"pin_id": 2, "type_id": 2544, "latitude": 1.1, "longitude": 1.0,
             "contents": [{"type_id": 2272, "amount": 100}]},
            {"pin_id": 3, "type_id": 2848, "latitude": 1.2, "longitude": 1.0,
             "install_time": _iso(expiry_hours - program_hours), "expiry_time": _iso(expiry_hours),
             "last_cycle_start": _iso(-1),
             "extractor_details": {"product_type_id": 2272, "qty_per_cycle": 600, "cycle_time": 1800,
                                   "heads": [{"head_id": 0, "latitude": 1.2, "longitude": 1.1},
                                             {"head_id": 1, "latitude": 1.2, "longitude": 0.9}]}},
        ],
        "links": [{"source_pin_id": 1, "destination_pin_id": 2, "link_level": 0},
                  {"source_pin_id": 2, "destination_pin_id": 3, "link_level": 0}],
        "routes": [],
    }


def _row(cid=1, planet_id=40000001, **kw):
    return {"owner_type": "character", "owner_id": cid, "planet_id": planet_id, "planet_type": "barren",
            "solar_system_id": 30000142, "upgrade_level": 5, "num_pins": 3,
            "last_update": _iso(-2), "layout": _esi_colony(**kw)}


@pytest.fixture
def sde_lookups(monkeypatch):
    monkeypatch.setattr(storage, "get_pi_planet", lambda pid: (pid, "Jita IV", 30000142, 2016, 5000.0))
    monkeypatch.setattr(storage, "get_solar_system", lambda sid: (sid, "Jita", 0.95, 10000002))
    pa._planet_zone_cache.clear()
    yield
    pa._planet_zone_cache.clear()


# ----------------------------------------------------------------- registry wiring (P-49)
def test_every_snapshot_kind_has_fetcher_stale_table_and_reader(monkeypatch):
    @contextlib.contextmanager
    def no_db():
        yield object()

    monkeypatch.setattr(access, "_shared_owner_ids", lambda *a, **k: [])
    monkeypatch.setattr(storage, "connect", no_db)
    for kind in OWNED_DATA_KINDS:
        if kind.live_only:
            continue
        assert (kind.key, "character") in fetchers.FETCHERS, kind.key
        assert ("corporation" in stale._KIND_TABLES.get(kind.key, {})) == bool(kind.corporation_scope), kind.key
        assert "character" in stale._KIND_TABLES[kind.key], kind.key
        # a reader branch exists: an unknown kind would raise AccessorError
        access.read_esi(kind.key, kind.consuming_tools[0])


def test_planets_kind_definition():
    kind = next(k for k in OWNED_DATA_KINDS if k.key == "planets")
    assert kind.label == "Planetary Industry" and kind.character_scope == "esi-planets.manage_planets.v1"
    assert kind.consuming_tools == ("pi", "char_alerts") and kind.schedule_mode == "on_demand"
    assert kind.corporation_scope is None and not kind.live_only


def test_planets_tables_are_in_both_clear_allowlists():
    import inspect

    src = inspect.getsource(storage.delete_owner_snapshot_rows)
    assert src.count("character_pi_colonies") == 2


# ----------------------------------------------------------------- fetcher
class _Client:
    def __init__(self, missing=(), fail=None):
        self.missing, self.fail, self.detail_calls = set(missing), fail, []

    def character_planets(self, cid, auth_role):
        return [{"planet_id": 11, "planet_type": "barren", "solar_system_id": 5, "upgrade_level": 4,
                 "num_pins": 3, "last_update": "2026-10-04T10:00:00Z"},
                {"planet_id": 12, "planet_type": "lava", "solar_system_id": 6, "upgrade_level": 5,
                 "num_pins": 1, "last_update": "2026-10-04T09:00:00Z"}]

    def character_planet(self, cid, planet_id, auth_role):
        self.detail_calls.append(planet_id)
        if planet_id in self.missing:
            raise ESIError("HTTP 404 for https://esi/x: not found")
        if self.fail:
            raise ESIError(self.fail)
        return {"pins": [{"pin_id": planet_id}], "links": [], "routes": []}


def test_fetcher_skips_a_404_colony_and_stores_the_rest(monkeypatch):
    stored = []
    monkeypatch.setattr(storage, "replace_character_pi_colonies", lambda cid, rows: stored.append((cid, rows)))
    out = fetchers.fetch_character_planets(_Client(missing={12}), 7, "esi:7", "Alice")
    assert out == {"written": 1, "skipped": 1}
    cid, rows = stored[0]
    assert cid == 7 and [r[0] for r in rows] == [11]
    assert rows[0][1:6] == ("barren", 5, 4, 3, "2026-10-04T10:00:00Z")
    assert rows[0][6] == {"pins": [{"pin_id": 11}], "links": [], "routes": []}


def test_fetcher_propagates_other_errors_without_writing(monkeypatch):
    stored = []
    monkeypatch.setattr(storage, "replace_character_pi_colonies", lambda cid, rows: stored.append(rows))
    with pytest.raises(ESIError):
        fetchers.fetch_character_planets(_Client(fail="HTTP 500 for x"), 7, "esi:7", "Alice")
    assert stored == []


# ----------------------------------------------------------------- colony views / actions
def test_colony_views_project_a_converted_colony(sd, sde_lookups):
    (v,) = pa.colony_views(sd, [_row()], PiConfig(), NOW)
    assert v["planet_name"] == "Jita IV" and v["zone"] == "highsec" and v["planet_type_id"] == 2016
    assert v["template_available"] is True
    ext = v["projection"]["extractors"][0]
    assert ext["pin_id"] == 3 and ext["hours_left"] == pytest.approx(10.0) and not ext["expired"]
    assert v["projection"]["age_hours"] == pytest.approx(2.0)
    assert v["colony"].layout.cc_level == 5 and len(v["colony"].layout.pins) == 2


def test_do_colonies_reports_each_character_state_and_stores_samples(sd, sde_lookups, monkeypatch):
    import eve_trader.auth as auth_mod
    import eve_trader.esi_data as esi_data
    from eve_trader.character_management import fields

    monkeypatch.setattr(pa, "_static", lambda: sd)
    monkeypatch.setattr(auth_mod, "TokenManager", lambda cfg: object())
    monkeypatch.setattr(fields, "token_characters", lambda: [
        {"character_id": 1, "character_name": "Alice"}, {"character_id": 2, "character_name": "Bob"},
        {"character_id": 3, "character_name": "Cara"}, {"character_id": 4, "character_name": "Dan"}])
    gates = {1: None, 2: "not_shared", 3: "reauth_needed", 4: None}
    monkeypatch.setattr(fields, "gate", lambda kind, tool, cid, tokens: gates[cid])
    monkeypatch.setattr(fields, "freshness_by_kind", lambda cid: (
        {"planets": {"last_success_at": "2026-10-04T10:00:00+00:00", "last_error": None}} if cid == 1 else {}))
    monkeypatch.setattr(esi_data, "read_esi", lambda kind, tool, **kw: [_row(cid=1)])
    saved = []
    monkeypatch.setattr(storage, "upsert_pi_yield_samples", lambda rows: saved.extend(rows))

    out = pa.do_colonies(PiConfig())
    states = {c["character_id"]: c["state"] for c in out["characters"]}
    assert states == {1: "ok", 2: "not_shared", 3: "reauth_needed", 4: "not_synced"}
    assert out["shared"] is True
    alice = out["characters"][0]
    assert len(alice["colonies"]) == 1 and "colony" not in alice["colonies"][0]
    json.dumps(out)                                       # JSON-safe
    assert len(saved) == 1 and saved[0]["character_id"] == 1 and saved[0]["planet_type_id"] == 2016
    assert saved[0]["security"] == 0.95 and saved[0]["heads"] == 2


def test_do_colonies_with_nothing_shared(sd, sde_lookups, monkeypatch):
    import eve_trader.auth as auth_mod
    import eve_trader.esi_data as esi_data
    from eve_trader.character_management import fields

    monkeypatch.setattr(pa, "_static", lambda: sd)
    monkeypatch.setattr(auth_mod, "TokenManager", lambda cfg: object())
    monkeypatch.setattr(fields, "token_characters", lambda: [{"character_id": 2, "character_name": "Bob"}])
    monkeypatch.setattr(fields, "gate", lambda *a: "not_shared")

    def boom(*a, **k):
        raise AssertionError("must not read")
    monkeypatch.setattr(esi_data, "read_esi", lambda *a, **k: [])
    monkeypatch.setattr(storage, "upsert_pi_yield_samples", boom)
    out = pa.do_colonies(PiConfig())
    assert out["shared"] is False and out["characters"][0]["colonies"] == []


def test_do_colony_template_validates_and_optionally_saves(sd, sde_lookups, monkeypatch):
    import eve_trader.esi_data as esi_data

    monkeypatch.setattr(pa, "_static", lambda: sd)
    monkeypatch.setattr(esi_data, "read_esi", lambda kind, tool, **kw: [_row(cid=1)])
    saves = []
    monkeypatch.setattr(storage, "save_pi_template", lambda *a: saves.append(a) or 9)
    monkeypatch.setattr(storage, "get_pi_template", lambda tid: {"template_id": tid, "name": "x", "template": {}})

    r = pa.do_colony_template(1, 40000001, cfg=PiConfig())
    assert r["saved"] is None and saves == []
    assert r["template"]["Cmt"] == "Jita IV" and r["template"]["CmdCtrLv"] == 5
    assert "analysis" in r and r["template_json"]

    r = pa.do_colony_template(1, 40000001, save=True, name="My colony", cfg=PiConfig())
    assert r["saved"]["template_id"] == 9
    assert saves[0][0] == "My colony" and saves[0][6] == "esi"

    with pytest.raises(ActionError):
        pa.do_colony_template(1, 12345, cfg=PiConfig())


# ----------------------------------------------------------------- calibration
def _sample(pid, per_head=10.0, hours=72.0, planet_id=40000001, p0=2272):
    return {"character_id": 1, "planet_id": planet_id, "pin_id": pid, "install_time": NOW, "p0_type_id": p0,
            "planet_type_id": 2016, "security": 0.95, "heads": 4, "program_hours": hours,
            "per_head_per_hour": per_head}


@pytest.fixture
def calibration(monkeypatch, sde_lookups):
    pa.clear_calibration_cache()
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    yield
    pa.clear_calibration_cache()


def test_calibrated_yield_replaces_the_default_from_three_samples(monkeypatch, calibration):
    cfg = PiConfig()
    samples = [_sample(1, 9.0), _sample(2, 10.0)]
    monkeypatch.setattr(storage, "list_pi_yield_samples", lambda: samples)
    assert pa._assumptions(cfg, "highsec").yield_per_head == cfg.pi_yield_highsec   # 2 samples: default
    pa.clear_calibration_cache()
    samples.append(_sample(3, 11.0))
    a = pa._assumptions(cfg, "highsec")
    assert a.yield_per_head == pytest.approx(10.0)
    assert pa._assumptions(cfg, "lowsec").yield_per_head == cfg.pi_yield_lowsec     # other zone untouched
    assert pa._assumptions(cfg, "highsec", yield_override=3.0).yield_per_head == 3.0


def test_calibration_is_cached_until_cleared(monkeypatch, calibration):
    calls = []
    monkeypatch.setattr(storage, "list_pi_yield_samples",
                        lambda: calls.append(1) or [_sample(i, 10.0) for i in range(3)])
    assert pa._calibrated_yield_for_zone("highsec") == pytest.approx(10.0)
    pa._calibrated_yield_for_zone("highsec")
    assert len(calls) == 1
    pa.clear_calibration_cache()
    pa._calibrated_yield_for_zone("highsec")
    assert len(calls) == 2


def test_calibration_never_raises_without_storage(monkeypatch):
    pa.clear_calibration_cache()

    def broken():
        raise RuntimeError("no tenant")
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    monkeypatch.setattr(storage, "list_pi_yield_samples", broken)
    assert pa._calibrated_yield_for_zone("highsec") is None
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)
    assert pa._calibrated_yield_for_zone("highsec") is None


def test_do_calibration_summarises_zones_and_p0(sd, monkeypatch, calibration):
    monkeypatch.setattr(pa, "_static", lambda: sd)
    monkeypatch.setattr(storage, "list_pi_yield_samples",
                        lambda: [_sample(i, 10.0) for i in range(3)] + [_sample(9, 20.0, p0=2305)])
    out = pa.do_calibration(PiConfig())
    high = next(z for z in out["zones"] if z["zone"] == "highsec")
    assert high["count"] == 4 and high["active"] is True and high["median"] == pytest.approx(10.0)
    low = next(z for z in out["zones"] if z["zone"] == "lowsec")
    assert low["count"] == 0 and low["active"] is False and low["median"] is None
    by_p0 = {p["type_id"]: p for p in out["p0"]}
    assert by_p0[2272]["count"] == 3 and by_p0[2305]["median"] == pytest.approx(20.0)
    assert by_p0[2305]["name"] == "Autotrophs"


# ----------------------------------------------------------------- alert decisions
def _colony(pid=5, expiries=(), full_h=None, inputs_h=None, name="Planet V"):
    return {"planet_id": pid, "planet_name": name, "projection": {
        "last_update": _iso(-3),
        "extractors": [{"pin_id": p, "expiry_time": _iso(h), "product_name": "Heavy Metals"}
                       for p, h in expiries],
        "full_at": _iso(full_h) if full_h is not None else None,
        "inputs_empty_at": _iso(inputs_h) if inputs_h is not None else None,
        "inputs_empty_type": "Silicon",
    }}


def test_extractor_expiry_inside_lead_is_announced_once_per_expiry():
    c = _colony(expiries=[(3, 5.0), (4, 40.0)])
    (d,) = logic.pi_decisions(logic.PI_EXTRACTOR_EXPIRY, [c], NOW, 12, {}, "Alice")
    assert d.planet_id == 5 and d.key == f"expiry:3:{_iso(5.0)}"
    assert "Alice" in d.message and "Planet V" in d.message and "expires in 5 h" in d.message
    assert "Heavy Metals" in d.message
    # already sent: suppressed
    assert logic.pi_decisions(logic.PI_EXTRACTOR_EXPIRY, [c], NOW, 12, {(5, logic.PI_EXTRACTOR_EXPIRY): d.key},
                              "Alice") == []
    # reset by the player (new expiry) -> a new key is announced again
    c2 = _colony(expiries=[(3, 8.0)])
    assert len(logic.pi_decisions(logic.PI_EXTRACTOR_EXPIRY, [c2], NOW, 12,
                                  {(5, logic.PI_EXTRACTOR_EXPIRY): d.key}, "Alice")) == 1
    # nothing due -> nothing
    assert logic.pi_decisions(logic.PI_EXTRACTOR_EXPIRY, [_colony(expiries=[(3, 40.0)])], NOW, 12, {}, "A") == []


def test_extractor_already_expired_says_expired_and_two_due_share_one_message():
    c = _colony(expiries=[(3, -4.0), (4, 2.0)])
    (d,) = logic.pi_decisions(logic.PI_EXTRACTOR_EXPIRY, [c], NOW, 12, {}, "Alice")
    assert "expired" in d.message and "expires in 2 h" in d.message
    assert d.key.count("expiry:") == 2


def test_pad_full_and_inputs_empty_are_estimates_with_hour_rounded_keys():
    c = _colony(full_h=6.4, inputs_h=3.0)
    (full,) = logic.pi_decisions(logic.PI_PAD_FULL, [c], NOW, 12, {}, "Alice")
    assert full.key == f"padfull:5:{(NOW + timedelta(hours=6)).isoformat()}"
    assert "estimated" in full.message and "colony state of" in full.message
    (empty,) = logic.pi_decisions(logic.PI_INPUTS_EMPTY, [c], NOW, 12, {}, "Alice")
    assert empty.key == f"inputs:5:{(NOW + timedelta(hours=3)).isoformat()}" and "Silicon" in empty.message
    assert logic.pi_decisions(logic.PI_PAD_FULL, [c], NOW, 12, {(5, logic.PI_PAD_FULL): full.key}, "A") == []
    assert logic.pi_decisions(logic.PI_PAD_FULL, [_colony(full_h=50.0)], NOW, 12, {}, "A") == []
    assert logic.pi_decisions(logic.PI_INPUTS_EMPTY, [_colony()], NOW, 12, {}, "A") == []


def test_pi_alert_types_are_registered():
    assert set(logic.PI_ALERT_TYPES) <= set(logic.ALERT_TYPES)
    from eve_trader.alerts import actions as aa
    assert all(aa.KIND_BY_ALERT[t] == "planets" for t in logic.PI_ALERT_TYPES)
