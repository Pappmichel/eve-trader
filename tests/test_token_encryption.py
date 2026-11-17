"""Stored SSO tokens are encrypted, fail closed, and never look like a dead
owner when decryption fails."""
from __future__ import annotations

import json

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.types.json import Jsonb

from eve_trader import storage, token_crypto, token_maintenance
from eve_trader.esi_data import orchestrator

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant  # noqa: F401

pytestmark = pg_helpers.postgres_required()

FULL = {
    "role": "producer:1", "character_id": 1, "character_name": "X",
    "access_token": "ACCESS-SECRET-VALUE", "refresh_token": "REFRESH-SECRET-VALUE",
    "expires_at": 1.0, "scopes": "s",
}


@pytest.fixture
def key_env(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(token_crypto.ENV_VAR, key)
    token_crypto.reset_key_cache()
    return key


def _raw_rows(tenant_id):
    with psycopg.connect(pg_helpers.OWNER_DSN) as conn:
        return conn.execute(
            "SELECT role, record FROM tenant_tokens WHERE tenant_id = %s ORDER BY role", (tenant_id,)
        ).fetchall()


def _write_raw(tenant_id, role, value):
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO tenant_tokens (tenant_id, role, record) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id, role) DO UPDATE SET record = excluded.record",
            (tenant_id, role, Jsonb(value)),
        )


@pytest.fixture
def real_tenants():
    pg_helpers.wipe_tables("tenant_tokens")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    a = storage.create_tenant("tok-a")
    b = storage.create_tenant("tok-b")
    yield a, b
    pg_helpers.wipe_tables("tenant_tokens")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))


def test_round_trip_full_and_partial_record_and_raw_row_is_ciphertext(tenant, key_env):
    storage.save_tenant_token("producer:1", dict(FULL))
    storage.save_tenant_token("producer:2", {"access_token": "x"})

    loaded = storage.load_all_tenant_tokens()
    assert loaded["producer:1"] == FULL
    assert loaded["producer:2"] == {"access_token": "x"}

    for _role, record in _raw_rows(tenant):
        assert set(record) == {"v", "ct"}
        text = json.dumps(record)
        assert "REFRESH-SECRET-VALUE" not in text and "ACCESS-SECRET-VALUE" not in text


def test_missing_and_invalid_key_fail_closed(tenant, key_env, monkeypatch):
    storage.save_tenant_token("producer:1", dict(FULL))

    monkeypatch.delenv(token_crypto.ENV_VAR)
    token_crypto.reset_key_cache()
    with pytest.raises(token_crypto.TokenKeyError):
        storage.load_all_tenant_tokens()
    with pytest.raises(token_crypto.TokenKeyError):
        storage.save_tenant_token("producer:1", dict(FULL))

    monkeypatch.setenv(token_crypto.ENV_VAR, "not-a-key")
    token_crypto.reset_key_cache()
    with pytest.raises(token_crypto.TokenKeyError):
        storage.load_all_tenant_tokens()


def test_wrong_key_plaintext_row_and_moved_row_raise_never_skip(tenant, key_env, monkeypatch):
    storage.save_tenant_token("producer:1", dict(FULL))

    # Plaintext row
    _write_raw(tenant, "producer:2", {"access_token": "plain"})
    with pytest.raises(token_crypto.TokenDataError):
        storage.load_all_tenant_tokens()

    # Row copied to another role
    rows = dict(_raw_rows(tenant))
    _write_raw(tenant, "producer:2", rows["producer:1"])
    with pytest.raises(token_crypto.TokenDataError):
        storage.load_all_tenant_tokens()

    # Row copied to another tenant
    other = "00000000-0000-0000-0000-0000000000aa"
    _write_raw(other, "producer:1", rows["producer:1"])
    with storage.tenant_context(other):
        with pytest.raises(token_crypto.TokenDataError):
            storage.load_all_tenant_tokens()
    pg_helpers.wipe_tables("tenant_tokens")

    # Another key
    storage.save_tenant_token("producer:1", dict(FULL))
    monkeypatch.setenv(token_crypto.ENV_VAR, Fernet.generate_key().decode())
    token_crypto.reset_key_cache()
    with pytest.raises(token_crypto.TokenDataError):
        storage.load_all_tenant_tokens()
    pg_helpers.wipe_tables("tenant_tokens")


