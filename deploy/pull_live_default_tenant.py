"""Copy the Default tenant's production data into the local dev DB.

Usage (from the repo root, local Postgres container `eve-trader-pg` running):
    EVE_TRADER_LIVE_SSH="-i .ssh-local/<key> <user>@<host>" \\
        python -I deploy/pull_live_default_tenant.py <empty work dir>

Back up the local DB first (`docker exec eve-trader-pg pg_dump -U postgres
-Fc eve_trader > data/backups/<name>.dump`). Replaces the local Default
tenant's rows and every tenant-free table (SDE, Goonmetrics history, ...).

Filters server-side (only tenant_id = DEFAULT rows, plus tenant-free global
tables like the SDE), streams one tar.gz back over SSH, and replaces the
local Default tenant's rows in one transaction. Nothing stays on the server
(mktemp dir removed by trap). tenant_tokens and a few cross-tenant / secret
tables are never copied.
"""
import io
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

if not os.environ.get("EVE_TRADER_LIVE_SSH") or len(sys.argv) != 2:
    sys.exit(__doc__)
SSH = ["ssh", *shlex.split(os.environ["EVE_TRADER_LIVE_SSH"])]
REMOTE_PSQL = "psql -h 127.0.0.1 -U postgres -d eve_trader"
LOCAL_PSQL = ["docker", "exec", "-i", "eve-trader-pg", "psql", "-U", "postgres", "-d", "eve_trader"]
DEFAULT = "00000000-0000-0000-0000-000000000001"
EXCLUDE = {
    "tenant_tokens",                 # live refresh tokens - never copied
    "access_requests", "access_allowlist", "character_session_revocations",  # cross-tenant
    "alert_destinations",            # Discord links - local alerts must not DM
}
WORK = Path(sys.argv[1])

COLS_SQL = (
    "SELECT c.table_name, c.column_name FROM information_schema.columns c "
    "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
    "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
    "AND c.is_generated = 'NEVER' ORDER BY c.table_name, c.ordinal_position"
)


