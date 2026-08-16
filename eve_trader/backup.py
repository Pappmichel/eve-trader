"""Backs up the app's own persistent state - data/eve_trader.db, config.yaml,
and data/tokens.json (OAuth tokens) - into a single timestamped .zip under
data/backups/. This is the app's only source of truth (no git repo, no other
persistence layer for either tool) - a backup exists so a disk failure or an
accidental delete doesn't lose everything with no way back.

Uses SQLite's own online backup API (sqlite3.Connection.backup), not a plain
file copy - a raw copy of a live SQLite file isn't guaranteed consistent if
something else has it open for writing at the same moment (this app doesn't
force WAL mode, so a naive copy could snapshot a half-written page); the
backup API produces a guaranteed-consistent snapshot regardless of
concurrent access, without needing to coordinate with every other writer.
"""
from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import storage
from .config import DATA_DIR, DEFAULT_CONFIG_PATH, OAUTH_CONFIG

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_NAME_PREFIX = "eve_trader_backup_"
# ~2 weeks of daily backups at the scheduler's default interval - old ones
# are pruned automatically (oldest first), same "cap it so an always-on
# background process can't grow this without bound" reasoning as
# logging_setup.py's RotatingFileHandler.
MAX_BACKUPS = 14


def create_backup() -> dict:
    """Creates one timestamped .zip under BACKUP_DIR containing a consistent
    snapshot of the DB, config.yaml (if present), and tokens.json (if
    present), then prunes anything beyond MAX_BACKUPS. Returns the same shape
    as one entry of list_backups()."""
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_path = BACKUP_DIR / f"{BACKUP_NAME_PREFIX}{ts}.zip"
    tmp_db_path = BACKUP_DIR / f".tmp_{ts}.db"

    try:
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            src = sqlite3.connect(storage.DB_PATH)
            dst = sqlite3.connect(tmp_db_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            zf.write(tmp_db_path, arcname="eve_trader.db")

            if DEFAULT_CONFIG_PATH.exists():
                zf.write(DEFAULT_CONFIG_PATH, arcname="config.yaml")
            if OAUTH_CONFIG.token_store_path.exists():
                zf.write(OAUTH_CONFIG.token_store_path, arcname="tokens.json")
    except Exception:
        # Confirmed real gap: a failure partway through (a locked/corrupt
        # source DB, a full disk, ...) used to leave the partial/corrupt
        # .zip behind - it matches BACKUP_NAME_PREFIX, so _prune_old_backups
        # counts it against the rotation and list_backups() (Portfolio page,
        # scheduler) shows it looking like a normal, complete backup. Clean
        # it up before re-raising instead.
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        # tmp_db_path is a raw sqlite copy, never itself part of the zip
        # (only its *contents* were written in via zf.write above) - always
        # remove it, success or failure. Confirmed real gap: the old code
        # only unlinked it on the success path, so a failure between the
        # backup() call and this point left an orphaned .tmp_*.db under
        # BACKUP_DIR forever (it doesn't match BACKUP_NAME_PREFIX, so
        # nothing else here ever notices or cleans it up).
        tmp_db_path.unlink(missing_ok=True)

    _prune_old_backups()
    return _backup_info(backup_path)


def _prune_old_backups() -> None:
    backups = sorted(BACKUP_DIR.glob(f"{BACKUP_NAME_PREFIX}*.zip"), key=lambda p: p.name)
    for old in backups[:-MAX_BACKUPS]:
        old.unlink()


def _backup_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "size_bytes": stat.st_size,
    }


def list_backups() -> list[dict]:
    """Every existing backup, newest first - used by the Portfolio page to
    show what's already there, and by scheduler.py to decide whether one is
    due (no separate "last backup time" tracking needed - the newest file's
    own mtime already is that)."""
    if not BACKUP_DIR.exists():
        return []
    backups = sorted(BACKUP_DIR.glob(f"{BACKUP_NAME_PREFIX}*.zip"), key=lambda p: p.name, reverse=True)
    return [_backup_info(p) for p in backups]
