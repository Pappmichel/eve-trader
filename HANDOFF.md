# HANDOFF - multi-tenant migration in progress

Temporary note for resuming this work in a new Claude Code session (possibly on a
different machine) with no access to this machine's Claude memory. **Delete this file once
the multi-tenant migration is fully done and merged** - until then, keep it updated at each
pause point instead of leaving it stale.

## Where things stand

- Full architecture plan: `docs/MULTI_TENANT_PLAN.md` (committed, durable - read this
  first, it has the full context/reasoning, plus **four** real dialect gotchas discovered
  building Phases 0-1 (`SET LOCAL` param rejection, `IS NULL` parameter typing, Postgres
  `INTEGER` being 32-bit, psycopg3's `Connection` missing `executemany()`).
- Work happens on git branch **`multi-tenant`** (created from `main`, not yet merged).
  `main` and the live Oracle VM deployment are untouched and must stay that way until an
  explicit, separate go-ahead to cut over.
- This repo lives inside Dropbox (`C:\Users\marvi\Dropbox\Eve\eve_trader`), so the working
  tree - **including `.git`** - syncs across machines automatically. That's convenient but
  not fully reliable for git's internal state if Dropbox syncs mid-write. Treat Dropbox
  sync as a convenience mirror, not the source of truth - on a new machine, `git fetch &&
  git checkout multi-tenant` rather than trusting whatever Dropbox happened to sync.
  **Local is currently 3 commits ahead of `origin/multi-tenant`** (this session's Phase 1
  work - `origin` is still at `6b1d316`, the last pushed commit) - not yet pushed as of
  this note. Push before resuming on a different machine, or `git fetch` there will not
  see this session's work at all (Dropbox sync alone does carry the commits, per above,
  but don't rely on that being complete/consistent - confirm with `git log` after
  fetching). Standing rule: commit locally freely, push when it's instrumentally needed -
  it is, for exactly this cross-machine-resume reason.

## Local dev environment set up this session

**On a different machine, none of this exists yet** - Docker/Postgres/`.venv` are all
machine-local, not synced by Dropbox or git. Re-run: install Docker Desktop (needs WSL2
first, `wsl --install` as admin, then reboot), `docker run ...` below (schema applies
itself automatically the first time `pytest` runs - see below), and `pip install -e .`
(picks up `psycopg`/`psycopg_pool` from `pyproject.toml`, which *is* committed).

- **Docker Desktop installed** on this machine (needed WSL2 first - already done).
- **Postgres running** in a container named `eve-trader-pg` (`docker run -d --name
  eve-trader-pg -e POSTGRES_PASSWORD=devpassword -e POSTGRES_DB=eve_trader -p
  5432:5432 postgres:16`). If it's not running on a resumed session:
  `docker start eve-trader-pg` (container persists after `docker stop`/machine restart -
  only re-run the full `docker run` if the container was removed).
- **Full schema applied**: `docs/phase1_schema.sql` (supersedes `phase0_setup.sql` for
  schema purposes - all 37 tables, idempotent). Applied automatically by
  `tests/pg_helpers.py`'s session fixture on every `pytest` run, so no manual step is
  normally needed - only re-apply by hand if working outside pytest:
  `Get-Content docs\phase1_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader`
- **`psycopg[binary]` + `psycopg_pool`** added to `pyproject.toml`, installed into `.venv`.

## Progress against the phases in docs/MULTI_TENANT_PLAN.md

- [x] Branch `multi-tenant` created
- [x] `docs/MULTI_TENANT_PLAN.md` written and committed
- [x] **Phase 0 - done, live-verified.** `eve_trader/pg_tenant.py` (pool + contextvar +
      `set_config`-based tenant scoping + `?`→`%s` placeholder shim) +
      `tests/test_pg_tenant_isolation.py` (4/4 passing against the real local Postgres) -
      proves RLS isolation, the widened composite-PK `ON CONFLICT`, and the fail-closed
      "no tenant set" error, all through real code, not just raw SQL.
- [x] **Phase 1 - DONE.** Schema, isolation tests, and the actual `storage.py` cutover to
      Postgres are all complete:
      - `docs/phase1_schema.sql` - the complete Postgres schema for all 37 tables, bucketed
        per the plan's three categories (shared/no-RLS, composite-PK, column-only+no-PK).
        Idempotent (verified twice in a row against the live container).
      - `tests/pg_helpers.py`/`tests/conftest.py` - reusable fixtures (`tenant_pair`,
        `tenant`, `clean_tables`, `wipe_tables`, `postgres_required()`) + automatic schema
        provisioning - no manual `psql` step needed to run the Postgres tests.
      - All ~24 per-tenant tables have a passing isolation test (`test_pg_tenant_isolation.py`,
        `test_pg_composite_pk_tables.py`, `test_pg_column_only_and_no_pk_tables.py` - 27
        tests total).
      - **The cutover itself**: `pg_tenant.py`'s pool/contextvar/`SET LOCAL`-equivalent/
        placeholder-shim logic is now merged directly into `storage.py` (file deleted) -
        `storage.py`'s real `connect()`/`batch_session()` talk to Postgres, not SQLite.
        `db_path` removed from all ~78 function signatures; `DB_PATH` survives only as a
        legacy constant for `backup.py`'s still-SQLite-based online-backup API (Phase 4).
        The 11 composite-PK `ON CONFLICT` targets were widened in the real functions; 2
        SQLite-only `INSERT OR REPLACE`/`INSERT OR IGNORE` rewritten to Postgres
        `ON CONFLICT` syntax; 7 bare positional inserts got explicit column lists; the 2
        `pd.read_sql_query` sites replaced with manual `fetchall()` + `pd.DataFrame(...)`.
      - Converted all 7 SQLite-`tmp_path`-based storage test files (122 occurrences) to a
        fresh-tenant-per-test model, including a full rework of
        `test_storage_batch_session.py` (now counts `psycopg_pool` checkouts instead of
        `sqlite3.connect()` calls). Also had to fix ~20 more tests across
        `test_production_engine.py`/`test_production_unlisted_stock.py` that never
        touched `db_path` at all but still needed a tenant - `@storage.with_batch_session()`
        and any uncached `@lru_cache` SDE lookup (`get_system_security` etc.) opens a real
        pooled connection unconditionally, even when every individual `storage.*` call in
        the test is monkeypatched.
      - **Full suite: 329 passed** (up from 330 - one net test removed,
        `test_batch_session_raises_when_nested_for_a_different_db_path`, whose premise
        (`db_path` existing at all) no longer applies), stable across repeated runs, both
        with Postgres up and down (**241 passed/88 skipped** with it stopped - clean skips,
        zero failures either way).
      - **Live-verified the accepted trade-off**: started the real API server and hit a
        real endpoint - got a clean HTTP 500 with `RuntimeError: ... no current tenant
        set`, not a crash and not silently-wrong data. Confirms the fail-closed design
        works as intended; the live app is genuinely unusable until Phase 3, exactly as
        agreed with the user before starting this work.
      - Two more dialect gotchas found live during the cutover (now documented in
        `docs/MULTI_TENANT_PLAN.md`'s gotcha list): psycopg3's `Connection` has no
        `executemany()` (fixed once, centrally, in `_TranslatingConnection`); and
        column-only-bucket tables don't get free test isolation from a fresh tenant_id the
        way composite-PK-bucket tables do (their PK was deliberately left un-widened, so a
        hardcoded test id can still collide across tenants at the physical PK level) -
        fixed with `pg_helpers.wipe_tables()`.
- [x] **Phase 2 - DONE.** Config/secrets moved off `config.yaml` and into Postgres:
      - `docs/phase2_schema.sql` - `tenant_settings` (JSONB `overrides` per tenant+scope,
        not a wide per-field-column table - matches `save_config_overrides`'s existing
        "merge a diff dict" semantics) and `tenant_tokens` (schema only - `TokenManager`
        itself deliberately stays file-based, see below). Idempotent, depends on
        `phase1_schema.sql`'s `eve_trader_app` role already existing (`tests/pg_helpers.py`'s
        new `_apply_phase2_schema` fixture explicitly depends on `_apply_phase1_schema` to
        guarantee ordering, not just fixture-collection luck).
      - `eve_trader/config.py`'s new `ConfigProxy` class - `TRADING_CONFIG`/
        `PRODUCTION_CONFIG` are now proxies over a `contextvars.ContextVar`
        (`__getattr__`/`__setattr__` both forward), not plain dataclass instances. All 92
        `cfg: TradingConfig = TRADING_CONFIG`-style call sites across 20 files needed **zero
        changes** - confirmed live (both modules import cleanly, a real GET
        `/api/trading/settings` returns real values with no tenant context set at all, since
        the ContextVar's `default=` is exactly the one shared instance the app already used).
        `AccessConfig` stays a plain instance, not proxied - operator-only, becomes the
        tenant registry in Phase 3.
      - Fixed the `type(cfg)` break the plan flagged in advance: `validate_config_overrides`
        gained an optional `cfg_type` parameter (defaults to `type(cfg)`, correct for every
        existing test/loader call site - only `do_update_settings`, which now passes a
        proxy, needs it explicit).
      - `save_config_overrides` (YAML-writing) retired, replaced by
        `save_tenant_config_overrides` (validate -> `storage.save_tenant_settings` ->
        `apply_config_overrides`) - swapped into `actions.py`/`production/actions.py`'s
        `do_update_settings` *and* `production/actions.py`'s `do_set_system` (a third,
        easy-to-miss caller of the old function).
      - New `storage.save_tenant_settings`/`load_tenant_settings` - upsert-merges via
        Postgres's JSONB `||` operator, using `psycopg.types.json.Jsonb` to serialize
        (psycopg3 doesn't auto-adapt a plain `dict` to `jsonb` the way it auto-*deserializes*
        one back on read).
      - 2 new tests prove the actual round-trip (a real `do_update_settings` call lands in
        `tenant_settings` and is readable back; Trading/Production saves don't clobber each
        other, since `scope` is part of the PK) - the 3 pre-existing reject-invalid tests
        needed **zero changes**.
      - **Full suite: 331 passed** (329 + 2 new), stable with Postgres up and down (241
        passed/90 skipped when down).
      - **Live-verified**: real GET `/api/trading/settings` works with no tenant set (proxy
        fallback); real POST to save fails with the same "no tenant set" `RuntimeError`
        every other write does post-Phase-1 - expected, matches the accepted trade-off, not
        a new regression.
      - **Decided before starting**: `tenant_tokens` table exists but `TokenManager` stays
        file-based (`data/tokens.json`) - wiring it up needs a tenant_id at the OAuth
        callback, which is Phase 3's job (the plan's own "OAuth callback has no tenant
        context" section already flags this).
- [x] **Phase 3a - DONE.** Tenant resolution / access-gate / OAuth-callback (`gate` branch
      only) / admin CLI provisioning:
      - `docs/phase3_schema.sql` - `tenants` + `tenant_registry_entries` (deliberately not
        RLS-scoped - they're the directory used to resolve a tenant *before* one exists).
        Seeds `DEFAULT_TENANT_ID` (`00000000-0000-0000-0000-000000000001`) idempotently.
      - `storage.py` - `DEFAULT_TENANT_ID`, `connect_unscoped()` (a `connect()` sibling that
        doesn't require/set an ambient tenant_id - narrow, deliberately-documented escape
        hatch used only by the tenant-registry functions), `create_tenant`,
        `add_tenant_registry_entry` (upsert), `resolve_tenant_id(character_id,
        corporation_id, alliance_id)`, `list_tenants`, `list_tenant_registry_entries`.
      - `access_gate.py` - session cookie now signs `tenant_id` too; `is_allowed()` and
        `AccessConfig`'s 3 `allowed_*_ids` list fields retired entirely, replaced by
        `storage.resolve_tenant_id(...)`.
      - `api/app.py`'s `AccessGateMiddleware` - now *unconditionally* sets storage's ambient
        tenant contextvar every request: `DEFAULT_TENANT_ID` when the gate is off (today's
        default) or the path is exempt, the cookie's resolved `tenant_id` otherwise.
      - `auth.py`'s `/callback` - only the `role_prefix == "gate"` branch changed
        (`is_allowed` -> `storage.resolve_tenant_id`). Buyer/seller/producer branches and
        `/start`'s `_pending` dict are untouched - see deferred item below.
      - `cli.py` - new `tenant create`/`tenant add-entry`/`tenant list` admin commands;
        `main()`'s group callback sets `storage.DEFAULT_TENANT_ID` once per process (every
        existing command gets a working tenant context for free).
      - Tests: `test_access_gate.py` rewritten (cookie calls take `tenant_id`, `is_allowed`
        tests removed); `test_tenant_registry.py` (new, 8 tests) covers the removed
        `is_allowed` functionality's replacement; `test_gate_router.py` rewritten
        (`_enable_gate`/`_session_cookie` no longer touch `AccessConfig` allowlists; the 3
        `callback_gate_branch` tests register real rows via
        `storage.add_tenant_registry_entry` against real Postgres instead of monkeypatching).
      - **Full suite: 334 passed** with Postgres up, **233 passed/101 skipped** (clean, zero
        failures) with it stopped.
      - **Live-verified the headline claim**: with the gate disabled (today's default), a
        real CLI command (`eve-trader refresh-shortlist`) and a real HTTP request
        (`GET /api/portfolio/overview`, which queries Postgres) both now succeed end-to-end -
        this didn't work after Phase 1 (by design) and does now.
      - **Decided before starting**: `TokenManager`'s actual switch to `tenant_tokens`, and
        threading `tenant_id` through `/start`'s `_pending` dict + `/callback`'s
        buyer/seller/producer branches, deferred to their own session - nothing would
        consume a `tenant_id` there yet.
- [x] **Phase 3b - DONE.** `TokenManager` cut over to Postgres, `tenant_id` threaded
      through the buyer/seller/producer OAuth flow:
      - `eve_trader/auth.py` - `TokenManager.__init__` no longer eagerly loads
        (construction is now storage-free); `_ensure_loaded()` lazily reads
        `storage.load_all_tenant_tokens()` on first real access
        (`get_token`/`has_token`/`get_record`/`list_roles`); `_save()` became
        `_save_record(role)` (single-row upsert, not a whole-file rewrite);
        `remove_token` is now an unconditional (idempotent) delete. The old
        one-time "producer" -> "producer:<id>" on-disk migration was dropped from
        the runtime load path and moved into a new `import_tokens_file(tenant_id,
        path=None)` helper instead.
      - `storage.py` - `save_tenant_token`/`load_all_tenant_tokens`/`delete_tenant_token`
        (same pattern as `tenant_settings`'s functions); `get_current_tenant()` (read-only
        contextvar accessor); `with_current_tenant(fn)` - fixes a real gap found while
        researching this phase: `ThreadPoolExecutor` worker threads don't inherit
        contextvars from the submitting thread (confirmed via cpython's `_WorkItem.run`),
        so a token expiring *during* a parallelized ESI fetch
        (`production/esi_sync.py`'s `sync_esi`, `esi_client.py`'s `_get_all_pages`) would
        have 500'd with "no tenant set" from inside a worker thread. Applied at both
        `ThreadPoolExecutor` call sites.
      - `api/routers/auth.py` - `/start` stashes `storage.get_current_tenant()` into
        `_pending[state]["tenant_id"]` for every role_prefix; `/callback`'s non-gate
        branch wraps its `TokenManager` persistence in
        `with storage.tenant_context(pending.get("tenant_id") or DEFAULT_TENANT_ID):`.
      - `cli.py` - new `eve-trader tenant import-tokens` command (one-time cutover
        helper - hit a real naming collision live: `cli.py` already defines an `auth`
        *command* function that shadows a module-level `auth` import, so the fix
        imports `import_tokens_file` directly rather than the `auth` module).
      - New `tests/test_token_manager.py` (8 tests, real Postgres) + 2 new tests in
        `tests/test_gate_router.py` (`/start` capturing ambient tenant;
        `/callback`'s buyer branch persisting under the right tenant, isolated from a
        second tenant via `tenant_pair`).
      - **Decided before starting**: one-time import of the real local `data/tokens.json`
        (18 real records) into `tenant_tokens` under `DEFAULT_TENANT_ID`, rather than a
        clean cutover with no import - preserves today's live local dev SSO sessions.
      - **Full suite: 344 passed** (334 + 10 new) with Postgres up, **234 passed/110
        skipped** (clean, zero failures) with it stopped.
      - **Live-verified**: `eve-trader tenant import-tokens` imported all 18 records;
        real `GET /api/auth/status` returned the real buyer/seller names
        (`jason Andven`/`pappmichl`) straight from Postgres, no re-authorization; real
        `GET /api/production/producer-characters` returned all 16 real producer
        characters.
      - **Explicitly out of scope**: `backup.py` still zips the now-stale
        `data/tokens.json` (Phase 4's `pg_dump` rework); `scheduler.py`'s own
        pre-existing "no tenant in its background thread" gap (Phase 4).
- [x] **Phase 4 - DONE.** Scheduler multi-tenant loop, a real config-resolution gap
      closed, the SQLite->Postgres ETL script, `backup.py`'s `pg_dump` rework:
      - New `eve_trader/tenant_scope.py`'s `enter_tenant(tenant_id)` - sets storage's
        tenant contextvar *and* resolves/sets `TRADING_CONFIG`/`PRODUCTION_CONFIG`'s
        per-tenant instance together (`config.py`/`production/config.py` gained
        `resolve_and_set_trading_config`/`resolve_and_set_production_config` + `reset_*`).
      - **Real gap found while researching this phase**: `TRADING_CONFIG`/
        `PRODUCTION_CONFIG`'s `ContextVar`s (Phase 2) were never actually `.set()`
        anywhere - every tenant silently shared one in-memory config instance.
        Confirmed with the user before fixing it as part of this phase (not just the
        plan doc's original narrower "scheduler" scope).
      - Applied *only* where the bug is reachable: `AccessGateMiddleware`'s gate-
        *enabled* branch uses full `tenant_scope.enter_tenant` (a request could be any
        of several real tenants there). The gate-*disabled* branch (this app's
        default) deliberately keeps the original lightweight
        `storage.set_current_tenant` only - every request is structurally
        `DEFAULT_TENANT_ID` there, so the bug can't occur, and the full resolution
        would have cost every gate-disabled request a real Postgres round-trip -
        **confirmed live this broke `tests/test_api_routers.py`'s "safe to run
        anywhere" contract for ~30 tests**, caught by running the full suite with
        Postgres down (not just up) before calling this done, then fixed by narrowing
        the scope. A one-time `_load_default_tenant_config()` at `api/app.py`'s
        lifespan startup (mutating the shared default config instance directly, not
        via `.set()`) still closes the "Settings save survives a restart" gap for the
        default-tenant case, with no per-request cost. `scheduler.start()` gets the
        same one-time-at-boot treatment for its own `scheduler_enabled` check.
      - `scheduler.py` - `trading_pipeline`/`production_sync` iterate
        `storage.list_tenants()` each tick, each tenant's own `scheduler_enabled`/
        interval fields deciding independently; `backup` stays a single **global**,
        unscoped job (one `pg_dump` already covers every tenant), governed by
        `DEFAULT_TENANT_ID`'s own config. `last_run_status` is now `{tenant_id:
        {job_name: {...}}}`; a separate `_backup_status` covers the global job.
      - New `eve_trader/sqlite_migration.py` - generic, table-driven ETL
        (`_PER_TENANT_TABLES`, not 24 hand-written functions) for all 24 per-tenant
        tables. `eve-trader migrate-sqlite <db-path> [--tenant-id ...]`.
        **Live-verified against a real copy of this machine's actual (stale,
        pre-Phase-1) `data/eve_trader.db`** - 165,056 real rows migrated in one run
        (2,184 shortlist items, 9,162 corp assets, 66,972 new-candidate rows, ...);
        re-running confirmed PK-bearing tables don't duplicate, no-PK tables do
        (matches a real pipeline re-run).
      - `backup.py` dropped `sqlite3`/`storage.DB_PATH` entirely (removed from
        `storage.py` too) - now shells out to `docker exec <container> pg_dump -U
        postgres ... -Fc` (the *owner* role - RLS raises on a missing tenant setting,
        so the app's own non-bypassing role can't dump per-tenant tables at all).
        `EVE_TRADER_PG_CONTAINER`/`EVE_TRADER_DOCKER_BIN` env vars (this machine's
        `docker.exe` isn't on `PATH` - confirmed live, installed under a non-default
        path). `data/tokens.json` dropped from the zip entirely (dead now that
        `tenant_tokens` is the real store). **Live-verified**: a real `create_backup()`
        produced a valid `PGDMP`-header dump + `config.yaml`, no `tokens.json`.
      - **Full suite: 354 passed** (344 + 10 new) with Postgres up, **235 passed/119
        skipped** (clean, zero failures) with it stopped. One pre-existing, unrelated
        flaky test noticed (`test_read_session_token_rejects_tampered_token` - a
        base64-last-character tamper test, confirmed passing 5/5 solo reruns and
        confirmed unrelated to anything touched this phase) - not fixed, flagged as
        pre-existing test debt.
- [ ] Phase 5 - deploy docs. **Not started - the last phase in the plan doc.**

## Immediate next step

Phases 1 through 4 are all done - the app is genuinely multi-tenant end to end:
`storage.py`, `TRADING_CONFIG`/`PRODUCTION_CONFIG`, and `TokenManager` all resolve
per-tenant correctly (gate disabled -> `DEFAULT_TENANT_ID`; gate enabled -> real
per-tenant resolution via the registry, now including config, not just storage), the
scheduler runs each tenant's jobs independently, and backups/migration tooling are
Postgres-native. Only Phase 5 remains per `docs/MULTI_TENANT_PLAN.md`: deploy docs -
document the owner/app-role separation, connection pool sizing, and the `SET LOCAL`/
`app.tenant_id` discipline required of any *new* storage function added after this
migration (a future contributor forgetting this is the main long-term risk of this
design). The actual live-deployment cutover (running `migrate-sqlite` for real, pointing
the live Oracle VM at Postgres) is explicitly a separate, later decision beyond Phase 5 -
not started, not planned as part of this document's scope.
