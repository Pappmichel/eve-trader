# HANDOFF - multi-tenant migration in progress

Temporary note for resuming this work in a new Claude Code session (possibly on a
different machine) with no access to this machine's Claude memory. **Delete this file once
the multi-tenant migration is fully done and merged** - until then, keep it updated at each
pause point instead of leaving it stale.

## Where things stand

- Full architecture plan: `docs/MULTI_TENANT_PLAN.md` (committed, durable - read this
  first, it has the full context/reasoning, plus the Phase 0 results and three real
  dialect gotchas discovered while building it - the third (Postgres `INTEGER` is 32-bit,
  real ESI object IDs need `BIGINT`) was found this session).
- Work happens on git branch **`multi-tenant`** (created from `main`, not yet merged).
  `main` and the live Oracle VM deployment are untouched and must stay that way until an
  explicit, separate go-ahead to cut over.
- This repo lives inside Dropbox (`C:\Users\marvi\Dropbox\Eve\eve_trader`), so the working
  tree - **including `.git`** - syncs across machines automatically. That's convenient but
  not fully reliable for git's internal state if Dropbox syncs mid-write. Treat Dropbox
  sync as a convenience mirror, not the source of truth: **the `multi-tenant` branch is
  already pushed to `origin` on GitHub** (confirmed: `origin/multi-tenant` HEAD matches
  local HEAD, commit `174845b`) - on a new machine, `git fetch && git checkout
  multi-tenant` rather than trusting whatever Dropbox happened to sync. If you make more
  commits on this branch in a future session, push again (standing rule: commit locally
  freely, push when it's instrumentally needed - it is here, for exactly this
  cross-machine-resume reason).

## Local dev environment set up this session

