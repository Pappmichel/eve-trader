"""Phase 5: retired consent names stay gone outside the plan and the DROP."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", "dist", ".pytest_cache",
    "eve_trader.egg-info",
}
_SKIP_NAMES = {"test_esi_consent_retired.py"}
_SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".webp", ".ico", ".woff", ".woff2"}

_FORBIDDEN = (
    "has_role_consent",
    "record_role_consent",
    "role_consent_schema",
    "consentStatus",
    "acknowledgeConsent",
)

_ONCE_PER_ROLE = "once per role"


def _iter_source_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.name in _SKIP_NAMES:
            continue
        if path.suffix in _SKIP_SUFFIXES:
            continue
        yield path


def test_retired_consent_identifiers_are_gone_outside_the_plan():
    hits = []
    for path in _iter_source_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel == "docs/ESI_ACCESS_PLAN.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in _FORBIDDEN:
            if needle in text:
                hits.append(f"{rel}: {needle}")
        if "tenant_role_consents" in text and rel != "docs/esi_access_schema.sql":
            hits.append(f"{rel}: tenant_role_consents")
    assert hits == [], "retired consent names leaked:\n" + "\n".join(hits)


def test_confirm_dialog_has_no_once_per_role_copy():
    text = (ROOT / "frontend" / "src" / "roleAccessDescriptions.tsx").read_text(encoding="utf-8")
    assert _ONCE_PER_ROLE not in text.lower()
    assert "(new)" in text
