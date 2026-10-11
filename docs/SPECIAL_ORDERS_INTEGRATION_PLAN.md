# Special orders in the regular build workflow - plan

Status: **plan, not started** (2026-10-11). Open decisions are marked
**[Q1]**..**[Q4]** and need a user answer before Phase 2.

## Goal

A special order can opt in to the regular Production workflow with one
checkbox on the order ("Include in build lists"). While the box is ticked and
the order is `open`:

1. Its line items feed the regular Build List and Buy List
   (`plan_production`) and the Asset-Optimized Build List
   (`plan_asset_optimized`), next to the stock targets.
2. In the Asset-Optimized list its jobs are **prioritized** (stock and slots).
3. **No overbuild** (`component_overbuild`) is applied to demand that comes
   from orders.
4. The Asset-Optimized list can be filtered to **special-order jobs only**.

Unticked orders, and orders marked `done`, behave exactly as today. The
order's own page (Compute / Combined preview) keeps working as it does now.

## Today (what this builds on)

- `special_orders` (`order_id`, `note`, `net_against_stock`, `status`
  `open|done`) + `special_order_items` (`type_id`, `quantity`) +
  `special_order_events`, all RLS per tenant (`docs/special_orders_schema.sql`).
- `engine.plan_special_order` reuses the regular engine but is computed
  **separately**: no overbuild (`component_overbuild=0.0`), no margin gate,
  top-level quantity never netted against stock (SF-1), components netted
  only when `net_against_stock` is set, plus a `stock_overlap_warning`
  because the two plans don't know about each other.
- The overbuild buffer is sized from `_base_runs`, which is computed from
  `stock_targets` only (`_parent_base_runs_for_buffer`). Demand that never
  enters `_base_runs` therefore gets **no buffer** automatically - this is
  how goal 3 is met without a new code path.
- `docs/PRODUCTION_SEMANTICS.md` freezes the special-order rules (SF-1..SF-8);
  any change there is an explicit product decision with tests in the same
  change.

## Design

### D1 - Data model

- `special_orders.include_in_production BOOLEAN NOT NULL DEFAULT false`,
  added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the **existing**
  `docs/special_orders_schema.sql` (no new schema file, so no new entries in
  `deploy/deploy.sh` / READMEs / `.cursor/start.sh`).
- Integrated = `include_in_production AND status = 'open'`. Marking an order
  `done` takes it out of every plan; reopening puts it back.
- `do_update_special_order(..., include_in_production=...)` writes the flag
  and an event (`include_in_production on/off`), same as the existing
  `net_against_stock` toggle. `SpecialOrder` model / schema / `types.ts` /
  `sqlite_migration` column list get the field.
- New `storage.list_integrated_special_order_items()` -> rows
  `(order_id, note, type_id, type_name, quantity)` for open, included orders
  (one query, no per-header N+1).

### D2 - Asset sharing (`net_against_stock`) - recommendation: yes, forced

The question was whether the order's assets should be shared automatically
when integration is on. Recommendation: **yes - integrated orders always net
against stock, and the checkbox is locked on while integrated.**

- A pooled plan can't plan one share of a material "from scratch" and the
  other share against stock: the stock-target share would consume the hangar
  stock and the order share would be bought fresh, so the Buy List would ask
  for material that is already in the hangar.
- Both plans now see the same demand, so the "might be claimed twice"
  situation `stock_overlap_warning` exists for goes away for integrated
  orders (hide the warning for them).
- Turning integration on also sets `net_against_stock = true` (event logged),
  so the order's own preview matches what the integrated plan does; the
  checkbox is disabled with a tooltip while integrated. Turning integration
  off leaves `net_against_stock` as it is.
- ESI sharing (Characters page) needs nothing new: orders are part of the
  Production tool and read the assets already shared with `production`.

### D3 - Top-level quantity **[Q1]**

SF-1 says an order's ordered quantity is never netted against hangar stock.
That is right for a separate preview, but in the standing build lists it
breaks the flow: once the ordered ship is built and sits in the hangar (or
is still in a running job), the plan keeps asking for N more until the order
is marked `done` - the running job would be planned a second time.

Recommendation: for **integrated** orders only, net the top-level quantity
against current stock including running jobs (`_current_stock`), with the
order claiming first: `covered = min(order_qty, current_stock)`, the order
plans `qty - covered`, and a stock target for the same item sees
`current_stock - covered`. SF-1 stays as it is for the order's own preview.
Marking the order `done` after delivery is still the user's job (if it is
forgotten after the ship left the hangar, the order gets planned again).

Alternative: keep SF-1 everywhere and rely on marking orders `done` as soon
as their jobs are started. Simpler, but double-plans everything that is
already in production.

### D4 - Two passes, orders first (both build lists)

Each plan runs its demand walk twice, sharing one stock ledger:

1. **Order pass**: seed = integrated order demand (after D3), `base_runs = {}`
   (=> no overbuild buffer anywhere below it), no margin gate (D5).