**On a different machine, none of this exists yet** - Docker/Postgres/`.venv` are all
machine-local, not synced by Dropbox or git. Re-run: install Docker Desktop (needs WSL2
first, `wsl --install` as admin, then reboot), `docker run ...` below, apply
`docs/phase0_setup.sql`, and `pip install -e .` (picks up `psycopg`/`psycopg_pool` from
`pyproject.toml`, which *is* committed).

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
- [~] **Phase 1 - started, schema + all 24 per-tenant tables' isolation tests done.** Full
      Phase 1 (port ~24 per-tenant tables + 12 shared SDE tables + rework the
      ~136-occurrence test isolation model + the actual `storage.py` cutover) is still its
      own multi-day sub-effort - progress so far:
      - `docs/phase1_schema.sql` - the **complete** Postgres schema for all 37 tables
        (supersedes `docs/phase0_setup.sql` for schema purposes, that file stays as the
        historical Phase-0 record), correctly bucketed per the plan's three categories
        (shared/no-RLS, composite-PK, column-only+no-PK). Idempotent - verified by running
        it twice in a row against the live container with zero errors. Real ESI object ID
        columns (`item_id`/`job_id`/`order_id`/`location_id`/`output_location_id`/
        `installer_id`) are `BIGINT`, not `INTEGER` - see the plan's 3rd dialect gotcha,
        caught live by a test using a realistic structure ID.
      - `tests/pg_helpers.py` - reusable fixtures (`tenant_pair`, `clean_tables`,
        `postgres_required` skip marker) plus a session-scoped autouse fixture that applies
        `phase1_schema.sql` automatically - the manual `Get-Content | docker exec ... psql`
        step is no longer a prerequisite for running the Postgres tests, **including on a
        freshly (re)created container with no schema/role yet** - `_postgres_available()`
        had to be fixed to probe via the owner role (always exists) instead of the app role
        (only exists after the schema fixture runs), otherwise every Postgres test wrongly
        self-skipped at collection time on a brand-new container. Confirmed live.
      - `tests/conftest.py` - new file (didn't exist before), registers the above fixtures
        project-wide.
      - `test_pg_tenant_isolation.py` refactored onto the new helpers - same 4 tests, still
        passing, proves the extraction didn't break anything.
      - `tests/test_pg_composite_pk_tables.py` - isolation tests for all 10 remaining
        composite-PK-bucket tables (`stock_targets` itself already covered by
        `test_pg_tenant_isolation.py`) - 7 parametrized simple-upsert tests, 2 `ON CONFLICT
        DO NOTHING` tests (`shortlist_skip_streak`, `category_location_options`), 1 COALESCE
        upsert test (`shortlist`, mirrors `stock_targets`'s).
      - `tests/test_pg_column_only_and_no_pk_tables.py` - isolation tests for the remaining
        13 tables (8 column-only-bucket + 5 no-PK-bucket) - the key thing proven here is
        that an **unfiltered** `DELETE FROM {table}` (the real pattern every
        `replace_*`/`save_*` function for these tables uses, since their PK was never
        tenant_id-widened) only ever touches the calling tenant's own rows, purely via RLS.
        Also caught a real test-hygiene bug: this bucket's hardcoded PK literals (e.g.
        `item_id=100001`) can collide across *separate pytest sessions* (not just within
        one), because a leftover row from an earlier passing run isn't tied to the new
        run's fresh `tenant_pair` - fixed by cleaning up after each test too, not just
        before (the composite-PK bucket doesn't strictly need this, since tenant_id is
        part of its PK, but got the same fix for consistency/hygiene).
      - **All ~24 per-tenant tables now have a passing isolation test** - full `pytest`
        suite: **330 passed**, no regressions (confirmed stable across 2 consecutive runs,
        proving the cleanup fixes actually stop garbage accumulation).
      - **Not yet done**: the 2 `pd.read_sql_query` call sites (`storage.py:1444`, `1452`),
        and - the big one - actually rewiring `storage.py`'s `connect()`/`batch_session()`
        to Postgres (only happens once every table is proven, per the plan - which is now
        true, so this is the next real milestone).
- [ ] Phase 2 - config/secrets into Postgres + `TRADING_CONFIG`/`PRODUCTION_CONFIG` proxy
      objects (watch the `type(cfg)` break at `config.py:172`, already documented in the
      plan). **Not started.**
- [ ] Phase 3 - tenant resolution / access-gate / OAuth-callback tenant-threading / admin
      CLI provisioning. **Not started.**
- [ ] Phase 4 - scheduler multi-tenant loop + migration *tooling* (not a live migration -
      test against a copy of the SQLite file only). **Not started.**
- [ ] Phase 5 - deploy docs. **Not started.**

Note: `eve_trader/storage.py`'s real `connect()`/`batch_session()` have **not** been
touched yet - `pg_tenant.py` is a deliberately separate module until Phase 1 finishes
porting every table (swapping storage.py over earlier would break the app for the 36
tables not yet in the new Postgres schema).

## Immediate next step

Every per-tenant table now has a passing isolation test - the schema-and-proof stage of
Phase 1 is done. What's left before Phase 1 is fully complete:

1. The 2 `pd.read_sql_query(..., conn)` call sites (`storage.py:1444`, `1452`) - pandas'
   SQL layer doesn't reliably support raw `psycopg` connections, replace with manual
   `cursor.fetchall()` + `pd.DataFrame(...)`.
2. **The big one**: merge `pg_tenant.py`'s pool/contextvar/`SET LOCAL`/placeholder-shim
   logic into `storage.py`'s actual `connect()`/`batch_session()`, and retire the SQLite
   `SCHEMA` string. Note one thing this session's tests didn't need to deal with but the
   real cutover will: several `storage.py` writes use bare `INSERT INTO table VALUES
   (?,?,?,?)` (positional, no explicit column list - e.g. `replace_character_slots`,
   `replace_blueprints`, `replace_sell_orders`) - once `tenant_id` is a real column (with
   a server-side `DEFAULT`), those need an explicit column list or they'll silently
   misalign against the new column order. `tests/test_pg_column_only_and_no_pk_tables.py`
   sidestepped this by always listing columns explicitly - the real port doesn't get to.
3. Rework the test suite's SQLite `tmp_path`/`db_path` isolation model (~136 occurrences)
   to a Postgres equivalent once storage.py itself is on Postgres.
