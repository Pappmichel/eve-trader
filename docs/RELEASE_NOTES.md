# Release notes — 0.3.0

Deploys and operations; no change to any calculated result.

- `deploy/deploy.sh` deploys a fixed target (`DEPLOY_REF`, fast-forward only),
  records previous and new commit, takes a pre-deploy backup when a schema
  file changed, applies the schema in one transaction, builds the frontend
  atomically, polls `GET /api/health` and has a `rollback` command. It also
  creates a local `deploy-<timestamp>` tag.
- `GET /api/health` (public, status only) and `GET /api/admin/health`
  (version, backup age, last sync, scheduler); every response carries an
  `X-Request-ID`, which also appears in the log lines.
- `eve-trader backup create [--pre-deploy]`.
- Runtime and test dependencies are split (`requirements.txt`,
  `requirements-dev.txt`); `docs/RUNBOOK.md` added.

Honest gap: until this version no deploy recorded which commit it put on the
server, no release tag was ever created (the `v0.2.0-rc1` tag mentioned below
and in older docs never existed), and a failed deploy had no way back. Earlier
deploys cannot be reconstructed from tags; use the server's reflog and the
deployment history of the hosting provider.

# Release notes — 0.2.0rc1

Production Special-Order path on the multi-tenant web app, certified after
Phases C–G (`docs/PRODUCTION_SEMANTICS.md`).

- Frozen planner: top-level hangar never netted, Combined is one unsaved
  planner run, pooling by `type_id`, preview purity, per-call Combined
  `net_against_stock`, invention via `_invention_need_row` only.
- E.2: transactional create, create-time pooling, status filter, append-only
  events (RLS), read-only audit.
- E.3: create by name, UI status filter / note save / buy totals.
- E.4: optional auto-recompute wrapper (`?recompute=true`).
- E.5: cost-index override actions; job-slot totals remain ESI `character_slots`.

Out of scope: persisting Combined, Always-Build on missing prices, changing
frozen engine helpers.
