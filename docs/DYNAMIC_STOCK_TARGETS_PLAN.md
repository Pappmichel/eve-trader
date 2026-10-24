# Dynamic stock targets - plan

Status: **all decisions confirmed (sections 2 and 15), real-data checks run on 2026-10-24 (section 14.2), design reviewed
against the code (sections 3-11), implementation pitfalls in section 16,
nothing implemented.** Before phase 1: the real-data
checks in section 14 (R-01..R-05 by SQL) and the impact simulation on the
current stock list (TODO list below); the rest via the preview in phase 3b.

## TODO - real-data checks (later session with server/DB access)

Not done yet: this container had no `config.yaml` and no production
database. Do these first, before phase 1. Details, reasons and SQL are in
section 14. Record each result next to its item and adjust the defaults in
sections 5, 6 and 9 if a result calls for it.

- [x] Read the real `config.yaml` / tenant overrides: `home_location_id`,
      `min_margin`, `market_fees`, `reference_region_id`.
- [x] R-01 Count stock targets with home / Jita / backup-only targets; list
      rows with home = 0 (need a manual max, D6).
- [x] R-02 Last ledger runs (`ran_at`, T0, fingerprint) and covered sales
      period; is there >= 14 days of history?
- [x] R-03 Share of `prod_realized_results` rows with NULL `source` /
      `location_id`.
- [x] R-04 Target-item sales split: C-J vs. elsewhere, market vs. contract.
- [x] R-05 Are corporation wallets shared with Production? If yes, decide
      whether own velocity counts character wallets only.
- [x] R-12 Number of manual Build/Buy overrides on target items (D8).
- [x] R-14 Do Trading / Station Trading sell the same types at C-J?
- [ ] R-16 Stockout days per item at C-J in the window (feedback-loop bias).
- [x] R-17 Items whose stock + listings already exceed the simulated target.
- [ ] R-18 C-J order book per item: our listings vs. other sellers.
- [x] R-19 Live vs. realized margin per item (is the live margin reliable?).
- [x] R-20 Dual-role items: targets that are also materials of other targets.
- [x] R-24 Open integrated special orders on target items.
- [x] R-25 Other tenants with Production stock targets (rollout scope).
- [ ] R-27 Backup taken; schema change applied twice on a copy without error.
- [ ] R-15 Backtest of the formula over the last 60-90 days vs. actual
      sales (stockouts / unsold stock) - same throwaway script as the
      impact simulation, extended with history.
- [ ] R-21 / R-22 Capacity: simulated build list vs. free job slots,
      invention slots/datacores, haul m3 (from the impact simulation).
- [x] R-23 Plan runtime today, plus the Goonmetrics call for N rows from the
      server.
- [ ] R-26 Volatility of the C-J quote and region movement (smoothing /
      dead band defaults).
- [x] **Impact on the current stock list (before any implementation).**
      Simulate the formula of section 6 for every current stock target
      with a home target, using real data, and write nothing. Method: a
      throwaway, read-only script in the session's scratch directory (not
      committed) that reuses the existing code paths - own velocity per
      R-06/R-10 SQL, region movement via `GoonmetricsClient.
      price_history_chunked(reference_region_id, ...)`, live margin via
      `margin_home` with the planner's cost (`_PlanContext` + `_unit_cost`)
      - with the default settings and D6 min/max (min 0, max 3x current).
      Result: one table per item with
      - current home target vs. simulated target (units and %),
      - velocity source (own / market / none) and margin source,
      - factor, reason (`below_min_margin`, `clamped_max`, `held_no_data`, ...),
      - ISK tied up at build cost, current vs. simulated,
      and a summary: how many items go up / down / to 0 / hit the max /
      stay held, the total change in tied-up capital, and the items whose
      build list entry would change most. Store the summary (not raw
      data) in this file. If the result looks wrong (e.g. most items at 0
      or at the max, capital doubles), adjust the defaults or the design
      here before phase 1.
- [ ] After phase 3b (preview): R-06 own sales vs. 10% market share,
      R-07 history coverage in `reference_region_id`, R-08 how often no C-J
      quote / quote is our own order, R-09 margin distribution vs. 15% / 30%,
      R-10 current target vs. proposed, R-11 lead time vs. 14 coverage days,
      R-13 tied-up capital today vs. proposed.
- [ ] Update defaults / design in this file from the results, then start
      phase 4.

## 1. Goal

Today every row on the Stock Targets page carries three fixed numbers
(`stock_targets.backup_stock`, `home_market_stock`, `jita_market_stock`) that
the user maintains by hand. The planners (`plan_production`,
`plan_asset_optimized`) build exactly up to those numbers, and the only
profitability input is the binary `min_margin` gate (below it: build nothing,
above it: build the full target).

Wanted: the **item list stays exactly as it is** (the user still decides
*what* is built), but the **quantities become dynamic**: an item that earns
more gets a higher target, an item that earns less gets a lower one, down
to zero. No items are added or removed automatically.

Non-goals:

- No automatic adding of items (that stays Build Candidates / Margin page).
- No selling, relisting, repricing. A lower target only means "build less /
  stop building"; stock already on hand or listed stays.
- No change to *how* an item is built (buy-vs-build, ME/TE, invention,
  alchemy, slots, special orders). Only one top-level quantity changes.
- No Jita sales channel for Production (CLAUDE.md). `jita_market_stock`
  keeps its current meaning and stays fixed.

## 2. Decisions (confirmed with the user 2026-10-24)

- **D1 - which pool scales:** only the home-market target
  (`home_market_stock`, listed and sold at C-J). `backup_stock` and
  `jita_market_stock` stay fixed on dynamic rows.
- **D2 - demand source:** `v = max(own sales velocity, market estimate)`;
  the market estimate alone while there is no usable own history. Market
  share default 10% of region turnover (tenant-wide setting, no per-row
  value).
- **D3 - method:** per-row profitability factor (section 6). A capital
  budget stays an optional later phase.
- **D4 - reaction speed:** slow - frozen once per day, smoothing 0.3, dead
  band 10%.
- **D5 - below `min_margin`:** effective target 0, the row minimum does not
  apply there.
- **D6 - defaults on switch to dynamic (max amended by D13):** min = 0, max = 3x the current
  fixed home-market value; if that value is 0 the user must enter a max.
- **D7 - T2 invention:** the BPC stockpile follows the effective target.
- **D9 - global switch (confirmed 2026-10-24):** one tenant-wide switch
  turns the feature on and off for all items at once, plus bulk actions
  that set every row to dynamic or to fixed (section 10.1).
- **D12 - minimum of 1 for rows that sell (confirmed 2026-10-24, follows
  the real-data finding 1 in 14.2):** above `min_margin`, a row with at
  least one own sale in the window never rounds below 1 unit (the row min
  still applies if it is higher). Below `min_margin` D5 still gives 0. Rows
  without any own sale in the window may round to 0.
- **D13 - max floor (confirmed 2026-10-24):** the default max on a switch
  to dynamic is `max(3 x current fixed home value, 10)`; the D6 rule that a
  current value of 0 needs a manual max stays. Replaces the plain 3x of D6.