def columns(out: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in out.splitlines():
        if "|" in line:
            t, c = line.split("|", 1)
            result.setdefault(t, []).append(c)
    return result


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


remote_cols = columns(subprocess.run(
    SSH + [f'{REMOTE_PSQL} -At -F "|" -c "{COLS_SQL}"'],
    check=True, capture_output=True, text=True).stdout)
local_cols = columns(subprocess.run(
    LOCAL_PSQL + ["-At", "-F", "|", "-c", COLS_SQL],
    check=True, capture_output=True, text=True).stdout)

PK_SQL = (
    "SELECT t.relname, a.attname FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid "
    "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey) "
    "WHERE i.indisprimary AND t.relnamespace = 'public'::regnamespace"
)
local_pk = columns(subprocess.run(
    LOCAL_PSQL + ["-At", "-F", "|", "-c", PK_SQL],
    check=True, capture_output=True, text=True).stdout)

plan = []  # (table, cols, tenant_scoped)
for table in sorted(set(remote_cols) & set(local_cols)):
    if table in EXCLUDE:
        continue
    cols = [c for c in remote_cols[table] if c in local_cols[table]]
    plan.append((table, cols, "tenant_id" in cols))
print(f"{len(plan)} tables to copy; skipped remote-only: "
      f"{sorted(set(remote_cols) - set(local_cols))}; local-only: {sorted(set(local_cols) - set(remote_cols))}")

copy_lines = ["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"]
for table, cols, scoped in plan:
    where = f" WHERE tenant_id = '{DEFAULT}'" if scoped else ""
    copy_lines.append(
        f"\\copy (SELECT {', '.join(q(c) for c in cols)} FROM {q(table)}{where}) TO '{table}.copy'")
copy_lines.append("COMMIT;")
remote_script = (
    "set -euo pipefail\n"
    "d=$(mktemp -d)\n"
    "trap 'rm -rf \"$d\"' EXIT\n"
    "cd \"$d\"\n"
    f"{REMOTE_PSQL} -q -v ON_ERROR_STOP=1 -f - >&2 <<'SQL'\n"
    + "\n".join(copy_lines) + "\nSQL\n"
    "tar -czf - .\n"
)
WORK.mkdir(parents=True, exist_ok=True)
proc = subprocess.run(SSH + ["bash -s"], input=remote_script.encode(), capture_output=True)
if proc.returncode != 0:
    sys.exit(f"remote export failed: {proc.stderr.decode()[-2000:]}")
data_dir = WORK / "data"
data_dir.mkdir(exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:gz") as tar:
    tar.extractall(data_dir, filter="data")
print(f"export: {len(proc.stdout) / 1e6:.1f} MB compressed")

subprocess.run(["docker", "exec", "eve-trader-pg", "rm", "-rf", "/tmp/live_import"], check=True)
subprocess.run(["docker", "cp", str(data_dir), "eve-trader-pg:/tmp/live_import"], check=True)

load = ["\\set ON_ERROR_STOP 1", "BEGIN;", "SET session_replication_role = replica;"]
for table, cols, scoped in plan:
    if scoped:
        load.append(f"DELETE FROM {q(table)} WHERE tenant_id = '{DEFAULT}';")
    else:
        load.append(f"DELETE FROM {q(table)};")
    collist = ", ".join(q(c) for c in cols)
    load.append(f"CREATE TEMP TABLE _stage AS SELECT {collist} FROM {q(table)} WITH NO DATA;")
    load.append(f"\\copy _stage ({collist}) FROM '/tmp/live_import/{table}.copy'")
    pk = local_pk.get(table, [])
    if scoped and pk and set(pk) <= set(cols):
        # Leftover rows of other local (test) tenants may hold the same key.
        cond = " AND ".join(f"t.{q(c)} = s.{q(c)}" for c in pk)
        load.append(f"DELETE FROM {q(table)} t USING _stage s WHERE {cond};")
    load.append(f"INSERT INTO {q(table)} ({collist}) SELECT {collist} FROM _stage;")
    load.append("DROP TABLE _stage;")
# Serial sequences follow the copied ids.
load.append("""
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT s.relname AS seq, t.relname AS tbl, a.attname AS col
           FROM pg_class s JOIN pg_depend d ON d.objid = s.oid AND d.deptype IN ('a', 'i')
           JOIN pg_class t ON t.oid = d.refobjid
           JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
           WHERE s.relkind = 'S' AND s.relnamespace = 'public'::regnamespace LOOP
    EXECUTE format('SELECT setval(%L, GREATEST(COALESCE((SELECT MAX(%I) FROM %I), 0), 1))',
                   'public.' || r.seq, r.col, r.tbl);
  END LOOP;
END $$;""")
# Production runs its scheduler; the local copy must not.
load.append(
    "UPDATE tenant_settings SET overrides = overrides || '{\"scheduler_enabled\": false}'::jsonb "
    f"WHERE tenant_id = '{DEFAULT}' AND overrides ? 'scheduler_enabled';")
load.append("COMMIT;")
(WORK / "load.sql").write_text("\n".join(load), encoding="utf-8")
subprocess.run(["docker", "cp", str(WORK / "load.sql"), "eve-trader-pg:/tmp/live_import/load.sql"], check=True)
res = subprocess.run(["docker", "exec", "eve-trader-pg", "psql", "-U", "postgres", "-d", "eve_trader",
                      "-q", "-f", "/tmp/live_import/load.sql"], capture_output=True, text=True)
print(res.stdout[-2000:], res.stderr[-4000:])
subprocess.run(["docker", "exec", "eve-trader-pg", "rm", "-rf", "/tmp/live_import"], check=True)
if res.returncode != 0:
    sys.exit("local load failed - transaction rolled back, local DB unchanged")
print("done")
