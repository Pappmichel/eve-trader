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
  sync as a convenience mirror, not the source of truth: **push the `multi-tenant` branch
  to `origin` on GitHub too** (ask before pushing if that hasn't happened yet - standing
  rule is commit locally freely, push only when asked) so a resume on another machine can
  `git fetch && git checkout multi-tenant` instead of trusting whatever Dropbox synced last.

## Local dev environment set up this session

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
- [ ] Phase 1 - port the remaining ~24 per-tenant tables + 12 shared SDE tables + rework
      the test suite's isolation model (currently SQLite `tmp_path`-based, ~136
      occurrences - needs a Postgres equivalent). **Not started.**
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

Start Phase 1: pick the next per-tenant table (see docs/MULTI_TENANT_PLAN.md's two
buckets - composite-PK-needed vs. tenant_id-column-only) and repeat the Phase 0 pattern -
add to schema, widen conflict target if needed, add a test. Once all ~24 are ported,
merge `pg_tenant.py`'s logic into `storage.py`'s actual `connect()`/`batch_session()` and
retire the SQLite `SCHEMA` string.
