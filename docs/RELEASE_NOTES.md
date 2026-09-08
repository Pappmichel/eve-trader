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
