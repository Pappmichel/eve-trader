"""Backs up the app's own persistent state - the whole Postgres database
(every tenant's data, via pg_dump) and config.yaml - into a single
timestamped .zip under data/backups/. This is the app's only source of
truth (no other persistence layer) - a backup exists so a disk/container
failure or an accidental delete doesn't lose everything with no way back.

Shells out to `pg_dump` (custom/compressed format, `-Fc` - restorable via
`pg_restore`) rather than going through psycopg, since this is a whole-
database, cross-tenant dump - it must run as the Postgres *owner* role
(`postgres`, `-U postgres`), not the app's own `eve_trader_app` role: every
per-tenant table's RLS policy raises on a missing `app.tenant_id` session
setting (see storage.connect()'s own fail-closed docstring) rather than
silently returning zero rows, so a non-bypassing role can't dump per-tenant
tables at all without one already set - the owner role bypasses RLS
entirely, which is exactly what a real disaster-recovery snapshot needs.
Invoked via `docker exec <container> pg_dump ...` since the dev Postgres
only runs inside Docker - EVE_TRADER_PG_CONTAINER/EVE_TRADER_DOCKER_BIN env
vars make both the container name and the `docker` binary itself
overridable, matching the existing EVE_TRADER_PG_DSN/EVE_TRADER_PG_OWNER_DSN
convention.

data/tokens.json is deliberately never included anymore (TokenManager
persists to Postgres's tenant_tokens table now - see auth.py - so the live
tokens are already inside the pg_dump; including a separate, possibly-stale
copy would be actively misleading on a restore).

Restoring is a manual operation, not a CLI command here (same as before
this rework - list_backups()/create_backup() never had a restore
counterpart either): extract eve_trader.dump from the zip, then
`docker exec -i <container> pg_restore -U postgres -d eve_trader --clean --if-exists < eve_trader.dump`
(add `-c` and `--if-exists` so it can run against an already-populated DB).
"""
from __future__ import annotations

import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, DEFAULT_CONFIG_PATH

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_NAME_PREFIX = "eve_trader_backup_"
# ~2 weeks of daily backups at the scheduler's default interval - old ones
# are pruned automatically (oldest first), same "cap it so an always-on
# background process can't grow this without bound" reasoning as
# logging_setup.py's RotatingFileHandler.
MAX_BACKUPS = 14

PG_CONTAINER = os.getenv("EVE_TRADER_PG_CONTAINER", "eve-trader-pg")
DOCKER_BIN = os.getenv("EVE_TRADER_DOCKER_BIN", "docker")
PG_DB_NAME = "eve_trader"


def create_backup() -> dict:
    """Creates one timestamped .zip under BACKUP_DIR containing a pg_dump
    (`-Fc`) of the whole Postgres database plus config.yaml (if present),
    then prunes anything beyond MAX_BACKUPS. Returns the same shape as one
    entry of list_backups()."""
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_path = BACKUP_DIR / f"{BACKUP_NAME_PREFIX}{ts}.zip"
    tmp_dump_path = BACKUP_DIR / f".tmp_{ts}.dump"

    try:
        with open(tmp_dump_path, "wb") as dump_file:
            result = subprocess.run(
                [DOCKER_BIN, "exec", PG_CONTAINER, "pg_dump", "-U", "postgres", "-d", PG_DB_NAME, "-Fc"],
                stdout=dump_file, stderr=subprocess.PIPE, timeout=300,
            )
        if result.returncode != 0:
            raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {result.stderr.decode(errors='replace')}")

        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_dump_path, arcname="eve_trader.dump")
            if DEFAULT_CONFIG_PATH.exists():
                zf.write(DEFAULT_CONFIG_PATH, arcname="config.yaml")
    except Exception:
        # A failure partway through (pg_dump not reachable, a full disk, ...)
        # used to leave a partial/corrupt .zip behind - it matches
        # BACKUP_NAME_PREFIX, so _prune_old_backups counts it against the
        # rotation and list_backups() (Portfolio page, scheduler) shows it
        # looking like a normal, complete backup. Clean it up before
        # re-raising instead.
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        # tmp_dump_path is never itself part of the zip (only its *contents*
        # were written in via zf.write above) - always remove it, success or
        # failure, or a failure between the pg_dump call and this point would
        # leave an orphaned .tmp_*.dump under BACKUP_DIR forever (it doesn't
        # match BACKUP_NAME_PREFIX, so nothing else here ever notices it).
        tmp_dump_path.unlink(missing_ok=True)

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
