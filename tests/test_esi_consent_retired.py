"""Phase 5: retired consent names stay gone outside the plan and the DROP."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", "dist", ".pytest_cache",
    "eve_trader.egg-info", "data", ".ssh-local", ".idea",
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


def _candidate_paths():
    """The repo's tracked files. Walking the whole checkout used to stat ~60k
    entries under .venv/node_modules and read ~300 MB of untracked data
    (backups, SDE downloads, keys), which made this the suite's slowest test
    (about 40 s). Without git, walk the tree but never enter the skipped
    directories."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        return [ROOT / p for p in out.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            paths.extend(Path(dirpath) / f for f in filenames)
        return paths


def _iter_source_files():
    for path in _candidate_paths():
        if any(part in _SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.name in _SKIP_NAMES:
            continue
        if path.suffix in _SKIP_SUFFIXES:
            continue
        if not path.is_file():
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
