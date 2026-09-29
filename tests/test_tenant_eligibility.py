import datetime as dt

from eve_trader import storage, tenant_eligibility
from eve_trader.config import ACCESS_CONFIG, SCHEDULER_OPERATOR_CONFIG

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.timezone.utc)


def test_unknown_or_never_recorded_activity_counts_as_active():
    assert tenant_eligibility.is_active("t", {}, now=NOW) is True
    assert tenant_eligibility.is_active("t", {"t": None}, now=NOW) is True


def test_inactive_only_past_the_threshold(monkeypatch):
    monkeypatch.setattr(SCHEDULER_OPERATOR_CONFIG, "inactive_tenant_days", 14.0)
    assert tenant_eligibility.is_active("t", {"t": NOW - dt.timedelta(days=13)}, now=NOW) is True
    assert tenant_eligibility.is_active("t", {"t": NOW - dt.timedelta(days=15)}, now=NOW) is False


def test_naive_timestamps_are_read_as_utc(monkeypatch):
    monkeypatch.setattr(SCHEDULER_OPERATOR_CONFIG, "inactive_tenant_days", 14.0)
    naive = (NOW - dt.timedelta(days=20)).replace(tzinfo=None)
    assert tenant_eligibility.is_active("t", {"t": naive}, now=NOW) is False


def test_zero_days_disables_the_check_and_default_tenant_is_always_active(monkeypatch):
    old = {"t": NOW - dt.timedelta(days=400), storage.DEFAULT_TENANT_ID: NOW - dt.timedelta(days=400)}
    monkeypatch.setattr(SCHEDULER_OPERATOR_CONFIG, "inactive_tenant_days", 0.0)
    assert tenant_eligibility.is_active("t", old, now=NOW) is True
    monkeypatch.setattr(SCHEDULER_OPERATOR_CONFIG, "inactive_tenant_days", 14.0)
    assert tenant_eligibility.is_active(storage.DEFAULT_TENANT_ID, old, now=NOW) is True
    assert tenant_eligibility.is_active("t", old, now=NOW) is False


def test_granted_tools_is_unrestricted_with_the_gate_off(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: (_ for _ in ()).throw(AssertionError))
    assert tenant_eligibility.granted_tools("t") is None
    assert tenant_eligibility.may_use("trading", None) is True


def test_granted_tools_reads_the_tenants_grants_with_the_gate_on(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: ["production", "portfolio"])
    grants = tenant_eligibility.granted_tools("t")
    assert grants == {"production", "portfolio"}
    assert tenant_eligibility.may_use("production", grants) is True
    assert tenant_eligibility.may_use("trading", grants) is False
    # gate on + no grants at all is a real (empty) restriction, not "unrestricted"
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: [])
    assert tenant_eligibility.granted_tools("t") == set()
