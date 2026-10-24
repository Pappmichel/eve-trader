# Dynamic stock targets - plan

Status: **draft, nothing implemented.** Open decisions are in section 8 and
must be answered by the user before phase 1 starts.

## 1. Goal

Today every row on the Stock Targets page carries three fixed numbers
(`stock_targets.backup_stock`, `home_market_stock`, `jita_market_stock`) that
the user maintains by hand. The planners (`plan_production`,
`plan_asset_optimized`) build exactly up to those numbers, and the only
profitability input is the binary `min_margin` gate (below it: build nothing,
above it: build the full target).

Wanted: the **item list stays exactly as it is** (the user still decides
*what* is built), but the **quantities become dynamic**:

- an item that earns more (higher margin, more ISK/day, sells faster) gets a
  higher target, so more of it is built;
- an item that earns less gets a lower target, down to zero;
- no new items are added and none are removed automatically.

Non-goals:

- No automatic adding of items (that remains Build Candidates / Margin page).
- No selling, relisting or price changes. A lower target only means "build
  less / stop building"; stock already on hand stays.
- No change to *how* an item is built (buy-vs-build, ME/TE, invention,
  alchemy, slots). Only the top-level quantity fed into the planners changes.
- No Jita sales channel for Production (CLAUDE.md: Production sells only at
  C-J). The existing `jita_market_stock` column keeps its current meaning.

## 2. Where the quantities enter today (current state)

All target quantities come from one storage read,
`storage.load_stock_targets()` -> `(type_id, type_name, backup_stock,
home_market_stock, jita_market_stock)`. Consumers that use the *quantities*
(not just the ids):

| Consumer | File | Use |
|---|---|---|
| `_PlanContext.stock_targets` | `production/engine.py` | feeds `plan_production`, `plan_asset_optimized`, `_base_runs` (overbuild buffer), `_total_missing`, invention rows (`_invention_need_row`, stockpile %) |
| `market_status()` | `production/engine.py` | listed vs. home/Jita target |
| `market_listing_shortfall_by_type()` | `production/engine.py` | listing shortfall |
| `market_restock()` | `production/engine.py` | restock list for home targets |
| `stock_value()` | `production/engine.py` | ids only (value of stock) |
| `actions.py` (~1254-1324) | `production/actions.py` | listed-target ids |
| Frontend | `frontend/src/pages/production/StockTargets.tsx` | edit + "target sum" |

Consumers that only need the **ids** (jita price cache closure,
`trade_realized/report.py`, sorting, portfolio "configured" flag) are not
affected by this plan.

Profitability signals that already exist and can be reused (no new network
source needed):

- **Live build margin**: `_build_margin(type_id, cost_memo[type_id], ...)`,
  already computed per stock target inside both planners.
- **Market-wide turnover**: Goonmetrics region history for
  `TRADING_CONFIG.reference_region_id`, averaged as in
  `_scan_build_candidates` (`daily_movement`, `potential_daily_profit =
  daily_movement x margin x build_cost`).
- **Own sales velocity**: `prod_realized_results` (`type_id`, `quantity`,
  `sale_date`, `profit`) - what this tenant actually sold at C-J and earned,
  from the realized-profit ledger. Only present when the ledger has run and
  wallet/orders are shared with Production.

## 3. Core idea

One new pure function computes an **effective target** per row; everything
downstream keeps reading the same 5-tuple shape, so the planners themselves
do not change:

```
load_stock_targets()            -> configured rows (+ new mode/min/max columns)
dynamic_targets.resolve(rows, signals, cfg) -> effective rows (same 5-tuple)
_PlanContext.stock_targets      =  effective rows
```

`resolve()` lives in a new module `eve_trader/production/dynamic_targets.py`
(pure, no I/O, like `pi/engine.py` or `skill_plan_logic.py`), so the formula
is unit-testable without Postgres/ESI. Signal gathering (ledger read,
Goonmetrics history) is a separate thin function next to it.

### 3.1 Per-row mode

Each row gets a mode:

