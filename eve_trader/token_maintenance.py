"""Operator and startup checks over every tenant's stored token rows.

Output is counts only: never token content, never tenant or character names.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import storage, token_crypto


@dataclass
class TokenScan:
    """Row counts per tenant: encrypted and readable, plaintext, unreadable."""
    ok: dict[str, int] = field(default_factory=dict)
    plaintext: dict[str, int] = field(default_factory=dict)
    unreadable: dict[str, int] = field(default_factory=dict)

    @property
    def plaintext_total(self) -> int:
        return sum(self.plaintext.values())

    @property
    def unreadable_total(self) -> int:
        return sum(self.unreadable.values())

    @property
    def ok_total(self) -> int:
        return sum(self.ok.values())


def _tenant_ids() -> list[str]:
    return [str(t[0]) for t in storage.list_tenants()]


def scan_tokens() -> TokenScan:
    """Classifies every stored row of every tenant. Requires a key."""
    token_crypto.require_key()
    scan = TokenScan()
    for tenant_id in _tenant_ids():
        with storage.tenant_context(tenant_id):
            for role, stored, tid in storage.tenant_token_rows():
                if not token_crypto.is_encrypted(stored):
                    bucket = scan.plaintext
                else:
                    try:
                        token_crypto.decrypt_record(tid, role, stored)
                        bucket = scan.ok
                    except token_crypto.TokenDataError:
                        bucket = scan.unreadable
                bucket[tenant_id] = bucket.get(tenant_id, 0) + 1
    return scan


def verify_stored_tokens() -> None:
    """Startup check: a key exists and every stored row decrypts. Raises
    `RuntimeError` naming only counts per tenant and the fix command."""
    scan = scan_tokens()
    if not scan.plaintext and not scan.unreadable:
        return
    parts = []
    for label, counts in (("plaintext", scan.plaintext), ("unreadable", scan.unreadable)):
        for tenant_id, n in sorted(counts.items()):
            parts.append(f"tenant {tenant_id}: {n} {label} row(s)")
    hint = []
    if scan.plaintext:
        hint.append("run `eve-trader tenant encrypt-tokens` with the service stopped")
    if scan.unreadable:
        hint.append(
            "unreadable rows were written under another TOKEN_ENCRYPTION_KEY: restore that key "
            "(see docs/RUNBOOK.md, Token key)"
        )
    raise RuntimeError("stored SSO tokens are not usable: " + "; ".join(parts) + ". To fix: " + "; ".join(hint) + ".")


def encrypt_tokens(check_only: bool = False) -> dict[str, int]:
    """Encrypts plaintext rows (idempotent). With `check_only` nothing is
    written. Returns counts: encrypted, already_encrypted, changed_meanwhile."""
    token_crypto.require_key()
    result = {"encrypted": 0, "already_encrypted": 0, "plaintext": 0, "changed_meanwhile": 0}
    for tenant_id in _tenant_ids():
        with storage.tenant_context(tenant_id):
            for role, stored, tid in storage.tenant_token_rows():
                if token_crypto.is_encrypted(stored):
                    result["already_encrypted"] += 1
                    continue
                result["plaintext"] += 1
                if check_only:
                    continue
                new_value = token_crypto.encrypt_record(tid, role, stored if isinstance(stored, dict) else {})
                if storage.replace_tenant_token_row(role, stored, new_value):
                    result["encrypted"] += 1
                else:
                    result["changed_meanwhile"] += 1
    return result


def rotate_token_key() -> dict[str, int]:
    """Re-encrypts every encrypted row with the first configured key."""
    token_crypto.require_key()
    result = {"rotated": 0, "plaintext_skipped": 0, "changed_meanwhile": 0}
    for tenant_id in _tenant_ids():
        with storage.tenant_context(tenant_id):
            for role, stored, _tid in storage.tenant_token_rows():
                if not token_crypto.is_encrypted(stored):
                    result["plaintext_skipped"] += 1
                    continue
                new_value = token_crypto.rotate_stored(stored)
                if storage.replace_tenant_token_row(role, stored, new_value):
                    result["rotated"] += 1
                else:
                    result["changed_meanwhile"] += 1
    return result


def decrypt_tokens_to_plaintext() -> dict[str, int]:
    """Rollback aid for older code that reads plaintext rows only."""
    token_crypto.require_key()
    result = {"decrypted": 0, "plaintext_already": 0, "changed_meanwhile": 0}
    for tenant_id in _tenant_ids():
        with storage.tenant_context(tenant_id):
            for role, stored, tid in storage.tenant_token_rows():
                if not token_crypto.is_encrypted(stored):
                    result["plaintext_already"] += 1
                    continue
                plain = token_crypto.decrypt_record(tid, role, stored)
                if storage.replace_tenant_token_row(role, stored, plain):
                    result["decrypted"] += 1
                else:
                    result["changed_meanwhile"] += 1
    return result
