# Portfolio overview - plan

Status: **in progress 2026-10-18**. Decisions P1-P7 stand as confirmed.
Phase 1 (cards, period, header, wealth change) is built. Phases 2-5 are
not. Deviations: none yet. Phase 1 tests: `tests/test_realized_cards.py`
(periods, since_t0 window, single-tool total, recompute adds nothing),
`tests/test_portfolio.py` wealth_change gaps and percent, router period
forwarding, vitest `Portfolio.test.tsx` (4). Full pytest 3316 passed;
frontend lint, vitest (377) and build passed. Live page check follows
with the finished page.
Builds on `docs/PORTFOLIO_REWORK_PLAN.md` (Total Wealth, snapshots, manual
prices) and `docs/REALIZED_PROFIT_UNIFIED_PLAN.md` (one ledger, four
channels, `GET /api/portfolio/realized-cards`).

## 1. Goal

The Portfolio page (`frontend/src/pages/Portfolio.tsx`, `eve_trader/
portfolio.py`) grew in layers and no longer reads as one page:

- "Combined Value" adds a 30-day profit flow (Trading only) to a stock
  value at one point in time. Since the unified ledger it also leaves out
  Production, Doctrine and Station Trading profit. The page header still
  says "Trading + Production combined".
- Trading's card reads `portfolio_overview()`; the other three cards each
  call their own tool's summary. Different shape, no coverage on Trading, no
  link, five requests where `realized-cards` already returns all of them.
- Every figure is fixed to the last 30 days.
- History charts are Trading-only snapshot series.
- Nothing shows how much capital is tied up (stock at cost, open orders,
  escrow, outstanding contracts) or what it is worth.
- Total Wealth is three totals with no breakdown, an unpriced-item count
  without the items, and it misses goods that are not in the assets
  endpoint (section 2).

The page should answer, top to bottom: what am I worth and how did that
change, what did each tool earn, where is my capital and what is it worth,
and what needs my attention.

## 2. Facts that shape the design

