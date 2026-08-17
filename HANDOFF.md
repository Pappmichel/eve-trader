# HANDOFF - multi-tenant migration in progress

Temporary note for resuming this work in a new Claude Code session (possibly on a
different machine) with no access to this machine's Claude memory. **Delete this file once
the multi-tenant migration is fully done and merged** - until then, keep it updated at each
pause point instead of leaving it stale.

## Where things stand

- Full architecture plan: `docs/MULTI_TENANT_PLAN.md` (committed, durable - read this
  first, it has the full context/reasoning, plus the Phase 0 results and two real
  dialect gotchas discovered while building it).
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
- **Phase 0 schema/role applied**: `docs/phase0_setup.sql` (creates the non-owner
  `eve_trader_app` role + the RLS-enabled `stock_targets` table with a composite
  `(tenant_id, type_id)` PK). Re-apply with:
  `Get-Content docs\phase0_setup.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader`
  (only needed if the container/DB was recreated from scratch).
- **`psycopg[binary]` + `psycopg_pool`** added to `pyproject.toml`, installed into `.venv`.

## Progress against the phases in docs/MULTI_TENANT_PLAN.md

- [x] Branch `multi-tenant` created
- [x] `docs/MULTI_TENANT_PLAN.md` written and committed
- [x] **Phase 0 - done, live-verified.** `eve_trader/pg_tenant.py` (pool + contextvar +
      `set_config`-based tenant scoping + `?`→`%s` placeholder shim) +
      `tests/test_pg_tenant_isolation.py` (4/4 passing against the real local Postgres) -
      proves RLS isolation, the widened composite-PK `ON CONFLICT`, and the fail-closed
      "no tenant set" error, all through real code, not just raw SQL.
- [~] **Phase 1 - started, schema + test fixture slice done.** Full Phase 1 (port ~24
      per-tenant tables + 12 shared SDE tables + rework the ~136-occurrence test isolation
      model + the actual `storage.py` cutover) is still its own multi-day sub-effort - what's
      done so far is the mechanical foundation, not the table-by-table port itself:
      - `docs/phase1_schema.sql` - the **complete** Postgres schema for all 37 tables
        (supersedes `docs/phase0_setup.sql` for schema purposes, that file stays as the
        historical Phase-0 record), correctly bucketed per the plan's three categories
        (shared/no-RLS, composite-PK, column-only+no-PK). Idempotent - verified by running
        it twice in a row against the live container with zero errors.
      - `tests/pg_helpers.py` - reusable fixtures (`tenant_pair`, `clean_tables`,
        `postgres_required` skip marker) plus a session-scoped autouse fixture that applies
        `phase1_schema.sql` automatically - the manual `Get-Content | docker exec ... psql`
        step is no longer a prerequisite for running the Postgres tests.
      - `tests/conftest.py` - new file (didn't exist before), registers the above fixtures
        project-wide.
      - `test_pg_tenant_isolation.py` refactored onto the new helpers - same 4 tests, still
        passing, proves the extraction didn't break anything.
      - Full `pytest` suite: **307 passed**, no regressions.
      - **Not yet done**: per-table isolation tests for the other ~23 tables, the 2
        `pd.read_sql_query` call sites (`storage.py:1444`, `1452`), and - the big one -
        actually rewiring `storage.py`'s `connect()`/`batch_session()` to Postgres (only
        happens once every table is proven, per the plan).
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

Schema + fixtures are done for all 37 tables (see above) - the remaining Phase 1 work is
writing the actual per-table isolation tests (mirroring `test_pg_tenant_isolation.py`'s
`stock_targets` tests, now much less boilerplate thanks to `tests/pg_helpers.py`) table by
table, one bucket at a time. Once all ~24 per-tenant tables have a passing test, merge
`pg_tenant.py`'s logic into `storage.py`'s actual `connect()`/`batch_session()` and retire
the SQLite `SCHEMA` string.
