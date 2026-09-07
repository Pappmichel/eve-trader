# Phase E — Controlled feature expansion (online)

The Production Special-Order **core is frozen**. `docs/PRODUCTION_SEMANTICS.md`
(SF-1..SF-8) stays binding. Expansions sit **around** the core.

## Global constraints

- No edits to `engine.plan_special_order`, `_expand_all`, `_invention_need_row`,
  `_buy_or_build_decision`, or `_job_cost_rate` except E.5 setting existing
  config fields through `do_update_settings`.
- No new fields on `SpecialOrder` / `SpecialOrderLineItem`.
- Combined preview stays unsaved.
- This repo is multi-tenant Postgres. New tables need `tenant_id` + RLS.

## E.2 — Persistence

- Single-transaction create (`create_special_order_with_items` / `batch_session`).
- Create-time pooling of duplicate `type_id`s.
- `do_list_special_orders(status=None|"open"|"done")`.
- Append-only `special_order_events` (survives header delete).
- Read-only `order_integrity.audit()`.

## E.3 — UX

- Create by `type_id` or `name` / `type_id_or_name`.
- Frontend: status filter All/Open/Done, note save, buy-list total (display only).

## E.4 — Auto-recompute

- `preview_refresh.set_item_and_preview` / `remove_item_and_preview`.
- `PUT`/`DELETE` items `?recompute=true`.
- Frontend session checkbox. Wrapper must not import `engine`.

## E.5 — Job slots / cost indices

- Job-slot totals already come from ESI `character_slots` (`Slots` page:
  Total/Used/Free). No second manual table.
- Cost-index overrides already live in Production Settings; E.5 adds
  `do_set/clear/list_cost_index_override` wrappers over that path.
