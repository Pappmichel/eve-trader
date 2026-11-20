# Multi-tenant Postgres

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Multi-tenant Postgres: rules for new storage/schema code

Full design history and rationale lives in `docs/MULTI_TENANT_PLAN.md`
(read this if you need the "why", not just the "what" below) - this section
is the durable, quick-reference version for anyone adding a new storage
function or table after that migration (all 5 of its phases are done).

- **`storage.DEFAULT_TENANT_ID`** (`00000000-0000-0000-0000-000000000001`,
  seeded into `tenants` by `docs/phase3_schema.sql`) is the fixed tenant
  used whenever there's no real per-request tenant to resolve: the CLI
  (`cli.py`'s `main()` sets it once per process - a trusted single
  operator, no login wall there at all) and every web request when
  `AccessConfig.access_gate_enabled` is `False` (only when
  `EVE_TRADER_ALLOW_GATE_OFF` is set; a leftover `access_gate_enabled:
  false` in `config.yaml` is forced on at load — see P5-05 /
  `apply_access_gate_policy`. The gate itself is **on by default**). A real per-tenant login
  (gate enabled) resolves and uses a different, real `tenant_id` from the
  registry instead. `DEFAULT_TENANT_ID` does **not** imply any tool grant
  (including `"admin"`). First-admin recovery is `eve-trader admin
  bootstrap` (`admin.do_bootstrap_admin`), never an HTTP backdoor and never
  flipping the gate off.
- **One tenant per character, enforced at the DB level.**
  `tenant_registry_entries.tenant_id` has a `UNIQUE` constraint
  (`docs/admin_schema.sql`) - no two characters can ever share a tenant, by
  DB guarantee, not just UI convention. The Admin tool's "Add User"
  (`admin.do_add_user`) reflects this: it always creates a brand-new
  tenant (named after the character's own ESI-resolved name) as part of
  adding a user, there's no "assign to an existing tenant" flow anymore -
  `admin.do_create_tenant`/the standalone "Create Tenant" UI were removed
  for the same reason. `admin.do_remove_user` deliberately does *not*
  delete the now-permanently-orphaned tenant or its data - it deregisters
  the character and leaves the tenant's data intact, with one deliberate
  exception: the tenant's stored SSO tokens (every role, alts included) and its
  Discord link are deleted at once (`storage.delete_all_tenant_tokens`,
  `delete_alert_destination`), and the refresh tokens are then revoked at SSO
  on a best-effort basis (`auth.revoke_refresh_tokens`; a failure is only
  counted, the local deletion never waits for it, and an undecryptable row is
  deleted without a revoke). The result carries `tokens_deleted`, `revoked`,
  `revoke_failed`, `revoke_skipped`, `discord_unlinked`. A second, separate
  exception to "reversible over destructive": `admin.do_purge_tenant`
  (preview first, `GET/POST /api/admin/tenants/{id}/purge[-preview]`, CLI
  `eve-trader admin purge-tenant ID --confirm NAME`) deletes an orphaned
  tenant and all its rows for good. It is refused for the default tenant, the
  acting admin's own tenant, a tenant with any registry entry (remove the user
  first) and a wrong confirmation name. Tables come from the catalog (every
  public base table with a `tenant_id` column), so new tables are covered
  without a list; RLS tables go in one transaction, the unscoped ones and the
  `tenants` row after it, so running it again finishes an interrupted purge.
  Rolling backups keep the data until they rotate out.
- **Stored SSO tokens are encrypted** (`eve_trader/token_crypto.py`, key list
  `TOKEN_ENCRYPTION_KEY` in `.env`, first key encrypts, all decrypt). The
  encryption happens inside `storage.save_tenant_token` /
  `load_all_tenant_tokens`, so every caller is covered; the stored form is
  `{"v": 1, "ct": ...}` and the ciphertext carries the tenant id and role, both
  checked on read. It fails closed: no key, a bad key, a plaintext row or a
  failed decryption raise `TokenCryptoError` (never skipped, never plaintext),
  and that error is deliberately neither a dead owner nor a transient ESI
  failure (no stale clear). The app refuses to start without a key and, outside
  pytest, when any row is not decryptable (counts only in the log). Operator
  commands: `eve-trader tenant encrypt-tokens [--check]`, `rotate-token-key`,
  `decrypt-tokens --write-plaintext` (rollback only); `deploy.sh` stops the
  service, encrypts and starts it. See `docs/RUNBOOK.md` section 5. Do not add a
  `CHECK (record ? 'ct')` constraint while a rollback to older code is possible.