2. **Stock-target pass**: today's seeding and buffer, against whatever stock
   the order pass left.

Applies to `plan_production` (two `_expand_all` calls, `buy_totals` /
`build_runs` summed) and `plan_asset_optimized` (two `_run_rounds` calls with
shared `stock_used` / `stock_used_on_hand`, jobs merged by `job_key`). Using
the same shape in both keeps the two lists agreeing on *how much* to build
(the existing "never disagree" rule).

- Priority on stock: the order pass consumes stock first in both lists, and
  gets readiness ("Ready Now") first in the Asset-Optimized list.
- Overbuild: the order pass uses an empty `base_runs` and its own
  `buffered_parents`, so no order demand is ever buffered; a component used
  by both still gets exactly the stock-target buffer it gets today.
- Cost: runs are rounded up per pass, so an item needed by both passes can
  get up to one extra run. Acceptable; documented.
- Alchemy byproduct pre-pass and pricing (`_PlanContext(extra_type_ids=...)`
  with the order items) cover both passes.
- Attribution comes for free: every job / buy row knows how much came from
  the order pass.

### D5 - Margin gate **[Q2]**

Recommendation: same as `plan_special_order` - order demand ignores
`cfg.min_margin`, and the order pass does not apply `margin_excluded` to its
materials (an order has to be delivered either way).

### D6 - Invention

A Tech II/III line item adds an `InventionNeedRow` to `invention_list` with
`bpc_buffer_multiplier = 1.0` (exactly the ordered quantity, like the order
preview), marked as coming from an order. Its datacores/decryptors then reach
the Buy List and Logistics through the existing
`_merge_invention_inputs_into_buy_totals` / `invention_logistics` path.

### D7 - Asset-Optimized priority and filter

New fields on `AssetPlanJob`: `special_order_runs`,
`special_order_ready_runs`, `special_order_ids` (+ schema / `types.ts`).

Slots (`_allocate_slots_min_makespan`) **[Q3]** - recommendation:
- With fewer free slots than jobs, jobs with order runs start first (ahead
  of unlock time).
- With a slot days target, a job with order runs first gets enough slots to
  queue **all its order runs** within the target, then the normal
  fastest-finish split runs for the rest. Runs over the cap that have to wait
  are always stock-target runs before order runs. Without a target, order
  jobs just take part in the normal split.

Filter: a "Special orders only" switch next to the Category / Tech Level
filters. When on, it shows only jobs with `special_order_runs > 0`, with the
order share in the run/ready columns and a "Special orders" column listing
the order notes. "Split Into" stays per job (one blueprint, one in-game job).
Optional: a MultiSelect to pick single orders.

### D8 - Regular Build List / Buy List **[Q4]**

Recommendation: a "Special order" quantity column on Build List and Buy List
rows (no filter there unless wanted). The inventory/stock-target view stays
stock-targets only.

### D9 - Staleness

The cached plans (`_last_plan` / `_last_asset_plan`) don't recompute
themselves. Toggling the checkbox, editing items of an integrated order and
changing its status mark them stale (the existing "recompute" hint), and the
Special Orders page says "Recompute the build lists to apply".

## Phases

1. **Data + toggle**: column, storage read, action + event, forced
   `net_against_stock`, API/schema/types, checkbox on the order page
   (header and list column), staleness hint. Tests: storage round-trip,
   event, toggle forces `net_against_stock`, done/reopen.
2. **plan_production**: order demand (D3), two-pass `_expand_all` (D4),
   margin bypass (D5), invention rows (D6), attribution on Build/Buy rows
   (D8). Tests: no overbuild on order demand, buffer unchanged for a shared
   component, orders consume stock first, top-level netting incl. running
   jobs, done order drops out, margin bypass, invention row multiplier 1.0.
3. **plan_asset_optimized**: two-pass `_run_rounds` with shared ledgers,
   readiness priority, attribution fields, slot priority (D7). Tests:
   readiness goes to order claims first, order runs never wait while
   stock-target runs are queued, fewer-slots-than-jobs ordering.
4. **Frontend**: "Special orders only" filter + columns, Build/Buy List
   column, hide `stock_overlap_warning` for integrated orders.
5. **Docs**: new SF rule(s) in `docs/PRODUCTION_SEMANTICS.md` (integration,
   D3 exception to SF-1 if chosen), `docs/KNOWN_LIMITATIONS.md` (rounding,
   manual `done`), CLAUDE.md summary. Live check on the test server with a
   real order before production.

## Open decisions

- **[Q1]** Net integrated orders' top-level quantity against stock incl.
  running jobs, order first (recommended), or keep SF-1?
- **[Q2]** Ignore the margin gate for order demand (recommended)?
- **[Q3]** Slot priority as in D7 (recommended)?
- **[Q4]** Build/Buy List: column only (recommended), or a filter as well?
