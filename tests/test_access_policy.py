"""Allowlist policy: exemptions, staleness, the 6h window, single-flight."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from eve_trader import access_policy, storage
from eve_trader.access_policy import Verdict


def _stored(*, corp=5, alliance=None, age=None, suspended=False, checked=True):
    checked_at = None
    if checked:
        checked_at = datetime.now(timezone.utc) - (age if age is not None else timedelta(hours=1))
    return {
        "corporation_id": corp,
        "alliance_id": alliance,
        "affiliation_checked_at": checked_at,
        "access_suspended": suspended,
    }


def test_admin_is_exempt_and_does_not_fetch(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(access_policy, "fetch_affiliation", lambda cid: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(storage, "allowlist_is_empty", lambda: False)
    result = access_policy.evaluate_registered(1, ["admin", "trading"], _stored())
    assert result.verdict == Verdict.OK
    assert calls["n"] == 0


def test_empty_allowlist_skips_the_check(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(access_policy, "fetch_affiliation", lambda cid: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(storage, "allowlist_is_empty", lambda: True)
    result = access_policy.evaluate_registered(1, ["trading"], _stored())
    assert result.verdict == Verdict.OK
    assert calls["n"] == 0


def test_fresh_fetch_allowed_and_denied(monkeypatch):
    monkeypatch.setattr(storage, "allowlist_is_empty", lambda: False)
    monkeypatch.setattr(storage, "allowlist_contains", lambda corp, alliance: corp == 5 or alliance == 9)
    monkeypatch.setattr(access_policy, "fetch_affiliation", lambda cid: (5, None))
    assert access_policy.evaluate_registered(1, ["trading"], None).verdict == Verdict.OK
    monkeypatch.setattr(access_policy, "fetch_affiliation", lambda cid: (8, 9))
    assert access_policy.evaluate_registered(1, ["trading"], None).verdict == Verdict.OK
    monkeypatch.setattr(access_policy, "fetch_affiliation", lambda cid: (8, None))
    denied = access_policy.evaluate_registered(1, ["trading"], None)
    assert denied.verdict == Verdict.SUSPENDED
    assert denied.fresh_affiliation == (8, None)


def test_esi_failure_uses_stored_affiliation_up_to_seven_days(monkeypatch):
    monkeypatch.setattr(storage, "allowlist_is_empty", lambda: False)
    monkeypatch.setattr(storage, "allowlist_contains", lambda corp, alliance: corp == 5)
    monkeypatch.setattr(access_policy, "fetch_affiliation", lambda cid: None)
    fresh = access_policy.evaluate_registered(1, ["trading"], _stored(age=timedelta(days=6)))
    assert fresh.verdict == Verdict.OK
    denied = access_policy.evaluate_registered(
        1, ["trading"], _stored(corp=8, age=timedelta(days=1)),
    )
    assert denied.verdict == Verdict.SUSPENDED
    stale = access_policy.evaluate_registered(
        1, ["trading"], _stored(age=timedelta(days=8)),
    )
    assert stale.verdict == Verdict.UNKNOWN


def test_recheck_window_and_force(monkeypatch):
    calls = {"n": 0}
    state = _stored(age=timedelta(hours=1))

    def fetch(_cid):
        calls["n"] += 1
        return (5, None)

    def update(cid, corp, alliance, suspended, *, touch_checked_at=True):
        state["corporation_id"] = corp
        state["alliance_id"] = alliance
        state["access_suspended"] = suspended
        if touch_checked_at:
            state["affiliation_checked_at"] = datetime.now(timezone.utc)

    monkeypatch.setattr(access_policy, "fetch_affiliation", fetch)
    monkeypatch.setattr(storage, "get_registry_affiliation", lambda cid: dict(state))
    monkeypatch.setattr(storage, "allowlist_is_empty", lambda: False)
    monkeypatch.setattr(storage, "allowlist_contains", lambda corp, alliance: corp == 5)
    monkeypatch.setattr(storage, "update_registry_affiliation", update)

    assert access_policy.refresh_registered(1, ["trading"]) == Verdict.OK
    assert calls["n"] == 0

    assert access_policy.refresh_registered(1, ["trading"], force=True) == Verdict.OK
    assert calls["n"] == 1

    state["affiliation_checked_at"] = datetime.now(timezone.utc) - timedelta(hours=6)
    assert access_policy.refresh_registered(1, ["trading"]) == Verdict.OK
    assert calls["n"] == 2


def test_refresh_single_flight_makes_one_esi_call(monkeypatch):
    calls = {"n": 0}
    state = _stored(checked=False)

    def fetch(_cid):
        calls["n"] += 1
        time.sleep(0.15)
        return (5, None)

    def update(cid, corp, alliance, suspended, *, touch_checked_at=True):
        state["corporation_id"] = corp
        state["alliance_id"] = alliance
        state["access_suspended"] = suspended
        if touch_checked_at:
            state["affiliation_checked_at"] = datetime.now(timezone.utc)

    monkeypatch.setattr(access_policy, "fetch_affiliation", fetch)
    monkeypatch.setattr(storage, "get_registry_affiliation", lambda cid: dict(state))
    monkeypatch.setattr(storage, "allowlist_is_empty", lambda: False)
    monkeypatch.setattr(storage, "allowlist_contains", lambda corp, alliance: True)
    monkeypatch.setattr(storage, "update_registry_affiliation", update)

    errors = []

    def run():
        try:
            access_policy.refresh_registered(42, ["trading"])
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert errors == []
    assert calls["n"] == 1
