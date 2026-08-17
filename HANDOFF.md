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
- [ ] Phase 3 - tenant resolution / access-gate / OAuth-callback tenant-threading / admin
      CLI provisioning, **and** `TokenManager`'s actual switch to `tenant_tokens`.
      **Not started. This is the next phase to pick up** - and the one that actually makes
      the live app usable again (Phases 1-2 deliberately left it non-functional until this
      lands).
- [ ] Phase 4 - scheduler multi-tenant loop + migration *tooling* (not a live migration -
      test against a copy of the SQLite file only) + `backup.py`'s real Postgres
      (`pg_dump`-based) rework. **Not started.**
- [ ] Phase 5 - deploy docs. **Not started.**

## Immediate next step

Phases 1 and 2 are both done - `storage.py` runs on Postgres for real, and
`TRADING_CONFIG`/`PRODUCTION_CONFIG` persist to `tenant_settings` instead of `config.yaml`.
Start Phase 3: extend the access-gate session cookie (`eve_trader/access_gate.py`) to carry
`tenant_id`, resolved at login against a new tenant registry (character/corp/alliance ID ->
tenant_id) instead of today's one global `AccessConfig`; have `AccessGateMiddleware`
(`eve_trader/api/app.py`) set the request's tenant contextvar right after validating the
cookie; fix the `/api/auth/callback` tenant-context gap (`_pending[state]` dict in
`eve_trader/api/routers/auth.py` needs to carry `tenant_id` through from `/start`); switch
`TokenManager` (`eve_trader/auth.py`) over to `tenant_tokens`; build the admin CLI
(`eve-trader tenant create ...`) that provisions a tenant registry entry only. This is the
phase where the live app actually becomes usable again - worth live-verifying end to end
(a real login, a real Settings save) once it's done, not just passing tests.
