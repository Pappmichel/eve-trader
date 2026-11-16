# Runbook

Operator procedures for a deployed instance. Everything here is a manual
procedure; nothing in the app alerts on its own (a deliberate decision).
Commands assume the repo root and the virtualenv at `.venv`. Hostnames,
addresses and passwords are placeholders: `<domain>`, `<deploy-user>`,
`<password>`.

Related: `deploy/README.md` (install and deploy), `docs/OPERATOR_SECURITY.md`
(gate bootstrap), the docstring of `eve_trader/backup.py` (how a backup is
made and why a restore needs the schema re-applied).

## 1. Restore from a backup

A backup is a zip under `data/backups/` holding `eve_trader.dump` (a
`pg_dump -Fc` of the whole database, every tenant) and `config.yaml`. Files
are named `eve_trader_backup_*.zip` (scheduled and Admin) or
`eve_trader_predeploy_*.zip` (taken by `deploy.sh` before a schema change; the
last 5 are kept; they are not shown in the Admin list).

### 1a. Restore drill (do this first, on a throwaway database)

The drill proves a backup is readable without touching the live database.

```bash
unzip -o data/backups/<backup>.zip eve_trader.dump -d /tmp/drill   # any private scratch dir
sudo -u postgres createdb eve_trader_restore_drill
sudo -u postgres pg_restore --no-owner --no-privileges \
    -d eve_trader_restore_drill /tmp/drill/eve_trader.dump
```

`--no-privileges` skips the dump's GRANTs, so the app role cannot read the
copy until you grant it:

```bash
sudo -u postgres psql -d eve_trader_restore_drill \
    -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO eve_trader_app"
```

Point a one-off script at the copy with an `EVE_TRADER_PG_DSN` environment
override that swaps `dbname=` (never edit the real `.env`) and call real
storage functions (`list_tenants()`, a tenant-scoped `connect()`), comparing
row counts with the live database. Then:

```bash
sudo -u postgres dropdb eve_trader_restore_drill
rm -rf /tmp/drill
```

On a Docker-based Postgres replace `sudo -u postgres <cmd>` with
`docker exec -i <container> <cmd> -U postgres` as in `backup.py`.

### 1b. Real restore

1. `sudo systemctl stop eve-trader`.
2. Keep the current state in case the restore is wrong: take one more backup
   (`.venv/bin/eve-trader backup create`) unless the database is unusable.
3. Extract the dump: `unzip -o data/backups/<backup>.zip eve_trader.dump -d <private dir>`.
4. `sudo -u postgres pg_restore --clean --if-exists -d eve_trader <private dir>/eve_trader.dump`
   (Docker: `docker exec -i <container> pg_restore -U postgres -d eve_trader
   --clean --if-exists < eve_trader.dump`).
5. **Re-apply every schema file, in the order of the loop in
   `deploy/deploy.sh`.** A restore brings back the schema as it was when the
   backup was taken, not today's; `pg_restore` alone silently reverts newer
   columns and primary keys. Easiest: `./deploy/deploy.sh` with
   `DEPLOY_REF` set to the commit you are running (or run the loop's
   `psql -1 -v ON_ERROR_STOP=1 -f docs/<file> ...` command by hand).
6. Set the app role's password again (`ALTER ROLE eve_trader_app PASSWORD
   '<password>'`; it must match `EVE_TRADER_PG_DSN` in `.env`). The restore
   may have replaced the role's password with the backup's.
7. `sudo systemctl start eve-trader`, then check `curl
   http://127.0.0.1:8000/api/health` returns `{"status":"ok"}`.
8. Verify in the app: gate login works with your character, a tool page that
   reads the database loads (for example the Ore & Minerals mineral list), and
   `GET /api/admin/health` shows a sensible version and sync time.

`config.yaml` in the zip is a copy for reference. Compare it with the live
file; do not overwrite the live file blindly (it is ACL-managed on a server).

## 2. Rotating secrets

Change one secret at a time and confirm the app is healthy before the next.
`.env` and `config.yaml` are ACL-managed on a server: edit them in place with
an editor, do not replace them with `sed -i` or a copy (that drops the ACL and
owner). Then `sudo systemctl restart eve-trader`.

| Secret | Where | Effect of rotating |
|---|---|---|
| `SESSION_SECRET_KEY` | `.env` | Every session cookie becomes invalid: **everyone is logged out** and must log in again. Nothing else breaks. |
| `EVE_SSO_CLIENT_SECRET` | `.env` and the EVE developer portal application | Generate the new secret in the portal, put it in `.env`, restart. Logins and ESI token refreshes fail between the portal change and the restart. Stored refresh tokens stay valid. |
| App role password (`eve_trader_app`) | database role and `EVE_TRADER_PG_DSN` in `.env` | `ALTER ROLE eve_trader_app PASSWORD '<password>'`, update the DSN in `.env`, restart. Requests fail until both match. |
| Owner role / backup role password | database role and `~/.pgpass` of the user that runs backups | Update both. Backups (scheduled, Admin, `deploy.sh`) fail until `~/.pgpass` matches. The running app is not affected. |
| `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_SECRET` | `.env` and the Discord developer portal | Reset in the portal, update `.env`, restart. Discord alerts stop until the restart; linked accounts stay linked. |
| Deploy key (read-only Git deploy key) | the repository's deploy-key settings and the server's SSH key | Create a new key pair on the server, add the public half as a read-only deploy key, remove the old one, test `git fetch`. Deploys fail while the keys do not match. |
| Dynamic-DNS update token | the DNS provider and wherever the update job on the server stores it | Replace the token at the provider, then in the job. The host name stops resolving to the server after the next IP change if the two differ. |
| Token-encryption key | _Reserved: this section is completed by the change that introduces encrypted token storage._ | |

After any rotation: `curl http://127.0.0.1:8000/api/health`, log in once, and
open one page that calls ESI.

## 3. Emergency admin access

If no character holds the `admin` grant (or the only admin lost access), on
the server:

```bash
.venv/bin/eve-trader admin bootstrap \
  --character-id <EVE character id> --character-name "<name>" --all-tools --confirm
```

It needs only the database, not the HTTP API, and never turns the access gate
off. Details and options: `docs/OPERATOR_SECURITY.md`. Confirm with
`.venv/bin/eve-trader tenant list`.

## 4. Deploys, pre-deploy backup and rollback

`./deploy/deploy.sh` deploys the fast-forward of `origin/main` (or
`DEPLOY_REF`). When a `docs/*.sql` file changed since the previous deploy (or
`DEPLOY_BACKUP=1`) it first takes a backup with `eve-trader backup create
--pre-deploy`; the path is printed, the file is mode 0600 under
`data/backups/`, and the last 5 are kept. If that backup fails the deploy stops
before the first migration. The schema files then run in one transaction.

After the restart the script polls `GET /api/health`. On failure it prints the
last journal lines and the rollback command; nothing rolls back by itself.

```bash
./deploy/deploy.sh rollback           # previous deploy
./deploy/deploy.sh rollback <sha>     # a specific commit
```

A rollback restores code and frontend only. **It does not undo database
changes**: schema files are additive, so the older code runs against the
newer schema. If data must return to its earlier state, restore the pre-deploy
backup with section 1b (the schema step in 1b is then a no-op for the older
code).

`data/deploy-state/` holds `previous_sha`, `current_sha` and `dist_prev_sha`.
`git tag -l 'deploy-*'` lists the local deploy tags (never pushed).

Operator health detail (admin session): `GET /api/admin/health` returns the
version, the age of the newest backup, the newest ESI sync and whether the
scheduler thread is running.
