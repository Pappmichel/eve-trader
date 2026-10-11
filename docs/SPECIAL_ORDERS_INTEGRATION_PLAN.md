# Special orders in the regular build workflow - plan

Status: **done** (2026-10-11). Decisions **[Q1]**..**[Q4]** were confirmed
with the user on 2026-10-11 (all as recommended, see the end of this file).
Implemented on branch `cursor/special-orders-integration-3535` (phases 1–5);
semantic freeze updates live in `docs/PRODUCTION_SEMANTICS.md` (SF-9, SF-10).

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

Filter (confirmed 2026-10-11): a three-state control next to the Category /
Tech Level filters - **All / Orders only / Without orders**. "Orders only"
shows jobs with `special_order_runs > 0`, with the order share in the
run/ready columns and a "Special orders" column listing the order notes;
"Without orders" shows the stock-target share of every job that has one. "Split Into" stays per job (one blueprint, one in-game job).
Optional: a MultiSelect to pick single orders.

### D8 - Regular Build List / Buy List **[Q4]**

Recommendation: a "Special order" quantity column on Build List and Buy List
rows (no filter there unless wanted). The inventory/stock-target view stays
stock-targets only.

### D9 - Staleness

The cached plans (`_last_plan` / `_last_asset_plan`) don't recompute
themselves. Toggling the checkbox, editing items of an integrated order,
changing its status and deleting it set `stale` on both cached plans. They
stay readable (Logistics, Invention and the order progress read them); the
Build / Buy / Asset-Optimized pages and the Special Orders page show a
recompute hint while the flag is set, and the next refresh clears it.

### D10 - Invention: one row per item

`_invention_need_row` nets the owned and in-progress T2 BPC runs inside each
row. A Tech II item that is both a stock target and an order line must
therefore get **one** row with the combined BPC target
(`stock target x bpc_inventory + order quantity`), not two rows that both
subtract the same owned BPCs (the same double count the Crow fix in that
function already guards against for another case).

### D11 - Market restock keeps ordered units

`market_restock` reserves `backup_stock` before it suggests hauling finished
goods to C-J for listing. Units an integrated order claims (D3) are reserved
the same way, first, so an ordered ship is never suggested for the market.

### D12 - Stock target view shows the reservation

When an order claims finished units of an item that is also a stock target,
the stock target sees less current stock (D3). The inventory rows get a
"Reserved for orders" figure so the lower number is explained, not a
surprise.

### D13 - Several orders for the same item: oldest first

Finished units (D3) and scarce readiness are given to orders by
`created_at`, oldest first, so the result is deterministic and the order
that waited longest is served first.

### D14 - Jita price cache covers integrated orders

`jita_price_cache` prices the material closure of the stock targets only.
It also takes the closure of integrated order items; otherwise those are
priced live on every plan (slower, and possibly against different quotes
than the rest of the plan).

### D15 - Bookkeeping

`order_integrity.audit`, the `sqlite_migration` column list and the event
log learn the new column; deleting an integrated order marks the cached
plans stale like the other order changes (D9).

### D16 - Progress per order (confirmed 2026-10-11)

The Special Orders page shows, per integrated order and line item, from the
last computed Asset-Optimized plan: units already in the hangar, units in
running jobs (both from D3's claim), runs ready to start, and what is still
missing. When every line is fully in the hangar the order gets a "Ready to
deliver" badge (a hint only - marking it `done` stays manual). Needs the
plan to keep the per-order D3 claims next to the jobs (`order_progress` in
the plan result), read from the cached plan - no extra computation on page
load.

## Phases

1. **Data + toggle**: column, storage read, action + event, forced
   `net_against_stock`, API/schema/types, checkbox on the order page
   (header and list column), staleness hint, bookkeeping (D15). Tests: storage round-trip,
   event, toggle forces `net_against_stock`, done/reopen.
2. **plan_production**: order demand (D3, oldest first D13), two-pass
   `_expand_all` (D4), margin bypass (D5), invention rows merged per item
   (D6, D10), attribution on Build/Buy rows (D8), reservation figure (D12),
   market restock reservation (D11), Jita price cache (D14). Tests: no overbuild on order demand, buffer unchanged for a shared
   component, orders consume stock first, top-level netting incl. running
   jobs, done order drops out, margin bypass, invention row multiplier 1.0.
3. **plan_asset_optimized**: two-pass `_run_rounds` with shared ledgers,
   readiness priority, attribution fields, slot priority (D7). Tests:
   readiness goes to order claims first, order runs never wait while
   stock-target runs are queued, fewer-slots-than-jobs ordering.
4. **Frontend**: All / Orders only / Without orders filter + columns,
   Build/Buy List column, progress per order with "Ready to deliver" badge
   (D16), hide `stock_overlap_warning` for integrated orders.
5. **Docs**: new SF rule(s) in `docs/PRODUCTION_SEMANTICS.md` (integration,
   D3 exception to SF-1 if chosen), `docs/KNOWN_LIMITATIONS.md` (rounding,
   manual `done`), CLAUDE.md summary. Live check on the test server with a
   real order before production.

## Decisions (confirmed with the user 2026-10-11)

Optional additions: progress per order (D16) and the three-state filter (D7)
are in; a due date per order and a Discord "order ready" alert were
considered and left out for now.

- **[Q1]** Integrated orders net their top-level quantity against stock
  including running jobs, the order claiming first (D3). SF-1 stays for the
  order's own preview.
- **[Q2]** Order demand ignores the margin gate (D5).
- **[Q3]** Slot priority as in D7: order jobs start first, and with a days
  target their order runs are fully queued before stock-target runs.
- **[Q4]** Build List / Buy List get a "Special order" quantity column only,
  no filter (D8).