- **D14 - first real run after the next ledger rebuild (confirmed
  2026-10-24):** the ledger covered 13.79 of the required 14 days on
  2026-10-24, so own velocity is unavailable until the next rebuild. The
  preview (phase 3b) and the first switch-on are done after that, so own
  velocity is part of the first real numbers. The design does not change.
- **D15 - overstock hint (confirmed 2026-10-24):** the Stock Targets page
  shows, per dynamic row, when stock plus listed units already exceed the
  effective target (backup included) and by how much (E-15, R-17: 116 rows,
  about 65 B ISK on 2026-10-24). Display only; nothing is sold or delisted.

## 3. Current state - every consumer of the quantities

`storage.load_stock_targets()` returns `(type_id, type_name, backup_stock,
home_market_stock, jita_market_stock)`. Checked in the code (2026-10-24),
every place that reads the **quantities** (not just the ids):

| # | Consumer | File | What it does with the home value | Change |
|---|---|---|---|---|
| C1 | `plan_production` | `production/engine.py` | `_total_missing`, gross demand, invention row target, `stock_used` | effective rows |
| C2 | `plan_asset_optimized` | `production/engine.py` | `_total_missing`, `seed_stock_coverage` | effective rows |
| C3 | `_base_runs` (overbuild buffer) | `production/engine.py` | sizes component buffer from the whole target | effective rows (via C1/C2) |
| C4 | `_invention_need_row` | `production/engine.py` | stockpile % against backup+home+Jita (D7) | effective rows (via C1) |
| C5 | `market_status()` | `production/engine.py` | skips a row with home and Jita both 0/NULL (issue #33) | effective rows **and** keep dynamic rows visible (E-09) |
| C6 | `market_listing_shortfall_by_type()` | `production/engine.py` | listing shortfall; used by Sorting (`sorting/engine.py`) | effective rows |
| C7 | `market_restock()` | `production/engine.py` | only rows with home > 0 | effective rows |
| C8 | unlisted-stock check | `production/actions.py` (~1324) | "meant for sale" = home > 0 or Jita > 0 | dynamic rows always count as meant for sale (E-10) |
| C9 | `GET /stock-targets` + `StockTargets.tsx` | router / frontend | edit cells, target sum | show configured + effective |

Consumers that only use the **ids** stay on the raw read and are not
affected: `jita_price_cache.py` (material closure), `trade_realized/
report.py`, `stock_value()`, `discover_build_candidates`
(`existing_target_ids`), the Portfolio "configured" flag and attention
list, `_PlanContext`'s priced-type universe.

`_PlanContext` is also constructed by Doctrine (`doctrine/engine.py`),
`plan_special_order`, discovery, the ship-margin page, `item_margin_detail`
and `planner_unit_values` (realized-profit valuation). None of these may
trigger the dynamic computation or its DB writes - the resolver is therefore
**not** called from `_PlanContext.__init__` (E-14).

## 4. Data model

### 4.1 `stock_targets` (existing table, `docs/phase1_schema.sql`)

```
ADD COLUMN IF NOT EXISTS target_mode TEXT NOT NULL DEFAULT 'fixed'
    CHECK (target_mode IN ('fixed', 'dynamic'))
ADD COLUMN IF NOT EXISTS dynamic_min REAL
ADD COLUMN IF NOT EXISTS dynamic_max REAL
```

- `home_market_stock` is **not** overwritten for a dynamic row. It keeps
  the last fixed value, so switching back to `fixed` restores exactly what
  the user had (reversible, as the repo prefers).
- `storage.load_stock_targets()` keeps its 5-tuple shape and meaning
  (configured values). Dozens of tests build that tuple directly; changing
  it would break them for no gain. New reads sit next to it (4.3).
- `upsert_stock_target`'s `ON CONFLICT` list does not mention the new
  columns, so existing edits leave them alone. `sqlite_migration` copies by
  column; the new columns have defaults.

### 4.2 `stock_target_dynamic_values` (new table)

Composite PK `(tenant_id, type_id, day)`, RLS copied from `stock_targets`.
Columns: `day DATE` (UTC = EVE time), `effective_qty REAL`, `smoothed_qty
DOUBLE PRECISION`, `raw_qty DOUBLE PRECISION`, `velocity_own`,
`velocity_market`, `velocity_used`, `margin`, `margin_source TEXT`,
`factor`, `reason TEXT`, `computed_at TIMESTAMPTZ`.

Placed in the existing `docs/production_buy_list_schema.sql` (no new file,
so the "schema file missing from one apply list" bug class from CLAUDE.md
cannot happen). Retention: rows older than 90 days are deleted on write.

### 4.3 Storage functions

- `load_stock_target_modes() -> dict[type_id, (mode, min, max)]`.
- `effective_stock_targets(cfg) -> list[5-tuple]` - **not** in `storage.py`
  but in `production/dynamic_targets.py`, built on top of
  `storage.load_stock_targets()` (P-01): with the global switch off (D9) it
  returns `storage.load_stock_targets()` unchanged and reads nothing else;
  otherwise configured rows, but
  for a dynamic row the home value is the **newest frozen `effective_qty`
  on or before today**; with no frozen row at all, the configured
  `home_market_stock` (the pre-switch value). Read-only, no pricing - this is
  what the cheap pages (C5-C8) use.
- `freeze_dynamic_values(day, rows)` - upsert for today's rows (today may be
  recomputed, earlier days never).
- `latest_dynamic_value_before(day) -> dict[type_id, row]` - the smoothing
  base (strictly before today, see E-05).
- `dynamic_value_history(type_id, days)`.

## 5. Signals

### 5.1 Own sales velocity

Source: `prod_realized_results`, read through the realized-profit
machinery, never with a raw query of its own:

- Run state as `production/realized/report._run_state()`: no ledger run, or
  the owner fingerprint changed (D11 / U3a) -> own velocity **unavailable**
  (never read hidden rows).
- Scope as `report._production_scope()` (`channel = 'production'`, owners
  shared with Production) via `realized/db._result_scope_sql`.
- Only `source = 'market'` (sales at the market) and `location_id =
  cfg.home_location_id` (sold at C-J). Contract sales are excluded: special
  orders and Doctrine deliveries are item-exchange contracts and are not
  market demand (E-07).
- Window: `end = min(now, ledger run ran_at)` (the derived rows only cover
  up to the last rebuild; measuring up to *now* would undercount when the
  rebuild is a few days old), `start = end - dynamic_target_window_days`
  (30), never before the ledger's T0 / archive start.
- `covered_days = end - start` after that clamp. If `covered_days <
  dynamic_target_min_history_days` (14) -> unavailable. Otherwise
  `velocity_own = sum(quantity) / covered_days`.
- One grouped query for all dynamic rows (new `db.market_units_sold_by_type
  (start, end, location_id, **scope)`), not one per row.

### 5.2 Market estimate

- `GoonmetricsClient().price_history_chunked(TRADING_CONFIG.
  reference_region_id, type_ids)` - the same region and call
  `_scan_build_candidates` already uses for `daily_movement`. Regions
  Goonmetrics does not track fall back to ESI daily history inside
  `price_history` itself.
