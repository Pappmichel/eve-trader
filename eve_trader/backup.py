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
Invoked via `docker exec <container> pg_dump ...` by default, since the dev
Postgres only runs inside Docker - EVE_TRADER_PG_CONTAINER/
EVE_TRADER_DOCKER_BIN env vars make both the container name and the
`docker` binary itself overridable, matching the existing
EVE_TRADER_PG_DSN/EVE_TRADER_PG_OWNER_DSN convention. Setting
EVE_TRADER_PG_CONTAINER to the literal empty string switches to a bare
`pg_dump` call with no `docker exec` wrapper at all - for a host that runs
Postgres natively (confirmed real: the live deploy target has ~1GB RAM,
where adding Docker's own overhead just for this one command isn't worth
it - see deploy/README.md's Postgres section).

data/tokens.json is deliberately never included anymore (TokenManager
persists to Postgres's tenant_tokens table now - see auth.py - so the live
tokens are already inside the pg_dump; including a separate, possibly-stale
copy would be actively misleading on a restore).

Restoring is a manual operation, not a CLI command here (same as before
this rework - list_backups()/create_backup() never had a restore
counterpart either): extract eve_trader.dump from the zip, then
`docker exec -i <container> pg_restore -U postgres -d eve_trader --clean --if-exists < eve_trader.dump`
(add `-c` and `--if-exists` so it can run against an already-populated DB) -
or, on a bare-`pg_dump`-mode host, the same `pg_restore -U postgres -d
eve_trader --clean --if-exists < eve_trader.dump` without the `docker exec`
wrapper.

Restore drill (T1-06/Phase 10 follow-up, 2026-09-26, live-verified against
evetrader.duckdns.org without touching the real `eve_trader` database):
`createdb eve_trader_restore_drill` (a throwaway sibling database on the
same Postgres instance) -> `pg_restore --no-owner --no-privileges -d
eve_trader_restore_drill eve_trader.dump` -> `GRANT SELECT ON ALL TABLES
IN SCHEMA public TO eve_trader_app` (the dump's own GRANTs were skipped by
--no-privileges, needed only so the app role can read this throwaway copy
at all) -> point a one-off script at it via an `EVE_TRADER_PG_DSN` env
override with `dbname=` swapped (never edit the real `.env`) and run real
storage.py functions (list_tenants(), a real RLS-scoped connect()) against
it -> `dropdb eve_trader_restore_drill` when done. Confirmed exact row-
count and JSON-payload parity with the live database this way (not just
"pg_restore exited 0").

**One real finding from this drill, not just a confirmation**: a restore
puts back the schema *as it was at backup time*, not today's schema - the
backup used for this drill (created before the same day's T1-04 fix) came
back with the T1-04-era bare `(item_id)`/`(job_id)` PKs on `corp_assets`/
`corp_industry_jobs`/`corp_blueprints`, not the widened `(tenant_id, ...)`
ones already live. A real disaster-recovery restore must re-apply every
`docs/*_schema.sql` file (in the same order `deploy/deploy.sh`'s migration
loop does) *after* `pg_restore`, or the restored database silently reverts
to whatever schema state the backup predates - `pg_restore` alone is not
sufficient on its own for a backup that isn't from today.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import uuid
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

log = logging.getLogger(__name__)
_backup_lock = threading.Lock()


class BackupError(RuntimeError):
    """User-facing backup failure. pg_dump stderr and filesystem paths stay
    in the process log, not in this message (F-NEW-04)."""

PG_CONTAINER = os.getenv("EVE_TRADER_PG_CONTAINER", "eve-trader-pg")
DOCKER_BIN = os.getenv("EVE_TRADER_DOCKER_BIN", "docker")
PG_DB_NAME = "eve_trader"


def create_backup() -> dict:
    """Creates one timestamped .zip under BACKUP_DIR containing a pg_dump
    (`-Fc`) of the whole Postgres database plus config.yaml (if present),
    then prunes anything beyond MAX_BACKUPS. Returns the same shape as one
    entry of list_backups(). Serialized so parallel callers cannot collide
    on a filename or race the retention prune."""
    with _backup_lock:
        return _create_backup_locked()


def _create_backup_locked() -> dict:
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    unique = uuid.uuid4().hex[:8]
    backup_path = BACKUP_DIR / f"{BACKUP_NAME_PREFIX}{ts}_{unique}.zip"
    tmp_dump_path = BACKUP_DIR / f".tmp_{ts}_{unique}.dump"

    if PG_CONTAINER:
        pg_dump_cmd = [DOCKER_BIN, "exec", PG_CONTAINER, "pg_dump", "-U", "postgres", "-d", PG_DB_NAME, "-Fc"]
    else:
        # Bare mode - the app process runs as an unprivileged OS user (not
        # `postgres`), so the default Unix-socket connection would try (and
        # fail) peer auth. `-h 127.0.0.1` forces a TCP connection instead,
        # which uses password auth - looked up automatically from `~/.pgpass`
        # (standard libpq convention, no password ever needs to live in this
        # process's own env/command line). See deploy/README.md's Postgres
        # section for setting up ~/.pgpass for the owner role.
        pg_dump_cmd = ["pg_dump", "-h", "127.0.0.1", "-U", "postgres", "-d", PG_DB_NAME, "-Fc"]

    try:
        with open(tmp_dump_path, "wb") as dump_file:
            result = subprocess.run(
                pg_dump_cmd,
                stdout=dump_file, stderr=subprocess.PIPE, timeout=300,
            )
        # T1-05 regression (found in Plan 2's independent challenge pass,
        # 2026-09-26): the raw dump - and, below, the finished zip - must
        # never depend on whatever umask the calling process happens to
        # have. A manual/ad-hoc invocation over a non-interactive SSH
        # session inherits the OS default umask (0002 here), not the
        # interactive-shell-only `umask 077` in .bashrc - confirmed live,
        # the exact way the challenge pass's own throwaway backup ended up
        # world-readable (chmod 600'd by hand afterward, real exposure
        # while it existed). systemd's own UMask=0027 on the real service
        # covers the scheduled/Admin-UI paths, but this function has no way
        # to know it's being called from a hardened process vs. an ad-hoc
        # script - chmod explicitly rather than trust the caller's umask.
        os.chmod(tmp_dump_path, 0o600)
        if result.returncode != 0:
            stderr_text = result.stderr.decode(errors="replace") if result.stderr else ""
            log.error("pg_dump failed (exit %s): %s", result.returncode, stderr_text)
            raise BackupError("Database backup failed.")

        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_dump_path, arcname="eve_trader.dump")
            if DEFAULT_CONFIG_PATH.exists():
                zf.write(DEFAULT_CONFIG_PATH, arcname="config.yaml")
        os.chmod(backup_path, 0o600)
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
    info = _backup_info(backup_path)
    # T1-06 (2026-09-26): _run_job only ever logs a scheduled backup's
    # FAILURE, never its success - manual (Admin "Create Backup") and
    # scheduled backups looked identical in the logs either way, which is
    # exactly why a several-day gap (scheduler_enabled off) went unnoticed.
    # This is the one place both paths funnel through.
    log.info("Backup created: %s (%s bytes)", info["name"], info["size_bytes"])
    return info


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
