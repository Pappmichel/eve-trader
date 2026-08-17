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
- [ ] Phase 2 - config/secrets into Postgres + `TRADING_CONFIG`/`PRODUCTION_CONFIG` proxy
      objects (watch the `type(cfg)` break at `config.py:172`, already documented in the
      plan). **Not started. This is the next phase to pick up.**
- [ ] Phase 3 - tenant resolution / access-gate / OAuth-callback tenant-threading / admin
      CLI provisioning. **Not started.** (This is what actually makes the live app usable
      again - Phase 1's cutover deliberately left it non-functional until this lands.)
- [ ] Phase 4 - scheduler multi-tenant loop + migration *tooling* (not a live migration -
      test against a copy of the SQLite file only) + `backup.py`'s real Postgres
      (`pg_dump`-based) rework. **Not started.**
- [ ] Phase 5 - deploy docs. **Not started.**

## Immediate next step

Phase 1 is fully done - `storage.py` runs on Postgres for real now, proven by the full test
suite. Start Phase 2: create `tenant_settings`/`tenant_tokens` tables (add them to
`docs/phase1_schema.sql`'s pattern, or a new `docs/phase2_schema.sql`), build the
`TRADING_CONFIG`/`PRODUCTION_CONFIG` `contextvars`-backed proxy objects, fix
`config.py:172`'s `type(cfg)` call site (pass the concrete dataclass type explicitly
instead of deriving it), and swap `do_update_settings`'s persistence backend from the
YAML-file write to a `tenant_settings` UPDATE. Acceptance test per the plan: a
Settings-page save with a bad value still fails with `ConfigError` through the proxy,
exactly as it does today.
