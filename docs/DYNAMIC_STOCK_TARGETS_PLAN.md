# Dynamic stock targets - plan

Status: **all decisions confirmed (sections 2 and 14), design reviewed
against the code (sections 3-11), nothing implemented.** Next step: phase 1.

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
- **D6 - defaults on switch to dynamic:** min = 0, max = 3x the current
  fixed home-market value; if that value is 0 the user must enter a max.
- **D7 - T2 invention:** the BPC stockpile follows the effective target.

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
- `load_effective_stock_targets() -> list[5-tuple]`: configured rows, but
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
  manual override (D8, section 14).
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
   - `candidate = clamp(round(smoothed), row_min, row_max)` (reason
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
- Settings: cross-field validation, invalidation of today's rows and stale
  flag on save.
- Router tests by module monkeypatch (CLAUDE.md), then live checks against
  `localhost:8000` and a Playwright screenshot of the Stock Targets page.

## 13. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Decisions | done 2026-10-24 |
| 1 | Schema (4.1, 4.2), storage functions (4.3), settings fields + validation (9) | storage tests green, schema applied twice without error |
| 2 | Signals (5): ledger query, Goonmetrics wrapper, margin with fallback | signal tests green |
| 3 | Pure resolver (6) | resolver tests green |
| 4 | Freeze lifecycle (7) + planner integration (8) + C5-C8 | full `pytest` green incl. regression test E-19 |
| 5 | Actions, router, CLI, UI, Settings section (10) | router tests, live HTTP check, browser check |
| 6 (optional) | Capital budget / job-slot limit | separate decision |

## 14. Decision D8 (confirmed with the user 2026-10-24)

- **D8 - manual Build/Buy override on a dynamic row:** D5 applies anyway -
  below `min_margin` the effective target is 0. The override only decides
  build vs. buy, not the quantity. The margin uses the cost of the
  overridden source (buy price for a Buy override, build cost for Build).