- `velocity_market = mean(movement) x dynamic_target_market_share` (0.10).
- Only real traded volume - never order-book depth (`sell_volume`, issue
  #51).
- No history -> unavailable. A network failure is logged and treated as
  unavailable for that run; it never fails the plan.

### 5.3 Combined

```
both unavailable          -> no velocity  (hold, E-04)
only one available        -> that one
both available            -> max(own, market)          (D2)
```

### 5.4 Margin

- Live margin at the home market: `margin_home(type_id, cost, home, cfg)`
  (C-J sell quote net of `market_fees`), because the scaled pool is the
  C-J listing (D1). The planner's existing gate keeps using `_build_margin`
  (Jita margin when the row has a Jita target) - see E-12.
- `cost` = what the planner would actually pay: the build cost from
  `cost_memo`, or the buy price if `_buy_or_build_decision` says Buy, including a
  manual override (D8, section 15).
- No home sell quote (common right after we sold out, since we may be the
  only seller at C-J) -> fall back to the **realized** margin over the same
  window (`sum(profit) / sum(cost)` from 5.1), `margin_source =
  'realized'`. Neither -> no margin (hold, E-04).

## 6. Algorithm (pure, `eve_trader/production/dynamic_targets.py`)

Input per dynamic row: velocity, margin, row min/max, previous frozen row
(or none), configured home value, settings. Output: the new frozen row.

1. **No velocity or no margin** -> `effective = previous effective` (or the
   configured home value on the first day), `smoothed` unchanged, reason
   `held_no_data`. A data gap or outage never collapses or inflates a
   target.
2. **Margin below `min_margin`** -> `effective = 0`, reason
   `below_min_margin` (D5). `smoothed` is **kept** (not reset), so a
   recovered margin resumes at the old level instead of ramping up from 0
   for a week (E-06).
3. Otherwise:
   - `raw = velocity x dynamic_target_coverage_days` (14)
   - `factor = clamp((margin - min_margin) / (reference - min_margin),
     factor_min, factor_max)` with defaults reference 0.30, 0.25..2.0. If
     `reference <= min_margin` (min_margin raised later, E-16) -> factor 1.0.
   - `raw = raw x factor`
   - `smoothed = prev_smoothed + alpha x (raw - prev_smoothed)` (alpha 0.3;
     first day: `prev_smoothed = configured home value`, so switching a row
     on does not jump).
   - `candidate = clamp(round(smoothed), row_min, row_max)`, with D12: when the row has an own sale in the window, `round(smoothed)` is at least 1 before the clamp (reason
     `clamped_min` / `clamped_max` when that applies).
   - **Dead band:** `effective = candidate` only if `|candidate -
     prev_effective| >= max(1, deadband x prev_effective)`; otherwise
     `effective = prev_effective`, reason `within_deadband`. `smoothed` is
     always stored, so many small moves still add up and eventually cross
     the band (E-08: the rounded value alone would never move).
   - Coming back from `below_min_margin` skips the dead band (the 0 is not
     a real "previous level").

Reasons are a fixed vocabulary: `ok`, `held_no_data`, `below_min_margin`,
`clamped_min`, `clamped_max`, `within_deadband`, plus `velocity_source`
(`own` / `market`) and `margin_source` (`live` / `realized`) as columns.

Rounding is to whole units only; the planner already rounds jobs up to whole
runs, so rounding the target to run multiples would only double-round.

## 7. Freeze lifecycle

- **When:** at the start of `plan_production` / `plan_asset_optimized`
  (and the CLI equivalents), via `ensure_dynamic_targets_frozen(cfg, ctx)`:
  if every dynamic row has a frozen value for today, nothing is computed.
  Otherwise the missing rows are computed and written. No scheduler job:
  the value is only needed when a plan is made, and the lazy shape is the
  same as the portfolio snapshot's lazy fallback.
- **Both planners on one day use the same number** (they read the frozen
  row, not a fresh computation), and so do the cheap pages (C5-C8).
- **Concurrency:** one in-process lock per tenant (single lock, same shape
  as `discover_build_candidates`) so two plan requests do not fetch
  Goonmetrics twice; the write is an upsert, so two processes at worst
  compute the same value twice (the inputs are the same day's data).
- **Smoothing base is the newest row strictly before today** (E-05), so
  recomputing today (button, settings change) is idempotent and never
  smooths twice.
- **Global switch off (D9):** `ensure_dynamic_targets_frozen` returns
  without computing or writing anything; planners and pages use the
  configured values. Frozen rows stay as history.
- **Global switch turned on again:** a row whose newest frozen value is
  older than `dynamic_target_window_days` restarts from its configured
  home value (as on the first day), so a long pause does not resume from
  stale numbers; a shorter pause resumes from the last frozen row.
- **Recompute today** (delete today's rows; the next plan run computes them
  again) on: a Production settings save (`min_margin`, fees, structure,
  any `dynamic_target_*` field all move the result - invalidate on every
  save, it is cheap), a mode/min/max change of that row, the "Recalculate"
  button, an SDE apply.
- **Stale plans:** each of those triggers also calls
  `_mark_cached_production_plans_stale()` (special-orders D9 mechanism).
  The plan result carries `dynamic_targets_day`; a cached plan from an
  earlier day shows the existing recompute hint.
- Days without a plan run simply have no row; smoothing advances once per
  computation, not per calendar day (documented, deliberate - an inactive
  tenant does not drift).

## 8. Integration

- `plan_production` / `plan_asset_optimized`: after `ctx = _PlanContext(...)`,
  call `ensure_dynamic_targets_frozen(cfg, ctx)` (needs `ctx.home`, uses and
  fills the same `cost_memo` the planner then reuses, so no second pricing
  pass), then `ctx.stock_targets = storage.load_effective_stock_targets()`.
  The rest of both planners is unchanged.
- The type universe priced by `_PlanContext` already covers every stock
  target's closure, so the resolver needs no extra prices.
- Every integration point checks the global switch first (D9): off means
  the code path is exactly today's.
- C5-C8 switch from `load_stock_targets()` to
  `load_effective_stock_targets()`; C5 and C8 also consult the mode
  (E-09, E-10).
- Special orders: unchanged. Integrated orders are netted first (SF-1); the
  dynamic value only sizes the stock-target pass. Contract deliveries are
  excluded from velocity (E-07).

## 9. Settings and validation

`ProductionConfig` fields (prefix `dynamic_target_`, because
`_FIELD_RANGES` is flat across all tool dataclasses):

| Field | Default | Range |
|---|---|---|
| `dynamic_targets_enabled` | false | bool (D9, global switch) |
| `dynamic_target_window_days` | 30 | 7..90 |
| `dynamic_target_min_history_days` | 14 | 1..90 |
| `dynamic_target_market_share` | 0.10 | 0..1 |
| `dynamic_target_coverage_days` | 14 | 1..120 |
| `dynamic_target_reference_margin` | 0.30 | 0..None |
| `dynamic_target_factor_min` | 0.25 | 0..None |
| `dynamic_target_factor_max` | 2.0 | 0..None |
| `dynamic_target_smoothing` | 0.3 | 0.01..1 |
| `dynamic_target_deadband` | 0.10 | 0..1 |

Cross-field checks (`factor_min <= factor_max`, `min_history_days <=
window_days`, `reference_margin > min_margin`) need the *merged* config,
which `validate_production_overrides` does not see (it gets only the
overrides). New `validate_production_merged(cfg, overrides)` called from
`do_update_settings` before saving; raises `ConfigError` -> `ActionError`.
The runtime guard in section 6 still covers a later `min_margin` change.

Per-row validation in `do_set_stock_target_mode`: mode in
`fixed`/`dynamic`; min, max >= 0; min <= max; max required (> 0) for
dynamic (D6: when the fixed value is 0 there is nothing to derive it from).
`do_update_stock_target` rejects a `home_market_stock` edit on a dynamic
row with an `ActionError` ("switch to fixed or edit min/max") instead of
silently changing a hidden value.

## 10. API, UI, CLI

- `do_set_stock_target_mode(type_id, mode, dynamic_min=None,
  dynamic_max=None)`; defaults per D6 when switching on without values.
  Switching on seeds today's frozen row with the configured home value
  (`reason = 'ok'`, nothing jumps). Router `PUT
  /api/production/stock-targets/{type_id}/mode`.
- `GET /api/production/stock-targets` gains `target_mode`, `dynamic_min`,
  `dynamic_max`, `effective_home_market_stock`, `dynamic_day`, `reason`,
  `velocity_*`, `margin`, `margin_source`, `factor` (bare reads, no logic).
- Global switch and bulk actions: see 10.1.
- `do_recalculate_dynamic_targets()` + `POST /stock-targets/recalculate`
  (runs the freeze now, same function as the planner; `ActionError` when no
  `home_location_id`).
- `GET /stock-targets/{type_id}/dynamic-history?days=30`.
- `StockTargets.tsx`: Fixed/Dynamic toggle per row; for dynamic rows the
  Home Market Target cell shows the effective value read-only with a
  breakdown tooltip (velocity source, margin, factor, reason, day) and two
  editable Min/Max cells; "Recalculate" button; `targetSum` uses the
  effective value. Settings page: a "Dynamic targets" section.
- CLI: `eve-trader production targets mode|recalculate|show` calling the
  same `do_*`, with `--tenant-id` inside `tenant_scope.enter_tenant`.

### 10.1 Global switch and bulk actions (D9)

Two separate controls, so turning the feature off never loses the per-row
setup:

- **Global switch** `ProductionConfig.dynamic_targets_enabled` (default
  `false`), saved via the normal `do_update_settings` path (type-checked,
  per tenant). Shown at the top of the Stock Targets page and in the
  "Dynamic targets" Settings section.
  - **Off:** every row behaves as `fixed`, nothing is computed, no
    Goonmetrics call, no ledger read, plans are exactly today's. Per-row
    mode, min and max are kept. The page shows the mode column greyed out
    with "Dynamic targets are off".
  - **On:** rows marked `dynamic` use the computed value; `fixed` rows stay
    fixed.
  - Toggling deletes today's frozen rows (the next plan computes them
    again) and marks cached plans stale.
- **Bulk actions** "All to dynamic" / "All to fixed":
  `do_set_all_stock_target_modes(mode)`, router `PUT
  /api/production/stock-targets/mode`, CLI `eve-trader production targets
  mode --all dynamic|fixed`.
  - "All to dynamic" applies the D6 defaults to every row that has no
    min/max yet and keeps existing min/max. Rows without a home target
    (home = 0, no max) are skipped and returned in a `skipped` list with
    the reason, never half-applied. Backup-only rows are skipped too (D1:
    nothing to scale).
  - "All to fixed" sets every row to `fixed`; min/max stay stored, so "All
    to dynamic" later restores them.
  - Both run in one transaction and confirm with the affected row count
    in the UI before applying.
- "Switch everything on" for a user = global switch on + "All to dynamic";
  "switch everything off" = global switch off (one click, reversible).

## 11. Edge cases and pitfalls (register)

| Id | Case | Handling |
|---|---|---|
| E-01 | New item, no sales, Goonmetrics has history | market estimate only |
| E-02 | Ledger never built / fingerprint changed | own unavailable, market only; never read hidden rows (D11) |
| E-03 | Ledger rebuilt days ago | window ends at `ran_at`, not now |
| E-04 | No velocity or no margin at all (outage, untracked item) | hold previous value, `held_no_data` |
| E-05 | Recompute same day twice | smoothing base is strictly before today |
| E-06 | Margin dips below `min_margin` for a few days | 0 while below, smoothed level kept, resumes at old level |
| E-07 | Special-order / Doctrine contract deliveries | `source = 'market'` only |
| E-08 | Small items: rounding hides slow drift | smoothed float stored, dead band on the rounded value |
| E-09 | Effective 0 makes `market_status` drop the row (#33 rule) | dynamic rows always listed, with reason |
| E-10 | Effective 0 hides unlisted stock from the unlisted check | dynamic rows always count as "meant for sale" |
| E-11 | We sold out at C-J, no home quote | realized margin fallback |
| E-12 | Row with a Jita target: planner gate uses Jita margin | gate unchanged; can still drop the whole row; dynamic only sizes home pool |
| E-13 | Feedback loop: not built -> not sold -> lower target | `max(own, market)` keeps the market floor; row min |
| E-14 | Doctrine / special order / discovery / valuation build `_PlanContext` | resolver only in the two planners, never in `__init__` |
| E-15 | Stock above a lowered target | nothing is sold or delisted; just no new builds |
| E-16 | `min_margin` raised above `reference_margin` later | factor 1.0, plus save-time validation |
| E-17 | Ledger sales sold outside C-J | `location_id = home_location_id` only |
| E-18 | `home_location_id` not set | dynamic rows hold their value; recalculate raises `ActionError` |
| E-19 | Tenant with only fixed rows | no Goonmetrics call, no ledger read, plans byte-identical to today |
| E-20 | Item removed and re-added | old frozen rows deleted with the target (`delete_stock_target`) |
| E-21 | One-day price spike | smoothing + dead band + row max |
| E-22 | Several tenants, same item | everything per tenant (RLS table, per-tenant config) |
| E-23 | T2 target rises faster than BPCs exist | invention row follows (D7); the build waits for BPCs as today |
| E-24 | Global switch off with dynamic rows configured | everything fixed, no computation, per-row setup kept (D9) |
| E-25 | Global switch on again after a long pause | rows with a frozen value older than the window restart from the configured value |
| E-26 | "All to dynamic" with rows that have no home target | skipped and listed, never half-applied |

## 12. Tests

- `tests/test_dynamic_targets.py` (pure resolver, table-driven): every
  reason, first day, switch on, hold, below min margin and recovery, dead
  band accumulation, clamps, factor guard, rounding.
- Signals: own velocity with fixture rows (market vs. contract, home vs.
  other location, window clamp to T0 and `ran_at`, fingerprint change ->
  unavailable, revoked share); market estimate with a monkeypatched
  Goonmetrics client (history, none, exception).
- Storage: effective read fallback order, smoothing base before today,
  retention, RLS isolation (`test_pg_tenant_isolation.py` pattern).
- Planner regression: a tenant with only fixed rows produces the same
  `plan_production` / `plan_asset_optimized` output as before; a dynamic row
  changes only the home pool; Doctrine / special order / discovery never
  write a frozen row.
- C5/C8: a dynamic row with effective 0 stays in `market_status` and in the
  unlisted check.
- Global switch: off -> planners, pages and Sorting produce exactly
  today's output with dynamic rows configured; no frozen row is written;
  on -> dynamic rows used. Re-enable after a long pause restarts from the
  configured value. Bulk actions: skipped rows, min/max kept, one
  transaction.
- Settings: cross-field validation, invalidation of today's rows and stale
  flag on save.
- Router tests by module monkeypatch (CLAUDE.md), then live checks against
  `localhost:8000` and a Playwright screenshot of the Stock Targets page.

## 13. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Decisions | done 2026-10-24 |
| 1 | Golden baseline of today's plans (P-05); schema (4.1, 4.2), storage functions (4.3), settings fields + validation (9) | storage tests green, schema applied twice without error; P-24, P-26..P-33 checked |
| 2 | Signals (5): ledger query, Goonmetrics wrapper, margin with fallback | signal tests green; P-12..P-17 checked |
| 3 | Pure resolver (6) | resolver tests green |
| 3b | Preview action + CLI (14.1), run on real data, answer R-01..R-27, adjust defaults here | defaults confirmed from real data |
| 4 | Freeze lifecycle (7) + planner integration (8) + C5-C8 (P-01..P-11, P-18..P-25) | full `pytest` green incl. regression test E-19 |
| 5 | Actions, router, CLI, UI, Settings section (10) | router tests, live HTTP check, browser check; P-34..P-40 checked |
| 6 (optional) | Capital budget / job-slot limit | separate decision |

## 14. Checks against real data before phase 1

The design above was checked against the code only. These points depend on
the real stock list, ledger and config and can change defaults or even the
design. All queries are read-only. Run them on the server as the owner role
with the tenant filter, e.g.
`docker exec -it eve-trader-pg psql -U postgres -d eve_trader -v t="'<tenant uuid>'"`,
and check the real `config.yaml` (not `config.example.yaml`) for
`home_location_id`, `min_margin`, `market_fees` and `reference_region_id`.

| Id | Question | Why it matters | Affects |
|---|---|---|---|
| R-01 | How many stock targets, how many with a home target, Jita target, backup only? | which rows can go dynamic at all; rows with home = 0 need a manual max (D6) | D1, D6 |
| R-02 | Does the ledger have recent rows? `ran_at` of the last run, T0, days covered | own velocity needs >= 14 covered days; if the ledger is rarely rebuilt, own velocity is always old | 5.1, min history |
| R-03 | Are `source` and `location_id` filled on `prod_realized_results`? Share of NULL | the filter `source = 'market' AND location_id = home` drops NULL rows; if many are NULL, own velocity reads 0 | 5.1 |
| R-04 | Share of sales at C-J vs. elsewhere, market vs. contract, per target item | confirms that C-J market sales are the real demand signal (E-07, E-17) | 5.1 |
| R-05 | Are sales booked from a **corporation** wallet shared with Production? | CLAUDE.md: a shared corp wallet mixes other members' fills into the ledger, which would inflate own velocity | 5.1 (may need: characters only) |
| R-06 | Own units sold per day vs. region traded volume (Goonmetrics, `reference_region_id`) per item | calibrates the 10% market share; if own sales are often far above 10% the market floor is useless, far below it inflates targets | D2 default |
| R-07 | How many target items have Goonmetrics / ESI history in `reference_region_id` at all? | items without history depend only on own sales | E-01, E-04 |
| R-08 | How often is there no C-J sell quote for a target item, and is the quote usually our own order? | frequency of the realized-margin fallback; our own listing sets the margin | 5.4, E-11 |
| R-09 | Distribution of live home margins of target items vs. 15% / 30% | if most items are above 30%, every factor sits at 2.0 and nothing is differentiated; reference margin must be set from real data | 6, reference margin |
| R-10 | Current home target vs. "velocity x 14 days" per item (dry run) | shows how far targets would move on day one; big jumps mean coverage days or share are off | coverage days, D6 max |
| R-11 | Build lead time per target item (T2 invention + build, capitals) vs. 14 coverage days | coverage must cover at least the lead time, or the target is always short | coverage days (maybe per category) |
| R-12 | Manual Build/Buy overrides on target items | how often D8 applies | D8 |
| R-13 | Capital tied up in home targets today vs. after the dry run | a factor of 2.0 on many items can double tied-up capital; decides whether the budget phase is needed | phase 6 |
| R-14 | Does Trading or Station Trading also sell the same type at C-J? | the ledger books those sales on another channel; Production velocity then undercounts real demand | 5.1 |
| R-15 | Backtest: what would the formula have set over the last 60-90 days (ledger sales + ESI daily history, which covers a year), and would that have run out of stock or left stock unsold compared with what actually sold? | the only check of the formula against real past demand before it changes a plan | all defaults |
| R-16 | Stockout days per item: days without an own sell order at C-J (`ledger_market_orders`, `issued` + duration / `state`) in the window | own velocity is too low for items that were often sold out (feedback loop E-13); decides whether velocity must be divided by listed days instead of window days | 5.1 |
| R-17 | Dead stock: items whose current stock + listed quantity is already above the simulated target | capital that a lower target cannot free (E-15); may need a separate "overstock" hint on the page | UI, expectations |
| R-18 | C-J order book per item: our listed quantity vs. other sellers, price gap | a higher target only pays if the market absorbs it; with strong competition a raised target lowers the price instead | market share, factor max |
| R-19 | Live margin (`margin_home`) vs. realized margin per item over the window | a large gap means the live margin is unreliable as the factor input (fees, freight, our own price setting the quote) | 5.4 |
| R-20 | Dual-role items: stock targets that are also materials of other stock targets | their demand also comes from the other targets' build trees; a lower home target must not starve those (`target_reserved` path) | 8, tests |
| R-21 | Capacity: would the simulated build list fit the free job slots and the asset plan's slot-days target, incl. invention slots and datacores for T2? | more targets than the slots can build only grows the backlog | coverage days, factor max |
| R-22 | Haul volume: buy-list m3 today vs. simulated (freight to C-J) | higher targets mean more freight; freight capacity may be the real limit | factor max, phase 6 |
| R-23 | Runtime: current `plan_production` / `plan_asset_optimized` time and the extra Goonmetrics call for N dynamic rows from the server | the daily freeze runs inside the first plan of the day; must not push the request past its timeout | 7 |
| R-24 | Integrated special orders currently open on target items | they are netted first; check that the simulated target plus open orders still makes sense | 8 |
| R-25 | Other tenants with Production stock targets | rollout scope; default off for all, but the migration runs for every tenant | rollout |
| R-26 | Day-to-day volatility of the C-J sell quote and of region movement for target items | checks whether smoothing 0.3 and the 10% dead band are enough to keep targets calm | D4 defaults |
| R-27 | Backup before the schema change; apply the schema change twice on a copy | the migration touches `stock_targets`, a table every Production page reads | phase 1 |

Queries (owner role, `:t` = tenant id):

```sql
-- R-01
SELECT count(*) AS targets,
       count(*) FILTER (WHERE COALESCE(home_market_stock,0) > 0) AS with_home,
       count(*) FILTER (WHERE COALESCE(jita_market_stock,0) > 0) AS with_jita,
       count(*) FILTER (WHERE COALESCE(home_market_stock,0) = 0
                          AND COALESCE(jita_market_stock,0) = 0) AS backup_only
FROM stock_targets WHERE tenant_id = :t;

-- R-02
SELECT run_id, ran_at, cutover_t0, owner_scope_hash IS NOT NULL AS has_fp
FROM prod_ledger_runs WHERE tenant_id = :t ORDER BY run_id DESC LIMIT 3;
SELECT min(sale_date), max(sale_date), count(*)
FROM prod_realized_results WHERE tenant_id = :t AND channel = 'production';

-- R-03 / R-04 / R-05 (replace <home> with home_location_id)
SELECT source, owner_type, (location_id = <home>) AS at_home,
       count(*) AS sales, sum(quantity) AS units
FROM prod_realized_results r
WHERE tenant_id = :t AND channel = 'production'
  AND sale_date >= now() - interval '30 days'
  AND type_id IN (SELECT type_id FROM stock_targets WHERE tenant_id = :t)
GROUP BY 1, 2, 3 ORDER BY units DESC;

-- R-06 / R-10 own side: units per day vs. current home target
SELECT s.type_id, s.type_name, s.home_market_stock,
       COALESCE(sum(r.quantity), 0) / 30.0 AS own_per_day,
       COALESCE(sum(r.quantity), 0) / 30.0 * 14 AS own_x14,
       COALESCE(sum(r.profit), 0) / NULLIF(sum(r.cost), 0) AS realized_margin
FROM stock_targets s
LEFT JOIN prod_realized_results r
  ON r.tenant_id = s.tenant_id AND r.type_id = s.type_id
 AND r.channel = 'production' AND r.source = 'market' AND r.location_id = <home>
 AND r.sale_date >= now() - interval '30 days'
WHERE s.tenant_id = :t AND COALESCE(s.home_market_stock, 0) > 0
GROUP BY 1, 2, 3 ORDER BY own_per_day DESC;

-- R-12
SELECT count(*) FROM manual_build_buy m
JOIN stock_targets s ON s.tenant_id = m.tenant_id AND s.type_id = m.type_id
WHERE m.tenant_id = :t;
```

R-06 (market side), R-07, R-08, R-09, R-11 and R-13 need live prices and
the planner, which SQL cannot give. They are covered by a **dry run**
(below) instead of hand queries.

### 14.1 Recommendation: dry run before the planner uses anything

Reorder the phases so that the resolver runs **in preview mode** before it
changes any plan:

- Phases 1-3 as planned, plus a `do_preview_dynamic_targets()` action and a
  CLI `eve-trader production targets preview` that compute the proposed
  value for **every** row with a home target (fixed rows included), print
  current vs. proposed with all inputs (own and market velocity, margin and
  its source, factor, reason), and write nothing.
- Run it on the real tenant, answer R-06 to R-13 from its output, adjust
  the defaults (market share, reference margin, coverage days) in this
  file, and only then do phase 4 (planner integration).
- Optional: run the preview daily for one to two weeks and compare
  proposed values with what actually sold, before switching the first rows
  to dynamic.

### 14.2 Results from the real data (2026-10-24, default tenant, server 2)

Read-only: SQL on the production database plus one throwaway script (not
committed) that reused `_PlanContext`, `_unit_cost`, `margin_home` and
`GoonmetricsClient.price_history_chunked`. Items ticked in the TODO list
above are answered here. Still open: R-11, R-15, R-16, R-18, R-21, R-22,
R-26 (need history over time or a capacity model) and R-27 (needs the new
schema; the daily backup exists, the last one 2026-10-24 20:06).

**Config (default tenant, tenant override beats `config.yaml`):**
`home_location_id` 1049588174021, `min_margin` 0.10 (override; the yaml says
0.15), `market_fees` 0.05, `reference_region_id` 10000009 (Insmother).
`reference_margin` 0.30 is above `min_margin`, so the cross-field check holds.

| Id | Result | Consequence |
|---|---|---|
| R-01 | 371 targets: 247 with a home target (216,676 units), 84 of those also with a Jita target, 124 backup-only (42 with backup > 0, 82 all zero). No Jita-only rows. | Only the 247 can go dynamic. "All to dynamic" skips the 124 backup-only rows (E-26). |
| R-02 | Ledger rebuilt 2026-10-24 08:21; `cutover_t0` is 2026-10-10, so sales exist for 15 days and the run covers **13.79 days**. | With `min_history_days` 14 own velocity is **unavailable until the next rebuild** after 2026-10-24 08:xx; today every item would run on the market estimate alone (E-02). The simulation below ignored this gate to show what own velocity would add. |
| R-03 | 4,295 market rows in 30 days, 0 with NULL `source` or `location_id`, all at the home location. | The `source = 'market'` / home filter drops nothing. |
| R-04 | Target items: all sales are market sales at C-J, character wallets. | C-J market sales are the whole demand signal. |
| R-05 | Every Production sale is `owner_type = character`; no corporation wallet involved. | No "characters only" rule needed now. |
| R-12 | 4 manual overrides on target items, all `Buy`. | D8 applies to 4 rows. |
| R-14 | Trading sells 11.7 M units in 30 days but none of the Production target items; Doctrine 40 rows. | No overlap, own velocity is not undercounted by other channels. |
| R-24 | 1 open special order, integrated and netted against stock. | Covered by SF-1; check it in the phase 4 tests. |
| R-25 | 3 other tenants have 1-2 targets each; one has 54.4 M home units. | The migration runs for them, default off. |
| R-20 | 98 of 371 targets are materials of other targets (fuel blocks, decryptors, other components); none of the most-shared ones has a home target. | Lowering a home target does not starve a shared material today; keep the `target_reserved` path in the tests. |

**Impact simulation** (247 rows with a home target, defaults of sections 5-6,
min 0, max 3x current, first-day smoothing 0.3 and the fully converged value;
live C-J quote available for all 247):

- Velocity source: own 84, market 159, none 4 (Providence, Charon, Ion Siege
  Blaster I, Satyr II -> `held_no_data`). History exists for 243 of 247.
- Reasons: ok 144, `clamped_max` 26 (7 of them have a current target <= 10,
  so 3x is only a few units), `below_min_margin` 73, `held_no_data` 4.
- Today's planner gate already blocks the 73 `below_min_margin` rows (they
  carry 62.9 B ISK of target at build cost that is not built today either).
  35 rows have a Jita gate and a home margin on different sides of
  `min_margin` (E-12).
- Converged vs current home target: 54 up, 106 down, 76 to 0, 11 unchanged.
  First day: 46 up, 97 down, 73 to 0, 31 unchanged.
- Tied-up capital at build cost, rows that pass today's gate: **78.2 B now,
  63.3 B after day 1, 51.0 B converged (-35 %)**. Over all 247 rows the
  figures are 141.3 / 74.0 / 54.9 B.
- Target / current ratio on the gate-passing rows: p25 0.20, median 0.60,
  p75 1.48, p90 3.00. Current targets are mostly above 14 days of demand.
- Largest increases: Sacrilege 8 -> 24, Dominix 5 -> 15, Ferox Navy Issue
  10 -> 29, Industrial Jump Portal Generator I 1 -> 3, Retribution 20 -> 60.
  Largest decreases: Ishtar 36 -> 0 (margin 0.06), Vargur 5 -> 1, Avalanche
  2 -> 0, Nighthawk 10 -> 1, Widow 4 -> 2.
- Nova Rage Torpedo (home target 200,000, build cost 1,111 ISK, margin 0.43)
  is not a typo: the market estimate alone is about 14,000 units/day, the
  target would settle near 319,000 (about 0.35 B ISK).
- R-06: own vs market velocity (149 items with sales): own is higher for 84,
  lower for 65, median own/market 1.17. The 10 % share is about right.
- R-07: Goonmetrics/ESI history for 243 of 247 items.
- R-08: all 247 rows had a live C-J quote today; the realized fallback was
  never needed (it can still matter right after a sell-out).
- R-09: live margin p10 0.00, median 0.18, p90 0.78; 74 below 0.10, 35 in
  0.10-0.15, 60 in 0.15-0.30, 78 at or above 0.30. Margins differ enough for
  the factor to matter; with `min_margin` 0.10 the factor is 1.0 at 0.30 and
  reaches 2.0 at 0.50.
- R-19: live vs realized margin (149 items): median gap 0.04, 16 items differ
  by more than 0.20. The live margin is a usable factor input.
- R-17: for 116 rows stock plus listed units already exceed backup plus the
  converged target; about 65 B ISK of that excess cannot be freed by a lower
  target (E-15). The page needs the "overstock" hint.
- R-23: `plan_production` 9.7 s, `plan_asset_optimized` 3.6 s, the history
  fetch for 247 types fits in that range; the daily freeze is safe inside a
  plan request.

**Findings that change the design or the defaults (not yet decided):**

1. Rounding drives slow, expensive items to 0 or 1 although they sell
   (Avalanche 2 -> 0, Vargur 5 -> 1, Widow 4 -> 2, Devoter 8 -> 2). 14
   coverage days is shorter than the lead time of capital hulls (R-11).
   Options: keep a minimum of 1 for rows with any sale in the window, or a
   per-row/per-category coverage. Needs the user's decision before phase 3.
2. The own signal is on the edge of `min_history_days` (13.79 of 14 days).
   It becomes available with the next ledger rebuild; until then every row
   runs on the market estimate. Plan the first real run after that.
3. The `clamped_max` cap of 3x is tight for rows with a current target <= 10
   (7 rows). Either accept it or add a floor of the max (for example
   `max(3 x current, 10)`).
4. Dropping the 73 low-margin rows to 0 does not change what is built today
   (the gate does that already); it changes the listing targets on
   `market_status` and the Buy List only for rows with a Jita gate (E-12).
5. Nothing in the data argues against D1-D9. No default needs to change for
   the factor, share (10 %), smoothing or dead band; coverage days and the
   max/min rule are the open points above.
## 16. Implementation pitfalls

Found by reading the code paths the implementation will touch
(2026-10-24). Each one is a concrete way the change could break something
that works today. Every phase in section 13 must check its pitfalls before
it is called done.

### 16.1 Tests and existing call sites

- **P-01 Monkeypatched `storage.load_stock_targets`.** 48 call sites in 10
  test files (`test_production_engine.py`, `test_special_orders_
  integration.py`, `test_sorting_engine.py`, `test_production_unlisted_
  stock.py`, `test_stock_value.py`, `test_portfolio*.py`, ...) inject stock
  targets by monkeypatching that function. A new SQL read in `storage.py`
  used by the planners would bypass those patches and silently test
  nothing (or hit the real DB). Therefore `effective_stock_targets()` sits
  in `dynamic_targets.py`, calls `storage.load_stock_targets()` itself, and
  with the switch off returns its result unchanged.
- **P-02 Fakes without the new functions.** Some tests replace storage
  functions with fakes. With the switch off no new storage function may be
  called at all (check `cfg.dynamic_targets_enabled` first, before
  `load_stock_target_modes()`), so these tests keep passing untouched.
- **P-03 Tuple shape.** `load_stock_targets()` keeps its 5-tuple. Do not add
  the mode as a sixth element: every `for a, b, c, d, e in ...` unpack in
  engine, actions, router and tests would raise.
- **P-04 Router tests monkeypatch module objects** (CLAUDE.md). New routes
  call `actions.do_*` through the module import, never `from ... import
  do_x`.
- **P-05 Regression baseline first.** Before touching the planners, record
  the current `plan_production` / `plan_asset_optimized` output for a
  fixture set as a golden test; the "fixed-only tenant is byte-identical"
  claim (E-19) is only provable against a baseline taken before the change.

### 16.2 Planner integration

- **P-06 Order inside the planners.** `plan_production` unpacks
  `stock_targets = ctx.stock_targets` right after building the context and
  then calls `_base_runs(...)` with it; `plan_asset_optimized` calls
  `_base_runs(..., ctx.stock_targets, ...)` early too. The effective rows
  must be assigned **before** both, or the overbuild buffer is sized from
  the configured values while the shortfall uses the dynamic ones.
- **P-07 Shared memos.** The resolver needs `cost_memo`, `t2_memo`,
  `alchemy_memo`, `selected_decryptors`, `cost_indices`, `adjusted_prices`
  - the planner creates the memos after the context. Create them first,
  pass the same objects to the resolver and the planner. A fresh memo in
  the resolver doubles pricing time and can give a different margin than
  the gate.
- **P-08 Same margin arguments as the gate.** `_unit_cost` must be called
  with the same arguments the planner uses (`depth 0`, same decryptors,
  same `alchemy_memo`), and the source via `_buy_or_build_decision` with
  `classify_activity`'s `bp`. Otherwise the factor and the gate can
  disagree about the same item.
- **P-09 Not in `_PlanContext.__init__`** (E-14). Nine callers construct it
  (Doctrine, special orders, discovery, ship margins, item margin, unlisted
  stock, `planner_unit_values` for realized valuation, both planners). Only
  the two planners and the explicit recalculate / preview actions call the
  resolver.
- **P-10 Market-intent rules.** `market_status` skips rows with home and
  Jita both falsy (#33), `market_restock` keeps only home > 0, the unlisted
  check uses home > 0 or Jita > 0. All three need the dynamic type ids
  (`dynamic_targets.dynamic_type_ids(cfg)`, empty with the switch off) in
  addition to the effective tuple (E-09, E-10). `0` and `NULL` mean the
  same there; the effective value of a dynamic row is always a number,
  never `NULL`.
- **P-11 Sorting.** `sorting/engine.py` reads
  `market_listing_shortfall_by_type` - covered by switching that function,
  but the Sorting tests monkeypatch `load_stock_targets` too (P-01).

### 16.3 Signals

- **P-12 Ledger internals.** `report._run_state()` /
  `report._production_scope()` are private helpers of the realized report.
  Do not copy their logic (the D11 fingerprint check would drift). Move
  them to a small shared function (e.g. `production/realized/scope.py`)
  used by both the report and the resolver, or call them through one
  public wrapper.
- **P-13 Goonmetrics days without trades.** `_scan_build_candidates`
  averages `movement` over the *returned* points. If a day without trades
  is missing from the answer, that mean overstates turnover for slow items
  (capitals). Divide by the calendar days of the window and count missing
  days as 0. The ESI fallback returns up to a year - cut it to the same
  window.
- **P-14 No DB connection held across network calls.** The Goonmetrics /
  ESI history fetch takes seconds. Do not run it inside
  `storage.batch_session()` or an open `connect()` (pool max 10, CLAUDE.md);
  read ledger rows, close, fetch history, then write.
- **P-15 Threads and tenant context.** `price_history_chunked` uses a
  thread pool; the Goonmetrics path does not touch storage, the ESI
  fallback already wraps with `storage.with_current_tenant`. Any new
  parallel code in the resolver must wrap the same way (CLAUDE.md).
- **P-16 `reference_region_id` belongs to Trading's config**
  (`TRADING_CONFIG`). It is per tenant like everything else, but a tenant
  who never opened Trading Settings has the base value - read it, do not
  assume Insmother. Log the region used in the frozen row's inputs.
- **P-17 Location filter.** Contract sales store `location_id = 0` when the
  location is unknown (`costing.py`); market sales store the transaction
  location. Filter on `source = 'market'` first, then on location.

### 16.4 Freeze, caching, concurrency

- **P-18 UTC day from Python.** Use `datetime.now(timezone.utc).date()` and
  pass it as a parameter. Postgres `CURRENT_DATE` follows the session time
  zone and can be a different day than EVE time.
- **P-19 Per-tenant lock.** A single module-level lock would serialize
  every tenant's first plan of the day. Use a dict of locks keyed by tenant
  id (the per-key shape of `goonmetrics_client._prices_locks`), created
  under a small guard lock.
- **P-20 Invalidation must not miss a cost change.** Today
  `invalidate_discover_cache()` is called from about 15 places in
  `production/actions.py` (settings, systems, decryptors, manual build/buy,
  stock targets, cost-index overrides, ...) and from `refining/actions.py`
  - every place where build cost or margin can change. Hook the
  invalidation of today's frozen rows into one helper called from the same
  places (or from `invalidate_discover_cache` itself) instead of adding it
  by hand to a subset. Add a test that greps or asserts the call next to
  every `invalidate_discover_cache()` call.
- **P-21 All-tenant invalidation.** `invalidate_discover_cache
  (all_tenants=True)` (SDE apply) has no ambient tenant, so it cannot
  delete RLS rows. Keep a process-wide "invalidated at" timestamp; a
  frozen row with `computed_at` before it counts as missing for today.
  After a restart this is lost - acceptable, the next day recomputes, and
  the Recalculate button exists.
- **P-22 Invalidation racing a running freeze.** A settings save while the
  first plan of the day is computing could be overwritten by the in-flight
  result built from the old settings. Invalidation takes the same
  per-tenant lock (waits seconds at most) before deleting today's rows.
- **P-23 Write once per freeze.** Write all rows of one freeze in one
  transaction (`batch_session`), after all network calls; a partial write
  would leave some items on yesterday's value and some on today's.
- **P-24 `delete_stock_target`** must also delete the item's frozen rows,
  in the same transaction (E-20). `upsert_stock_target` must not touch the
  new columns (its `ON CONFLICT ... SET` list stays as is), so re-adding an
  existing item keeps its mode.
- **P-25 Plan caches.** `_last_plan` / `_last_asset_plan` are per tenant in
  the router. Toggling the switch, a mode change, bulk actions and a
  recalculation all call `_mark_cached_production_plans_stale()`; the
  frontend's `StalePlanAlert.tsx` already shows the hint.

### 16.5 Schema and deploy

- **P-26 GRANT.** The new table needs `GRANT SELECT, INSERT, UPDATE, DELETE
  ... TO eve_trader_app` like `production_buy_list` - without it the app
  role gets "permission denied" at runtime, not at deploy.
- **P-27 Idempotent DDL.** `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT
  EXISTS` (the inline `CHECK` is skipped together with the column on a
  re-run), `DROP POLICY IF EXISTS` before `CREATE POLICY` - the deploy
  re-runs every file each time (`deploy/deploy.sh`, `ON_ERROR_STOP`).
- **P-28 Deploy order.** `deploy.sh` applies the schema before restarting
  the app; keep it that way. If code ever reaches a database without the
  new columns, `load_stock_target_modes()` raises `UndefinedColumn` - with
  the switch off it is never called (P-02), so a missed migration only
  breaks tenants who turned the feature on.
- **P-29 Both schema files exist in every apply list already**
  (`phase1_schema.sql`, `production_buy_list_schema.sql`); verify with a
  grep that no new file was created by accident.
- **P-30 Precision.** `stock_targets` uses `REAL` (float4); keep
  `effective_qty` `REAL` for consistency, but store `smoothed_qty` and the
  inputs as `DOUBLE PRECISION` so the dead band arithmetic (E-08) is not
  disturbed by float4 rounding.

### 16.6 Config and Settings

- **P-31 Settings page is hand-written.** `ProductionSettings.tsx` lists
  every field explicitly (e.g. `min_margin` shown as % with `x 100`). The
  new fields must be added there, fractions shown as %, or they are
  invisible. `config.example.yaml` documents them; the real `config.yaml`
  is not under version control and is never overwritten.
- **P-32 Ranges.** Every numeric field gets a `_FIELD_RANGES` entry
  (flat across all tools - the `dynamic_target_` prefix avoids
  collisions). The bool switch is type-checked automatically.
- **P-33 Merged validation.** Cross-field checks need the merged config
  (current + overrides) - `validate_production_overrides` only sees the
  overrides (section 9).

### 16.7 API and frontend

- **P-34 Pydantic drops unknown fields.** `GET /stock-targets` uses
  `response_model=list[schemas.StockTarget]`; new fields not added to that
  model never reach the frontend. Same for the plan's `InventoryRow` if it
  carries the effective value.
- **P-35 The read becomes logic.** `GET /stock-targets` is a bare storage
  read today. Merging configured, effective and frozen inputs is a
  decision, so it moves into a `do_list_stock_targets()` action
  (CLAUDE.md: a read that needs logic goes through `do_*`).
- **P-36 Query invalidation.** After a mode change, bulk action, switch
  toggle or recalculation, the frontend invalidates the stock targets,
  market status and plan queries (react-query keys), or the page shows old
  values until reload.
- **P-37 Edit cell on dynamic rows.** The Home Market Target cell becomes
  read-only for dynamic rows; the backend still rejects the edit (section
  9) in case of a stale page.
- **P-38 UI test.** `StockTargets.ui.test.tsx` exists and must cover the
  toggle, the read-only cell and the switch-off state.

### 16.8 Repo rules

- **P-39** All code, comments, docs and commit messages in English; no
  Claude attribution lines (CLAUDE.md).
- **P-40** Live verification before "done": real `localhost:8000` calls
  and a Playwright check of the Stock Targets page, scripts deleted after
  (CLAUDE.md). The full `pytest` suite stays green after every phase.

## 15. Decision D8 (confirmed with the user 2026-10-24)

- **D8 - manual Build/Buy override on a dynamic row:** D5 applies anyway -
  below `min_margin` the effective target is 0. The override only decides
  build vs. buy, not the quantity. The margin uses the cost of the
  overridden source (buy price for a Buy override, build cost for Build).