def test_encrypt_tokens_is_idempotent_and_check_reports_plaintext(real_tenants, key_env):
    a, b = real_tenants
    _write_raw(a, "producer:1", dict(FULL))
    _write_raw(b, "producer:2", {"access_token": "y"})

    assert token_maintenance.encrypt_tokens(check_only=True)["plaintext"] == 2
    first = token_maintenance.encrypt_tokens()
    assert first["encrypted"] == 2
    snapshot = _raw_rows(a) + _raw_rows(b)
    second = token_maintenance.encrypt_tokens()
    assert second["encrypted"] == 0 and second["already_encrypted"] == 2
    assert _raw_rows(a) + _raw_rows(b) == snapshot
    assert token_maintenance.encrypt_tokens(check_only=True)["plaintext"] == 0
    with storage.tenant_context(a):
        assert storage.load_all_tenant_tokens()["producer:1"] == FULL


def test_encrypt_does_not_overwrite_a_row_refreshed_in_between(real_tenants, key_env, monkeypatch):
    a, _ = real_tenants
    _write_raw(a, "producer:1", {"access_token": "old"})
    real = storage.replace_tenant_token_row

    def racing(role, old, new):
        _write_raw(a, role, {"access_token": "refreshed"})
        return real(role, old, new)

    monkeypatch.setattr(storage, "replace_tenant_token_row", racing)
    result = token_maintenance.encrypt_tokens()
    assert result["changed_meanwhile"] == 1
    assert dict(_raw_rows(a))["producer:1"] == {"access_token": "refreshed"}


def test_rotate_token_key_makes_old_key_removable(real_tenants, key_env, monkeypatch):
    a, _ = real_tenants
    with storage.tenant_context(a):
        storage.save_tenant_token("producer:1", dict(FULL))
    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv(token_crypto.ENV_VAR, f"{new_key},{key_env}")
    token_crypto.reset_key_cache()
    assert token_maintenance.rotate_token_key()["rotated"] == 1

    monkeypatch.setenv(token_crypto.ENV_VAR, new_key)
    token_crypto.reset_key_cache()
    with storage.tenant_context(a):
        assert storage.load_all_tenant_tokens()["producer:1"] == FULL


def test_decrypt_tokens_writes_plaintext_back(real_tenants, key_env):
    a, _ = real_tenants
    with storage.tenant_context(a):
        storage.save_tenant_token("producer:1", dict(FULL))
    assert token_maintenance.decrypt_tokens_to_plaintext()["decrypted"] == 1
    assert dict(_raw_rows(a))["producer:1"] == FULL


def test_startup_check_counts_only_and_passes_when_clean(real_tenants, key_env):
    a, b = real_tenants
    with storage.tenant_context(a):
        storage.save_tenant_token("producer:1", dict(FULL))
    token_maintenance.verify_stored_tokens()  # clean: no error

    _write_raw(b, "producer:2", {"refresh_token": "LEAKY-REFRESH"})
    with pytest.raises(RuntimeError) as exc:
        token_maintenance.verify_stored_tokens()
    message = str(exc.value)
    assert "1 plaintext row" in message and "encrypt-tokens" in message
    assert "LEAKY-REFRESH" not in message and "tok-b" not in message


def test_startup_check_without_key_raises(key_env, monkeypatch):
    from eve_trader.api import app as app_module
    monkeypatch.delenv(token_crypto.ENV_VAR)
    token_crypto.reset_key_cache()
    with pytest.raises(token_crypto.TokenKeyError):
        app_module.verify_token_encryption_on_startup()


def test_crypto_failure_is_not_a_dead_owner_nor_transient(monkeypatch):
    for exc in (token_crypto.TokenKeyError("no key"), token_crypto.TokenDataError("bad row")):
        assert not orchestrator._is_dead_owner_error(exc)
        assert not orchestrator._is_transient_error(exc)


def test_crypto_failure_in_a_sync_does_not_clear_snapshots(monkeypatch):
    cleared = []
    recorded = []
    monkeypatch.setattr(orchestrator, "clear_stale_owner_kind", lambda *a, **k: cleared.append(a) or True)
    monkeypatch.setattr(orchestrator.storage, "upsert_esi_freshness", lambda *a, **k: recorded.append(k))
    kind = next(iter(orchestrator._KIND_TIER))
    orchestrator._record_failure("character", 1, kind, token_crypto.TokenDataError("bad row"))
    assert cleared == []
    assert recorded and recorded[0]["transient"] is False