- **Two Postgres roles, never conflate them.** `eve_trader_app`
  (`storage.PG_DSN`) is what the app itself connects as for every real
  query - `NOSUPERUSER NOBYPASSRLS`, so it can *never* accidentally see
  another tenant's rows even from a bug, only from Postgres itself
  correctly refusing. `postgres` (the owner role) is used *only* for schema
  DDL (`docs/phase*_schema.sql`, applied by hand/CI, never by the running
  app) and `backup.py`'s `pg_dump` (a whole-database dump necessarily
  bypasses RLS - see "Backup" above). Never give the app role `BYPASSRLS`
  or run app queries as the owner "to make something easier" - that defeats
  the entire isolation guarantee this migration exists for.
- **No database credentials in code.** `storage.PG_DSN` is
  `EVE_TRADER_PG_DSN` (read after `load_dotenv()`) with no default: the
  module imports without it (CLI `--help`, tests that never touch the
  database), but `_get_pool()` raises `RuntimeError`. `phase1_schema.sql`
  creates `eve_trader_app` with no password; every install sets one with
  `ALTER ROLE`. `tests/conftest.py` sets a local-dev DSN with `setdefault`
  before any `eve_trader` import and `tests/pg_helpers.py` sets the role's
  password to match, so tests need no manual setup. `phase1_schema.sql`
  grants on an explicit table list (no `ON ALL TABLES`, which re-widened
  narrowed tables on every deploy); each later schema file grants on its own
  tables, and `tests/test_db_credentials_timeout.py` fails when a table in
  `public` is not selectable by the app role. `error_log` has no `UPDATE`
  (`observability_schema.sql` revokes it).
- **Statement timeout.** App connections carry `statement_timeout` 60 s
  (`EVE_TRADER_STATEMENT_TIMEOUT_MS`, `0` = off; skipped when the DSN has its
  own `options`). `storage.long_running()` (a plain `ContextVar`, inherited by
  `with_current_tenant` workers and `copy_context` threads) makes
  `connect()`, `connect_own_transaction()`, `batch_session()` (after its
  isolation statement, which must stay first) and `connect_unscoped()` run
  `set_config('statement_timeout', '0', true)`. It is enabled in
  `pipeline_runner._execute`, the scheduler's job worker, the mail archive
  thread, the startup warm-up threads, `admin.do_apply_sde` and the CLI
  (`storage.enable_long_running()`). A new background entry point or a
  synchronous route that runs a long single statement must enable it too.
- **Every real query goes through `storage.connect()`/`storage.
  batch_session()`.** These are the only two places that check out a pooled
  connection and set `app.tenant_id` (via `SELECT set_config(...)`, the
  parameterized equivalent of `SET LOCAL` - see `connect()`'s own
  docstring for why not literal `SET LOCAL`) from the ambient
  `contextvars.ContextVar`, fail-closed (`RuntimeError`) if none is set.
  Never open a raw `psycopg`/`psycopg_pool` connection anywhere else in
  `storage.py` or elsewhere in the app - doing so would silently bypass
  this entirely, and nothing would catch it (Postgres itself only enforces
  RLS based on `app.tenant_id` actually being set on *that* connection).
  `batch_session(isolation="repeatable read")` runs the whole batch on one
  snapshot (the realized ledger loader reads the archive that way so a sync
  committing mid-load cannot give a rebuild rows of two moments); nothing in
  such a block may write what a sync also writes, and a nested batch asking
  for it inside a weaker one raises `RuntimeError`.
  `storage.connect_unscoped()` is a narrow, deliberate exception for the
  handful of genuinely tenant-independent tables (`tenants`,
  `tenant_registry_entries`) - don't reach for it for anything else.
- **Adding a new per-tenant table** needs, in its `CREATE TABLE`: a
  `tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id',
  false)::uuid` column, `ENABLE ROW LEVEL SECURITY`, and a
  `tenant_isolation` policy (`USING`/`WITH CHECK` both comparing
  `tenant_id = current_setting('app.tenant_id', false)::uuid`) - copy the
  shape of any existing table in `docs/phase1_schema.sql`, don't write it
  from scratch. Decide whether its primary key needs widening to
  `(tenant_id, ...)` - only if the "natural" key (without tenant_id) could
  plausibly collide across tenants (an EVE item type ID, a literal
  scope/category string) - see that same file's "composite-PK bucket" vs.
  "column-only bucket" comment banners for real examples of each.
