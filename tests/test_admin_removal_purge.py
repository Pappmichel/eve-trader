"""Removing a user deletes the tenant's tokens and Discord link at once; an
orphaned tenant can be purged for good with an exact-name confirmation."""
from __future__ import annotations

import psycopg
import pytest
import requests

from eve_trader import admin, auth, storage, tenant_scope, token_crypto
from eve_trader.actions import ActionError

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
)

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "tenant_registry_entries", "tool_grants", "character_session_revocations", "tenant_tokens",
        "alert_destinations", "alert_subscriptions", "esi_sharing",
    )
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield


class _Resp:
    def __init__(self, code):
        self.status_code = code


def _seed(name, character_id):
    tid = storage.create_tenant(name)
    storage.add_tenant_registry_entry(tid, character_id, character_name=name)
    with tenant_scope.enter_tenant(tid):
        storage.save_tenant_token(
            f"producer:{character_id}", {"refresh_token": f"R-{character_id}-a", "access_token": "x"})
        storage.save_tenant_token(f"seller:{character_id + 1}", {"refresh_token": f"R-{character_id}-b"})
        storage.set_alert_destination("111")
        storage.upsert_alert_subscription(character_id, "skillqueue_empty", True, False, 12)
        with storage.connect() as conn:
            conn.execute(
                "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                "VALUES ('character', ?, 'assets', 'production')", (character_id,))
    return tid


def _tokens(tid):
    with tenant_scope.enter_tenant(tid):
        return storage.load_all_tenant_tokens()


def _tenant_ids():
    return {str(t[0]) for t in storage.list_tenants()}


@pytest.mark.parametrize("outcome", ["ok", "timeout", "http400"])
def test_remove_user_deletes_credentials_whatever_revoke_does(monkeypatch, outcome):
    a = _seed("tenant-a", 1001)
    b = _seed("tenant-b", 2001)
    calls = []

    def fake_post(url, data=None, **kw):
        calls.append((url, dict(data)))
        if outcome == "timeout":
            raise requests.Timeout("slow")
        return _Resp(200 if outcome == "ok" else 400)

    monkeypatch.setattr(auth.requests, "post", fake_post)

    result = admin.do_remove_user(1001)

    assert result["removed"] == 1001 and result["tokens_deleted"] == 2
    assert result["discord_unlinked"] is True
    assert (result["revoked"], result["revoke_failed"]) == ((2, 0) if outcome == "ok" else (0, 2))
    assert all(c[0].endswith("/v2/oauth/revoke") and c[1]["token_type_hint"] == "refresh_token" for c in calls)
    assert _tokens(a) == {}
    with tenant_scope.enter_tenant(a):
        assert storage.get_alert_destination() is None
        assert storage.get_alert_subscription(1001, "skillqueue_empty")[0] is False
        with storage.connect() as conn:  # sharing rows and other data stay
            assert conn.execute("SELECT count(*) FROM esi_sharing").fetchone()[0] == 1
    assert len(_tokens(b)) == 2  # other tenant untouched
    assert storage.resolve_tenant_id(1001) is None


def test_remove_user_with_unreadable_tokens_still_deletes(monkeypatch):
    a = _seed("tenant-a", 1001)
    from cryptography.fernet import Fernet
    monkeypatch.setenv(token_crypto.ENV_VAR, Fernet.generate_key().decode())
    token_crypto.reset_key_cache()

    def no_post(*args, **kwargs):
        raise AssertionError("no revoke without readable tokens")

    monkeypatch.setattr(auth.requests, "post", no_post)
    result = admin.do_remove_user(1001)
    assert result["tokens_deleted"] == 2 and result["revoke_skipped"] is True
    assert _tokens(a) == {}


def test_remove_user_unknown_character_is_a_no_op():
    result = admin.do_remove_user(999999)
    assert result["tokens_deleted"] == 0 and result["discord_unlinked"] is False


def test_purge_rejections_delete_nothing():
    a = _seed("tenant-a", 1001)
    orphan = storage.create_tenant("orphan")
    with pytest.raises(ActionError):
        admin.do_purge_tenant(storage.DEFAULT_TENANT_ID, "x")
    with pytest.raises(ActionError, match="registered user"):
        admin.do_purge_tenant(a, "tenant-a")
    with pytest.raises(ActionError, match="does not match"):
        admin.do_purge_tenant(orphan, "wrong")
    storage.add_tenant_registry_entry(orphan, 3001)
    with pytest.raises(ActionError):
        admin.do_purge_tenant(orphan, "orphan")
    storage.remove_tenant_registry_entry(3001)
    # the acting admin's own tenant is refused (registry entry of the actor is gone here,
    # so simulate with an actor that still resolves to the tenant)
    storage.add_tenant_registry_entry(orphan, 3002)
    with pytest.raises(ActionError):
        admin.do_purge_tenant(orphan, "orphan", actor_character_id=3002)
    assert len(_tokens(a)) == 2
    assert orphan in _tenant_ids()


def test_purge_deletes_every_tenant_row_and_leaves_others(monkeypatch):
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _Resp(200))
    a = _seed("tenant-a", 1001)
    b = _seed("tenant-b", 2001)
    admin.do_remove_user(1001)
    with tenant_scope.enter_tenant(a):
        storage.save_tenant_token("producer:1001", {"refresh_token": "again"})

    preview = admin.do_purge_tenant_preview(a)
    assert preview["name"] == "tenant-a" and preview["total_rows"] >= 3

    result = admin.do_purge_tenant(a, "tenant-a")
    assert result["rows_deleted"] >= 3 and result["tokens_deleted"] == 1

    assert storage.tenant_data_tables()
    assert storage.count_tenant_rows(a) == {}
    assert a not in _tenant_ids()
    assert len(_tokens(b)) == 2
    assert storage.count_tenant_rows(b)


def test_purge_can_be_finished_after_an_interruption(monkeypatch):
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _Resp(200))
    a = _seed("tenant-a", 1001)
    admin.do_remove_user(1001)
    real = storage.connect_unscoped

    def broken(*args, **kwargs):
        raise RuntimeError("connection lost")

    # The RLS part commits, then the unscoped part dies.
    monkeypatch.setattr(storage, "connect_unscoped", broken)
    with pytest.raises(RuntimeError):
        storage.purge_tenant_rows(a)
    monkeypatch.setattr(storage, "connect_unscoped", real)

    assert a in _tenant_ids()
    result = admin.do_purge_tenant(a, "tenant-a")
    assert result["purged"] == a
    assert a not in _tenant_ids()
