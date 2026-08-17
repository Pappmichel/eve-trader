# HANDOFF - multi-tenant migration in progress

Temporary note for resuming this work in a new Claude Code session (possibly on a
different machine) with no access to this machine's Claude memory. **Delete this file once
the multi-tenant migration is fully done and merged** - until then, keep it updated at each
pause point instead of leaving it stale.

## Where things stand

- Full architecture plan: `docs/MULTI_TENANT_PLAN.md` (committed, durable - read this
  first, it has the full context/reasoning, not just this checklist).
- Work happens on git branch **`multi-tenant`** (created from `main`, not yet merged).
  `main` and the live Oracle VM deployment are untouched and must stay that way until an
  explicit, separate go-ahead to cut over.
- This repo lives inside Dropbox (`C:\Users\marvi\Dropbox\Eve\eve_trader`), so the working
  tree - **including `.git`** - syncs across machines automatically. That's convenient but
  not fully reliable for git's internal state if Dropbox syncs mid-write (e.g. mid-commit).
  Treat Dropbox sync as a convenience mirror, not the source of truth: **the `multi-tenant`
  branch should also be pushed to `origin` on GitHub** (ask before pushing if that hasn't
  happened yet this session - standing rule is commit locally freely, push only when asked)
  so a resume on another machine can `git fetch && git checkout multi-tenant` instead of
  trusting whatever Dropbox happened to sync last.

## Blocker hit this session

**No Postgres available on this machine** - no Docker, no native `psql`/`pg_ctl`. Phase 0's
acceptance test (cross-tenant isolation on `stock_targets`) cannot actually be run/verified
here. Before continuing Phase 0 past writing code, resolve this - options: install Docker
Desktop, install Postgres natively, or use a free-tier cloud Postgres (e.g. Neon, Supabase)
for dev - this needs a decision from the user, not a unilateral choice (new account/service).

## Progress against the phases in docs/MULTI_TENANT_PLAN.md

- [x] Branch `multi-tenant` created
- [x] `docs/MULTI_TENANT_PLAN.md` written and committed
- [ ] Phase 0 - Postgres + RLS proof of concept on `stock_targets` - **blocked on Postgres
      availability, see above**
- [ ] Phase 1-5 - not started

## Immediate next step

Ask the user how they want to get a real Postgres instance to develop/test against (Docker
Desktop install / native install / free cloud instance), then proceed with Phase 0 exactly
as described in `docs/MULTI_TENANT_PLAN.md`.