- `realized_cards(tool_keys)` already returns per-tool profit, coverage,
  estimated and empty for the last 30 days, and a total only when two or
  more tools have a figure. Each tool's `summary(start, end, since_t0,
  days)` already returns `revenue`, `realized_profit`, `margin`,
  `coverage` and `by_day`.
- `portfolio_snapshots` stores one row per day: Trading figures,
  `production_stock_value`, `combined_value`, `total_wealth`,
  `wealth_assets_value`, `wealth_wallet_balance`. Blueprint value is
  derived (total - assets - wallet).
- `total_wealth()` reads `load_all_assets` (type_id, quantity only), owned
  blueprints and wallet balances of owners sharing with **Portfolio**.
  Prices are Goonmetrics home, then Jita sell, manual price first.
- **Gap:** ESI's assets endpoint does not list items sitting in a sell
  order or included in an outstanding contract, and ESI's wallet balance is
  already net of buy-order escrow. Total Wealth therefore undercounts
  everything listed for sale and everything in an outstanding contract, and
  shows escrow nowhere. For a tenant whose stock is mostly on the market at
  C-J this is a large share of the real wealth.
- The realized ledger knows: open cost layers (`prod_cost_layers`,
  `quantity_left`, `unit_cost`, `pool`), archived market orders
  (`ledger_market_orders`: price, `volume_remain`, `is_buy_order`, state)
  and outstanding item exchanges (`outstanding_contract_qty_by_type`). It
  reads owners shared with any realized tool (U3a), not the Portfolio
  share.
- `ledger_market_orders` has no escrow column; ESI returns `escrow` on
  buy orders.

## 3. Changes, by item

### 3.1 Total Wealth leads the page (item 1)

- Remove "Combined Value" from the page and from new snapshots' meaning.
  The column stays in `portfolio_snapshots` for old rows; new rows keep
  writing it so the history chart of old data is not broken, but the page
  no longer shows it (P1).
- New header: "Portfolio Overview" with no tool list.
- Top card: Total Wealth with change vs 1, 7 and 30 days ago, computed in
  the backend from `portfolio_snapshots` (`do_get_portfolio_overview`
  returns `wealth_change: {d1, d7, d30}` as ISK and percent, None where no
  snapshot exists that far back). The frontend shows the numbers only.
- Production stock value moves into section 3.5 (capital), where it
  belongs.

### 3.2 One set of realized-profit cards (item 2)

- `realized_cards` returns per tool: `profit`, `revenue`, `margin`,
  `coverage`, `estimated`, `empty`, `sales_count`, `link` (route), plus
  `total` (P2: also with a single tool).
- The page renders one card component from that list. The four
  per-tool card components and their five queries go away.
- Trading's figures come from the same `trade_realized.report.summary`
  as the Trading page, so Trading gets coverage and a link like the rest.
- `portfolio_overview()` keeps its Trading fields only for the snapshot
  (P1); the page stops reading them.

### 3.3 Period selector (item 3)

- `GET /api/portfolio/realized-cards?period=7|30|90|since_t0` (default
  30). `realized_cards` builds the window the same way the tool pages do
  (`production/realized/period` semantics), so "since T0" means the
  ledger's T0.
- One `SegmentedControl` above the cards, remembered per viewer in
  localStorage (try/catch). It drives sections 3.2 and 3.4.

### 3.4 Realized-profit history for every channel (item 4)

- New `GET /api/portfolio/realized-history?period=...&bucket=day|week`:
  per bucket, profit per channel the session holds, plus a running total.
  Built from each tool's `summary()['by_day']` (no new SQL), bucketed in
  the backend; week buckets when the period is longer than 90 days, like
  the tool pages.
- Chart: stacked bars per channel (fixed channel colours in `theme`), a
  line for the cumulative total, legend, tooltip with all channels.
- The Trading-only charts (average margin, daily volatility, Trading
  profit vs stock value) leave the Portfolio page. Margin and volatility
  move to the Trading realized page as two small figures from the same
  `portfolio_figures()` numbers (P3).
- The Total Wealth history chart stays.

### 3.5 Capital deployed (item 5)

New section "Capital" with one row per bucket, each with cost basis where
the ledger has one and market value:

| Bucket | Quantity source | Cost basis | Market value |
|---|---|---|---|
| Stock (hangars) | open cost layers not listed and not in contracts | layer unit cost | Total Wealth pricing |
| Listed for sale | open sell orders, `volume_remain` | layer unit cost of those types (FIFO) | order price (and Total Wealth pricing for comparison) |
| In outstanding contracts | `outstanding_contract_qty_by_type` | layer unit cost | contract price share, else Total Wealth pricing |
| Buy-order escrow | open buy orders | escrow ISK | escrow ISK |
| In production | running jobs' consumed inputs (`prod_job_records` in progress) | their cost | – |

- Unrealized profit = market value - cost basis, per bucket and in total,
  with the ledger's coverage flag (opening stock at planner value is
  estimated, as on the realized pages).
- Backend: new `eve_trader/portfolio_capital.py` (pure aggregation) plus a
  `do_get_portfolio_capital` action and `GET /api/portfolio/capital`.
  Reads go through `realized.access` (union of realized-tool shares) and
  the same price function as Total Wealth.
- Escrow: archive the `escrow` field ESI already returns on buy orders
  (`ledger_market_orders.escrow`, `ADD COLUMN IF NOT EXISTS`, filled on the
  next order sync). Until filled, escrow is shown as "–", never estimated.
- "In production" uses only what the ledger already holds; when no job is
  running the row is hidden.

### 3.6 Total Wealth breakdown and completeness (item 6)

- **Completeness (P4):** add two components to Total Wealth for owners
  sharing with Portfolio: items in open sell orders (market orders kind)
  and included items of outstanding contracts the owner issued (contracts
  kind), each priced like assets; plus buy-order escrow as ISK. Each is a
  separate line so the total stays explainable. Needs `market_orders` and
  `contracts` to accept `portfolio` as a consuming tool (registry +
  Characters page), same as F4 did for Trading.
- **Breakdown:** `load_all_assets` gains an optional grouped variant
  returning (owner, location_id, type_id, quantity). Total Wealth returns:
  - by owner (character / corporation name),
  - by location (resolved names; C-J and Jita named, the rest grouped as
    "Other" below a threshold),
  - by category (SDE category: ships, modules, charges, materials, ...),
  - top 20 positions by value.
  The page shows them as tabs with a table and a share bar.
- **Unpriced items:** return the list (type, quantity, owner, location)
  instead of a count, each row with an inline manual-price field that
  calls the existing manual-price action.
- Snapshot: add `wealth_listed_value`, `wealth_contract_value`,
  `wealth_escrow` columns (`portfolio_schema.sql`, idempotent) so history
  shows the new components; old rows stay NULL ("–").

### 3.7 Small items (item 7)

- **Attention strip** at the top: one compact list of what needs action,
  each with a link - missing wallet scope, realized data that needs a
  recompute, unpriced items, stale ESI data for Portfolio's kinds
  (`esi_freshness`), stock targets not configured. Replaces the scattered
  alerts and "Not computed yet" texts.
- **Freight figure:** last-period freight split (couriers / configured
  rate) summed over the tools the session holds, from the realized
  summaries' existing `freight_courier` / `freight_configured` fields.
- Replace "Everything above is read-only." with nothing; the manual-price
  section is editable.

## 4. Decisions (confirmed)

**P1 Combined Value.** *Confirmed:* stop showing it; keep writing the
column for old history. Alternative: drop the column (loses history).

**P2 Total with one tool.** *Confirmed:* always show the total when at
least one tool has a figure (it then equals that card). Alternative: keep
"two or more" as today.

**P3 Trading margin/volatility.** *Confirmed:* move to the Trading
realized page. Alternative: keep on Portfolio under the Trading card.

**P4 Wealth completeness.** *Confirmed:* add listed, contract and escrow
components through Portfolio's own sharing (new consuming tool on
`market_orders` and `contracts`). Alternative: read them through the
realized-tool union (no new sharing, but Portfolio would then show data
the user did not share with Portfolio - against PORTFOLIO_REWORK_PLAN
section 4).

**P5 Capital section scope.** *Confirmed:* read the realized-tool union
(the ledger already does, and cost basis only exists there), labeled
"from the realized-profit ledger". Alternative: restrict to owners that
also share with Portfolio (consistent scope, but cost basis gaps).

**P6 Market value price.** *Confirmed:* one price function for wealth
and capital (manual, home sell, Jita sell). Alternative: Jita buy for a
liquidation value - a second number to explain.

**P7 Period default.** *Confirmed:* 30 days. Alternative: since T0.

## 5. Phases

1. **Cards, period, header (3.1-3.3).** Backend: `realized_cards`
   extension with period, `wealth_change`. Frontend: header, Total Wealth
   on top, one card component, period selector. Tests: realized_cards
   periods and single-tool total, wealth_change with gaps, router tests,
   vitest for the cards.
2. **Realized history (3.4).** Endpoint and bucketing, stacked chart,
   moving margin/volatility. Tests for bucketing (day/week, missing days,
   channels the session does not hold).
3. **Wealth completeness and breakdown (3.6).** Registry change (P4),
   storage reads, snapshot columns, breakdown tabs, unpriced list with
   inline price. Tests: no double count between assets and orders/
   contracts, sharing gates, breakdown sums equal the total.
4. **Capital (3.5).** Escrow column, `portfolio_capital.py`, endpoint,
   section. Tests: FIFO cost of listed quantities, contract items,
   escrow missing -> None, unrealized with estimated coverage.
5. **Attention strip, freight, cleanup (3.7)** and docs (CLAUDE.md
   Portfolio paragraph, this plan's status).

Every phase: full `pytest`, frontend lint/test/build, live check against
the test server's real data before production.

## 6. Risks

- Double counting between assets, sell orders and contracts: ESI excludes
  listed/contracted items from assets, but a stale snapshot of one kind
  next to a fresh one can count an item twice or not at all for a sync
  interval. Show each component's sync time.
- Capital cost basis is FIFO across all owners (ledger design), so a
  listed item's cost is the oldest layer of that type, not necessarily the
  unit that is listed.
- Corp data is shared across tenants (D7); corp orders and contracts in
  the capital section follow the ledger's existing corp rules.
- More reads on page load: capital and breakdown are separate queries so
  the top of the page renders first.
