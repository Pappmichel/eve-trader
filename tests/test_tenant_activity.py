"""Scheduler rework phase C: tenant activity signal + operator config."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.config import (
    SCHEDULER_OPERATOR_CONFIG, ConfigError, SchedulerOperatorConfig, load_scheduler_operator_config,
)

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
    _apply_session_revocations_schema,
)

psycopg = pytest.importorskip("psycopg")


# ------------------------------------------------------------ operator config
def test_operator_config_defaults_keep_todays_behaviour():
    cfg = SchedulerOperatorConfig()
    assert cfg.inactive_tenant_days == 14.0
    assert cfg.backup_job_enabled is True
    assert cfg.jita_price_cache_job_enabled is True
    assert cfg.alerts_job_enabled is False
    assert isinstance(SCHEDULER_OPERATOR_CONFIG, SchedulerOperatorConfig)


def test_operator_config_reads_config_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "inactive_tenant_days: 30\nbackup_job_enabled: false\nalerts_job_enabled: true\n"
        "scheduler_enabled: true\n",  # unrelated key from the shared file is ignored
        encoding="utf-8",
    )
    cfg = load_scheduler_operator_config(path)
    assert cfg.inactive_tenant_days == 30
    assert cfg.backup_job_enabled is False
    assert cfg.alerts_job_enabled is True
    assert cfg.jita_price_cache_job_enabled is True


def test_operator_config_rejects_a_negative_threshold_and_wrong_types(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("inactive_tenant_days: -1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_scheduler_operator_config(path)
    path.write_text("backup_job_enabled: maybe\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_scheduler_operator_config(path)


# ------------------------------------------------------------ throttle (no DB)
def test_note_tenant_activity_is_throttled_and_never_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "touch_tenant_active", lambda tid: calls.append(tid))
    monkeypatch.setattr(access_gate, "_activity_last_touch", {})
    clock = {"t": 1000.0}
    monkeypatch.setattr(access_gate.time, "monotonic", lambda: clock["t"])

    access_gate.note_tenant_activity("t1")
    access_gate.note_tenant_activity("t1")
    access_gate.note_tenant_activity("t2")
    assert calls == ["t1", "t2"]

    clock["t"] += access_gate._ACTIVITY_TOUCH_INTERVAL_SECONDS + 1
    access_gate.note_tenant_activity("t1")
    assert calls == ["t1", "t2", "t1"]

    def boom(_tid):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage, "touch_tenant_active", boom)
    access_gate.note_tenant_activity("t3")  # must not raise


# ------------------------------------------------------------ storage + HTTP
pg = pg_helpers.postgres_required()


@pytest.fixture
def _clean_tenants(_apply_admin_schema):
    pg_helpers.wipe_tables("tenant_registry_entries", "tool_grants", "character_session_revocations")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    access_gate._activity_last_touch.clear()
    yield
    access_gate._activity_last_touch.clear()


@pg
def test_touch_and_list_last_active(_clean_tenants):
    tid = storage.create_tenant("T")
    assert storage.list_tenant_last_active()[tid] is None
    storage.touch_tenant_active(tid)
    assert storage.list_tenant_last_active()[tid] is not None


@pg
@pytest.mark.gate_enforced
def test_authorized_request_stamps_activity_once_and_unauthenticated_does_not(_clean_tenants, monkeypatch):
    client = TestClient(create_app())
    tid = storage.create_tenant("T")
    storage.add_tenant_registry_entry(tid, 11, character_name="T")
    storage.set_tool_grant(11, "trading", tid)
    cookies = {access_gate.SESSION_COOKIE_NAME: access_gate.create_session_token(11, "T", tid)}

    assert client.get("/api/trading/settings").status_code == 401
    assert storage.list_tenant_last_active()[tid] is None

    writes = []
    real = storage.touch_tenant_active
    monkeypatch.setattr(storage, "touch_tenant_active", lambda t: (writes.append(t), real(t))[1])
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200
    assert writes == [tid]
    assert storage.list_tenant_last_active()[tid] is not None


@pg
@pytest.mark.gate_enforced
def test_a_failing_activity_write_never_fails_the_request(_clean_tenants, monkeypatch):
    client = TestClient(create_app())
    tid = storage.create_tenant("T")
    storage.add_tenant_registry_entry(tid, 11, character_name="T")
    storage.set_tool_grant(11, "trading", tid)
    cookies = {access_gate.SESSION_COOKIE_NAME: access_gate.create_session_token(11, "T", tid)}

    def boom(_t):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage, "touch_tenant_active", boom)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200
