# Operations: server, retention, backup, deploy

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Server templates

`deploy/eve-trader.service.template` carries the systemd sandboxing directives
(no `ProtectHome`: the repo lives in the deploy user's home). `setup.sh` runs a
fresh install as a system user `eve-trader` (no shell, no sudo; ACLs give it
read on the repo and write on `data/`), keeps the `User=` of an existing unit,
and `APP_USER` overrides both; `deploy.sh` never assumes deploy and service
user are the same. Bare-host backups use `EVE_TRADER_PG_BACKUP_USER` (default
`postgres`); Docker mode keeps `-U postgres`. nginx has an HTTP and an HTTPS
template (`setup.sh` picks the HTTPS one when a certificate exists); both have
a 1 MiB body cap, the `eve_trader_timing` log with the request id and proposal
`limit_req` zones (general `/api/`, strict for solver/rebuild POSTs).

## Error log and data retention

`error_log` (unscoped, `docs/observability_schema.sql`) keeps the newest
`MAX_ERROR_LOG_ROWS_PER_TENANT` (500) rows per tenant (NULL tenant is its own
group) plus a global backstop of 5000; `storage.log_error` counts the tenant
on every insert and prunes only once it is 50 over. The Admin "Recent Errors"
view is grouped by source and normalized message (UUIDs and digit runs
ignored; `GET /api/admin/errors/groups`), the raw list stays under a toggle.
`POST /api/errors` needs a session but no tool grant.

`shortlist_snapshot` (per tenant) and `goonmetrics_history` (global) are
pruned once a day by scheduler jobs `shortlist_retention` and
`goonmetrics_retention` (master switch on; the per-tenant one also needs the
`trading` grant and an active tenant), keeping `TradingConfig.
history_retention_days` (90, floor 30) of shortlist runs and `TradingConfig.
price_history_retention_days` (365, floor 90) of Goonmetrics history (the
Trading price chart shows it all). Both are operator values, not on the
Settings page; the global job reads the Default tenant's. `storage.prune_*` delete in
chunks of 20,000 rows, one transaction each; `prune_shortlist_snapshot` never
deletes a tenant's newest run. Only the newest shortlist run is read.

## Backup

`eve_trader/backup.py`'s `create_backup()` zips a `pg_dump` (`-Fc`, custom/
compressed format - restorable via `pg_restore`) of the whole Postgres
database plus `config.yaml` into a timestamped `.zip` under `data/backups/`,
pruning down to `MAX_BACKUPS` (14) automatically. Stored SSO tokens inside the dump are encrypted and the key (`TOKEN_ENCRYPTION_KEY`, `.env`) is never in the ZIP: keep it in a password manager, a restore needs it. `data/tokens.json` is
**not** included - `TokenManager` persists to Postgres's `tenant_tokens`
table now, so the live tokens are already inside the dump; a separate,
possibly-stale file copy would be actively misleading on a restore. Shells
out to `docker exec <container> pg_dump -U postgres ...` - must run as the
Postgres *owner* role (`postgres`), not the app's own `eve_trader_app` role,
since RLS raises on a missing tenant setting rather than silently returning
zero rows (see "Multi-tenant Postgres" below) - a non-bypassing role
couldn't dump per-tenant tables at all. `EVE_TRADER_PG_CONTAINER`/
`EVE_TRADER_DOCKER_BIN` env vars override the container name/`docker`
binary path (default `"eve-trader-pg"`/`"docker"`). Both listing and
creating a backup live in the Admin tool (`admin.do_list_backups`/
`admin.do_create_backup`, `/api/admin/backups`) - moved off the Portfolio
page (confirmed real misplacement 2026-09-21): one `pg_dump` already
covers every tenant's data in one shot, so this is cross-tenant-impacting,
same reasoning as `/sde/refresh`/`/jita-price-cache/refresh` in that same
module. Both routes require the `admin` grant while the gate is on - no
more read/write split needing a one-off exception in `api/app.py`'s
`_required_tool_for_path` (the old `F-06` carve-out, now gone) - plus the
scheduler's own global backup job (opt-in via `DEFAULT_TENANT_ID`'s
`backup_interval_hours`).
A backup is written to `<name>.zip.partial` (matches no `*.zip` glob, so
`list_backups()` and both prefixes' pruning never see it), verified with
`testzip()`, fsynced and only then `os.replace`d to its final name; the
retention prune runs after that. `backup.cleanup_partial_files()` (app start,
not under pytest) deletes `.tmp_*` and `*.partial` older than 10 minutes.
Filenames include microseconds and a uuid so two backups cannot collide;
`pg_dump` stderr stays in the process log, not in the HTTP 400.

`create_backup(prefix=...)` has a second file-name family,
`eve_trader_predeploy_*` (`PREDEPLOY_BACKUP_PREFIX`, `eve-trader backup create
--pre-deploy`), with its own retention (`MAX_PREDEPLOY_BACKUPS` = 5) so several
deploys in one day cannot push the daily backups out of `MAX_BACKUPS`; the
Admin list (`list_backups`) deliberately does not show them. `deploy/deploy.sh`
calls it before the schema step whenever a `docs/*.sql` file changed since the
previous deploy (or `DEPLOY_BACKUP=1`) and aborts the deploy if it fails.

## Deploy, health and rollback

`deploy/deploy.sh` (steps and rollback semantics are in its header comment and
`deploy/README.md`; recovery procedures in `docs/RUNBOOK.md`). Settled rules:
- It updates by `git merge --ff-only` to `DEPLOY_REF` (default
  `origin/$DEPLOY_BRANCH`), never `git pull`, and refuses on modified tracked
  files. The code update and the state record (`data/deploy-state/`,
  untracked) come *before* the re-exec guard, as `git pull` did.
- The schema files run in one `psql -1` transaction (checked: no `CONCURRENTLY`,
  `ADD VALUE` or own `BEGIN`/`COMMIT`). tests/pg_helpers.py parses the
  `for f in phase1_schema.sql ...; do` loop - keep its shape.
- `frontend/dist` is replaced atomically from `dist.new`; `dist.prev` serves
  `deploy.sh rollback`. Rollback never undoes database changes (schema is
  additive). Deploy tags `deploy-<UTC>` are local only, the deploy key is
  read-only.
- The script never `chmod`s, `chown`s or `sed -i`s `config.yaml`, `.env` or
  `data/` (ACL-managed on a server); only `docs/*.sql` is `chmod 644`.
- `GET /api/health` is public (in `_GATE_EXEMPT_PATHS`) and answers only
  `{"status": "ok"}` or 503 `{"status": "degraded"}` from a `SELECT 1` with a
  2 s pool timeout; version, backup age, last ESI sync and scheduler state are
  behind the `admin` grant at `GET /api/admin/health`
  (`admin.do_get_health_detail`). Do not add such details to the public one.
- Every HTTP response carries `X-Request-ID` (`api/request_id.py`, outermost
  middleware; a client value is accepted only if it matches
  `^[A-Za-z0-9-]{8,64}$`, else a uuid4 hex). It lives in
  `request_context.request_id_var` (inherited by `copy_context` threads) and
  logging_setup's filter prints it as `[%(request_id)s]` (`-` outside a
  request).
- Dependencies: `requirements.txt` is runtime only, `requirements-dev.txt` adds
  the test packages (CI and `.cursor/install.sh` install it); deploy and the
  start scripts install `requirements.lock` (= `-r requirements.txt`). No hash
  pins yet (they need a Linux-generated file covering x86_64 and aarch64).

The scheduler is fully on in production since 2026-10-02 (`config.yaml`
`scheduler_enabled: true` plus the Default tenant's `tenant_settings` override).
`create_backup()` chmods its output to 0600 itself (a non-interactive SSH run
does not get `umask 077`).
