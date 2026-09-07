# Production / Special-Order semantic freeze (online)

Frozen baseline for the Production Special-Order core after Phase C
(adversarial audit) and Phase D (release readiness) on the **multi-tenant
web app**. Change any rule here only as an explicit product decision, with
tests updated in the same change.

Implementation lives in `eve_trader/production/actions.py` and
`eve_trader/production/engine.py`. Isolation is Postgres RLS (`tenant_id`
on `special_orders` / `special_order_items` / `special_order_events`).

## Data flow

```
Frontend / FastAPI / CLI
        ↓
production/actions.py
        ↓
do_set_special_order_item          (upsert only — no plan)
do_remove_special_order_item       (delete one line — no plan)
do_compute_special_order           (one stored order)
do_compute_combined_special_orders (unsaved pooled preview)
        ↓
_plan_special_order_items
        ↓
engine.plan_special_order
        ├── Buy/Build (_expand_all)
        └── Invention (_invention_need_row)
```

Routers and the React Special Orders page must not call `plan_special_order`,
`_expand_all`, or `_invention_need_row` themselves.

## Semantic freeze

### SF-1 — B1 (top-level hangar)

Top-level items of a special order are **never** netted against hangar
stock. Ordered quantity N always seeds N units of demand.

### SF-2 — Combined preview

`Preview(A+B)` is **one** planner run over pooled line items. It is not
`Preview(A) + Preview(B)`. The combined result is not persisted.

### SF-3 — Pooling

Identical `type_id`s are summed. Pooling must not last-write-wins.

### SF-4 — Preview purity

Compute and Combined do not mutate stored orders, line items, flags, stock,
or decryptor overrides.

### SF-5 — `net_against_stock`

For **combined** preview, `net_against_stock` is a per-call argument. A
stored per-order flag is used **only** by `do_compute_special_order`.

### SF-6 — Upsert / remove

`do_set_special_order_item` upserts; quantity `<= 0` is an error; does not
plan. `do_remove_special_order_item` errors on unknown type / last item.

### SF-7 — Invention single source of truth

`_invention_need_row` is the only constructor of `InventionNeedRow`.

### SF-8 — Shared planner path

API and UI reach the engine only through the actions above.

## CURRENT POLICY — missing market prices

If no market quotes exist and `_buy_or_build_decision` cannot form a
`build_cost`: unpriced Buy, BOM not expanded, quantity kept, state unchanged.

## Tenant isolation

Special-order tables are per-tenant with RLS. Events, audit, and CRUD never
cross `app.tenant_id`. Combined preview is still unsaved and still
tenant-scoped (it only loads the caller's orders).

## Release regression

```
pytest -m release
```