- **A brand-new schema file** (a new top-level `docs/*_schema.sql`, not an
  addition to an existing one) needs adding to every place that applies
  schema files, not just the file itself - confirmed real, repeated bug
  class: `deploy/deploy.sh`'s migration for-loop, `deploy/README.md`'s and
  the root `README.md`'s manual `psql`/`docker exec` instructions, and
  `.cursor/start.sh`'s dev-environment loop. Forgetting one of these is a
  silent gap, not a loud error - the app runs fine until the first request
  that touches the new table, which then 500s with an `UndefinedTable`/
  "relation does not exist" error that has nothing to do with whatever
  changed in that request (see `docs/pipeline_runs_schema.sql`'s own
  2026-09-11 addition, and the pre-existing `station_trading_schema.sql`
  incident `deploy/deploy.sh` documents inline - both were exactly this).
  Grep the repo for an existing schema filename (e.g.
  `production_buy_list_schema`) to find every place a new one needs adding
  alongside it, rather than assuming `deploy/deploy.sh` is the only list.
- **`tenant_scope.enter_tenant(tenant_id)`** is the one place that resolves
  a tenant fully - storage's own ambient tenant *and* `TRADING_CONFIG`/
  `PRODUCTION_CONFIG`'s per-tenant instance together. Used by
  `AccessGateMiddleware` (only its gate-*enabled* branch - see
  `api/app.py`'s own comment for why the gate-disabled default path
  deliberately stays on the cheaper `storage.set_current_tenant`-only path)
  and `scheduler.py`'s per-tenant job iteration. If you add a new place
  that needs to "become" a specific tenant for a stretch of code (a new
  background job, a new admin script), use this, not a bare
  `storage.set_current_tenant` - a table added in the future might need its
  config resolved too, and this is the one chokepoint that stays correct
  automatically.
- **Connection pool sizing**: `storage._get_pool()` opens one
  `psycopg_pool.ConnectionPool(PG_DSN, min_size=1, max_size=10)` per
  process, shared across every request/job. 10 is a Phase-1-era default,
  not the result of load testing - this app's traffic today is a handful of
  invite-only tenants, well under that. If concurrent load ever routinely
  saturates it (connections queuing, visible as request latency spikes
  under concurrent Trading/Production dashboard use), raise `max_size`
  first before anything more invasive - don't raise `min_size` preemptively,
  it only trades connection-pool cold-start latency for idle-connection
  overhead on Postgres's side, not something to guess at without a measured
  problem.
- **`storage.with_current_tenant(fn)` carries the whole context.** It snapshots
  `contextvars.copy_context()` on the calling thread when it wraps and runs
  every call in its own `ctx.copy()` (one Context cannot be entered by two
  threads), so the tenant id *and* the per-tenant `TRADING_CONFIG` /
  `PRODUCTION_CONFIG` values travel into worker threads. The snapshot is
  taken at wrap time: wrap after `enter_tenant`, not before. Per-thread state
  (`production/engine._planner_reads_var`, `esi_client._last_modified_seen`) is
  registered with `storage.register_thread_local_contextvar` and starts at its
  default in the worker; register any new per-run memo or collector the same way.
- **Refresh tokens before a batch, not inside it.** `orchestrator.
  _run_character_owner` / `_run_corporation_kinds_for_members` call
  `TokenManager.ensure_fresh(role)` (refreshes below 300 s of validity) for
  every role they will use *before* `storage.batch_session()` opens: a refresh
  inside the batch needs a second pooled connection for `save_tenant_token`
  and ten such owners exhausted the pool of 10. A failing pre-refresh is only
  logged; the lazy refresh in the fetch stays as the fallback. The save after
  a successful SSO refresh retries `PoolTimeout`/`OperationalError` 3 times and
  then keeps the new record in memory (SSO may already have rotated the
  refresh token). Pool size stays 10.
- **`ThreadPoolExecutor`/raw `threading.Thread` don't inherit contextvars**
  from the thread that spawned them (confirmed live, twice, in this
  migration - `production/esi_sync.py`'s `sync_esi` and `esi_client.py`'s
  `_get_all_pages`, both fixed via `storage.with_current_tenant(fn)`). If
  you parallelize anything that might transitively touch `storage.py`
  (directly or via `TokenManager`'s lazy-refresh path), wrap the submitted
  callable in `storage.with_current_tenant(...)` - don't assume the ambient
  tenant "just carries over" into a worker thread.