- `fixed` (default, today's behaviour, every existing row starts here) - the
  three numbers are used unchanged.
- `dynamic` - the scaled pool (see decision D1) is computed; the user sets
  a **minimum** and a **maximum** instead of one number. The other pools stay
  fixed.

Keeping `fixed` as the default means the feature is opt-in per item and a
deploy changes nothing until the user switches rows over.

### 3.2 Formula (proposal)

For a dynamic row:

1. **Demand velocity** `v` (units/day):
   - own sales: `sum(quantity) / window_days` from `prod_realized_results`
     over the last `dyn_window_days` (default 30);
   - market: Goonmetrics `daily_movement` x `dyn_market_share` (default
     0.10, i.e. "we can plausibly take 10% of region turnover");
   - `v = max(own, market_share_estimate)` once own history exists for at
     least `dyn_min_history_days`, otherwise the market estimate alone
     (cold start). If neither exists: fall back to the row's minimum.
2. **Base quantity** `q0 = v x dyn_coverage_days` (default 14 days: build
   lead time + buffer), i.e. "hold roughly two weeks of what actually
   sells".
3. **Profitability factor** `f` from the live build margin `m`:
   - `m < min_margin` -> `f = 0` (same as today's gate, nothing built);
   - otherwise `f = clamp((m - min_margin) / (dyn_reference_margin -
     min_margin), dyn_factor_min, dyn_factor_max)` - an item exactly at the
     reference margin (default 30%) gets factor 1.0, better items up to
     `dyn_factor_max` (default 2.0), weaker ones down to `dyn_factor_min`
     (default 0.25).
4. **Raw target** `q = q0 x f`, then `clamp(q, row_min, row_max)` and rounded
   to whole units (or to the product's run quantity, so the target is
   reachable in whole runs).
5. **Smoothing** (decision D4): `q_eff = previous + alpha x (q - previous)`
   (default `alpha = 0.3`) and a dead band - the stored value only changes
   when it moves by more than `dyn_deadband` (default 10%). Prevents a
   single price spike from rebuilding the whole plan and flapping build
   lists day to day.

An alternative to steps 3-4, if the user prefers a **capital budget**
(decision D3): rank dynamic rows by ISK/day per ISK of tied-up capital
(`v x m x build_cost / (q0 x build_cost)`) and hand out a tenant-wide budget
greedily, best ratio first, each row clamped to its min/max. This directly
answers "where it pays more, build more" under a fixed amount of capital,
but is harder to explain row by row. Proposal: ship the per-row factor
first (phase 2), budget mode as phase 5.

### 3.3 Freezing and transparency

The computed value is frozen once per tenant and calendar day in a new
table `stock_target_dynamic_values` (`tenant_id, type_id, day, target_qty,
velocity_own, velocity_market, margin, factor, raw_qty, reason`). Reasons
are a fixed vocabulary (`ok`, `below_min_margin`, `no_history_min_used`,
`clamped_min`, `clamped_max`, `no_price`), same idea as the PI verdict
reasons.

Why freeze:

- the Build plan, the Asset plan, the market status and the Stock Targets
  page all show the *same* number on the same day (no drift between two
  planner runs a minute apart with slightly different prices);
- smoothing needs the previous value;
- the user can see a short history ("why did Ishtar go from 6 to 2?").

Recompute triggers: first planner run of a day, a Settings save of a `dyn_*`
field, changing a row's mode/min/max, or an explicit "Recalculate" button.
These also mark the cached Build/Asset plans `stale`, same mechanism as the
special-orders integration (D9 there).

## 4. Integration points

- **`_PlanContext.__init__`**: after `home`/`jita` prices are loaded, call
  `dynamic_targets.effective_rows(...)`. The margin needs `cost_memo`, which
  the planners fill later; the resolver therefore calls `_unit_cost` for the
  dynamic rows itself (same memo object handed on to the planner, so no
  double work).
- **`market_status`, `market_listing_shortfall_by_type`, `market_restock`**:
  read the frozen effective rows (`storage.load_effective_stock_targets()`,
  falls back to configured rows when no frozen value exists for today),
  never recompute, so these cheap pages do not trigger pricing.
- **`_base_runs`** (overbuild buffer) and the invention rows automatically
  follow, because they already read `ctx.stock_targets`.
- **`min_margin` gate** in both planners stays as is. With `f = 0` below
  `min_margin` the dynamic target is already 0 (or the row minimum - see
  D5), so the gate and the factor agree.
- **Special orders**: unchanged. Integrated orders are netted first (SF-1,
  D3 of `SPECIAL_ORDERS_INTEGRATION_PLAN.md`); the dynamic target only sizes
  the stock-target pass.
- **Realized-profit ledger access** (D11): own-velocity reads go through the
  existing `realized/access.py` scope (`shared_owners_for`), so a revoked
  share also removes the signal - the row then falls back to the market
  estimate, never to hidden data.

## 5. Schema

- `stock_targets`: `ADD COLUMN IF NOT EXISTS target_mode TEXT NOT NULL
  DEFAULT 'fixed' CHECK (target_mode IN ('fixed','dynamic'))`,
  `dyn_min REAL`, `dyn_max REAL` - in the existing `docs/phase1_schema.sql`
  (an addition to an existing file, so no deploy-list change).
- New table `stock_target_dynamic_values`, composite PK `(tenant_id,
  type_id, day)`, RLS policy copied from `stock_targets`. Placed in the
  existing `docs/production_buy_list_schema.sql` to avoid the "new schema
  file not in every apply list" bug class; if it gets its own file, add it
  to `deploy/deploy.sh`, `deploy/README.md`, root `README.md` and
  `.cursor/start.sh` (grep for `production_buy_list_schema`).
- Config fields on `ProductionConfig` (type-checked automatically, ranges
  in `_FIELD_RANGES`): `dyn_window_days`, `dyn_min_history_days`,
  `dyn_market_share`, `dyn_coverage_days`, `dyn_reference_margin`,
  `dyn_factor_min`, `dyn_factor_max`, `dyn_smoothing_alpha`,
  `dyn_deadband`, later `dyn_capital_budget` (0 = off).

## 6. API and UI

- `do_update_stock_target_mode(type_id, mode, dyn_min, dyn_max)` in
  `production/actions.py` (`ActionError` on min > max, negative values,
  unknown mode); router `PUT /api/production/stock-targets/{type_id}/mode`.
- `GET /api/production/stock-targets` gains `target_mode`, `dyn_min`,
  `dyn_max`, `effective_target`, `reason` and the inputs (velocity own /
  market, margin, factor) for a tooltip.
- `do_recalculate_dynamic_targets()` + `POST
  /api/production/stock-targets/recalculate`.
- `GET /api/production/stock-targets/{type_id}/dynamic-history` (bare
  storage read, last 30 days).
- Stock Targets page: a Fixed/Dynamic toggle per row; for dynamic rows the
  scaled target column shows the effective value (read-only, with a
  breakdown tooltip) and two editable Min/Max cells; a "Recalculate" button;
  Settings page gets a "Dynamic targets" section for the `dyn_*` fields.
- CLI: `eve-trader production targets mode|recalculate|show`, same `do_*`.

## 7. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Answer section 8 with the user | decisions recorded here |
| 1 | Signals: own velocity from `prod_realized_results` (via access scope), market velocity from Goonmetrics (reuse `price_history_chunked`), one function returning both per type | unit tests with fixture rows, shared/revoked owner cases |
| 2 | `dynamic_targets.py` pure resolver (formula 3.2, reasons, rounding, clamps, smoothing) | table-driven tests: below min margin, cold start, clamp min/max, dead band, no price |
| 3 | Schema + storage (`load_effective_stock_targets`, freeze/read daily values), `_PlanContext` integration, stale flag on cached plans, market pages read frozen values | full `pytest` green; a fixed-only tenant produces byte-identical plans to before (regression test) |
| 4 | Actions, router, CLI, Stock Targets UI, Settings section | router tests (module monkeypatch), live check against `localhost:8000`, Playwright screenshot of the page |
| 5 (optional) | Capital-budget allocation mode (3.2 alternative), optionally job-slot capacity as a second limit | tests: budget respected, ordering by ISK/day per ISK |

## 8. Open decisions (for the user)

- **D1 - which pool scales?** Proposal: only the home-market target
  (`home_market_stock`, what is listed and sold at C-J). `backup_stock` is a
  reserve and stays fixed; `jita_market_stock` stays fixed. Alternative:
  scale the sum of all three proportionally.
- **D2 - demand source.** Own sales from the ledger, market turnover from
  Goonmetrics, or both (proposal: max of both, market-only for cold start).
  What market share of region turnover is realistic for C-J (proposal 10%)?
- **D3 - per-row factor or capital budget?** Proposal: factor first,
  budget as optional phase 5.
- **D4 - how fast may targets react?** Proposal: daily freeze, smoothing
  0.3, 10% dead band.
- **D5 - below `min_margin`:** target 0 (today's behaviour) or keep the row
  minimum as a floor anyway? Proposal: 0 - the existing rule "not worth
  building -> not worth buying" stays.
- **D6 - default min/max when switching a row to dynamic.** Proposal: min =
  0, max = 3x the current fixed value (or the current value if it was 0:
  user must enter one).
- **D7 - T2 invention.** Higher targets mean more BPCs (`bpc_inventory`
  multiplier). Should the invention stockpile follow the dynamic target
  (proposal: yes, automatically, it already reads the target) or the max?

## 9. Risks

- **Feedback loop**: an item that is not built is not sold, so own velocity
  drops, so the target drops further. Mitigation: velocity = max(own,
  market estimate) and the row minimum.
- **Theoretical-ceiling volumes** (CLAUDE.md): the market turnover is a
  whole-region figure; never use order-book depth (`sell_volume`) as
  velocity (issue #51) - only real traded volume, scaled by the market
  share.
- **Price spikes**: a one-day margin spike could triple a capital-ship
  target. Mitigation: smoothing, dead band, and row max.
- **Plan drift between pages**: avoided by the daily freeze (3.3).
- **Cost**: the resolver needs margins for every dynamic row before the
  plan; `_unit_cost` is memoised and handed to the planner, so no second
  pricing pass.
