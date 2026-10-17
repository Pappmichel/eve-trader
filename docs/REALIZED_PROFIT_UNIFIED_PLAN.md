# Realized profit for Production, Trading, Doctrine and Station Trading - unified plan

Status: **Phase 5 done (2026-10-17).** All phases of this plan have landed.
Decisions U1-U11 and ST1-ST3 stay as confirmed 2026-10-18. This environment
has no test server, so the section 2 counts were not re-measured; the
reviewer measures them there. Each later phase ends with a review and a
test-server check like the Production realized-profit work
(`docs/PRODUCTION_REALIZED_PROFIT_PLAN.md`, referred to as "the Production
plan" below).

## Status

### Phase 0 - confirm against the code (no code change)

Checked 2026-10-17. The plan's description of today's code matches the
tree. Nothing here re-decides U1-U11 or ST1-ST3.

- **Channel.** `costing._on_sell` books a `RealizedResult` only when the
  type is in `production_type_ids`. Every other market sale only consumes
  layers, and its broker-fee share lands in `fees_on_other_trading`.
  Contract sales are per type (`_on_contract_give` / `_book_contract_sale`,
  `sale_ref` `contract:<id>` or `contract:<id>:<type>`), and only
  Production types become results. There is no `channel`, no doctrine
  flag, and no station pool.
- **Sharing.** `access.shared_owners` and `owner_scope_hash` ask
  `is_shared(..., "production")` only. `fetchers._archive_for_production`
  skips a wallet shared only with Trading and contracts shared only with
  Doctrine. `tenant_eligibility.production_archive_retention` requires the
  `production` grant; the scheduler passes `archive_tools = {"production"}`.
- **Wallet consumers.** `registry.py` `wallet.consuming_tools` is
  `("trading", "production")`. `station_trading` is not a wallet consumer
  yet (ST2). `market_orders` already lists `station_trading`.
- **Freight.** `_TxnEnricher.freight` returns nothing unless the buy's
  region is The Forge (`db.THE_FORGE_REGION_ID`), and also nothing when
  the location is the home structure. A home-region buy outside The Forge
  (the Insmother / C-J buys in section 2) therefore already has no freight.
  A buy in another hub region (Domain, and any Forge station that is not
  the home structure) is the gap U4 closes: only The Forge is priced, at
  Production's `haul_cost_per_m3` via `_freight_rate_for_forge`, not from
  the shared hub table. `reapply_freight_rates` writes that one rate onto
  every row that already has `freight_m3`.
- **Fees.** `_attribute_broker_fees` already builds one pool per
  `(owner, UTC day)` from `brokers_fee` and `market_provider_tax` and
  spreads it over that day's order-based fills of every type. The pool is
  paid once. Non-production sells do not become results, so their share is
  parked on `fees_on_other_trading` instead of a Trading result. Sales tax
  uses `_tax_entry_after` (first `transaction_tax` after the market
  transaction in the same wallet and timestamp, before the next market
  transaction), so a journal id gap is already handled.
  `contract_brokers_fee` is named in the Production plan's Phase 0 notes
  and is not applied.
- **Opening and valuation.** `COSTING_VERSION` in `loader.py` is **3**
  (CLAUDE.md still says 2). A run whose `costing_version` is NULL or lower
  resets `prod_opening_frozen` once. Opening and unknown stock use
  `engine.planner_unit_values`, frozen per `(type, day)` in
  `ledger_valuation_prices`, with The Forge daily average only as
  fallback. `planned.MAX_JOB_AGE` is 24 hours. Tenant characters are the
  registry entry plus every token character (`loader._tenant_character_ids`).
- **Doctrine and Station Trading.** `match_and_validate_contract` and
  `doctrine_contract_history` exist and carry no cost. A doctrine contract
  is not marked on `ledger_contracts`. `StationTradingConfig.station_id`
  defaults to 60003760 (Jita 4-4). Station Trading reads orders and skills
  only.
- **`realized_trades`.** `do_reconcile_trades` still replaces the table.
  Portfolio's `trading_realized_profit` and daily volatility read it. The
  shortlist does not (issue #100). U10's "one release later" has not
  started: this change is the release that makes the ledger the reader.
  Phase 5 keeps the table and leaves a TODO.
- **Internal transfers and corp contracts.** `_classify_contract` already
  skips a contract whose issuer and acceptor are both tenant characters
  or the tenant's own corp, and a corp-only side is not booked (D7). A
  corp-issued contract counts when `issuer_id` is a tenant character.
  U8 attributes that contract to the issuing character; the code already
  books it through that character, and the corp wallet is not a second
  sale.
- **Names.** `production/realized/names.py` resolves type, owner and
  location ids for the report. The API already returns names alongside ids.

Test-server measurements still owed to the reviewer (section 2, not
re-run here): doctrine contracts per month and their item types; a
read-only comparison of `realized_trades` with the new Trading rules for
the same window (Phase 3's `eve-trader realized compare-trading`); buy
locations per region and which of them receive freight under U4.

### Phase 1 - shared archive scope - done 2026-10-17

- Moved the generic ledger (`access`, `costing`, `db`, `ingest`, `inputs`,
  `loader`, `names`) to `eve_trader/realized/` with no behaviour change
  (3262 passed). Production keeps `report.py` and `planned.py`.
- `shared_owners` is the union over `KIND_TOOLS`. `shared_owners_for`
  filters one tool. `owner_scope_hash` covers the union. The fetcher
  archives when `owner_in_scope` is true. `station_trading` consumes
  `wallet`. Inactive-tenant retention keeps the archive for
  `production`, `trading`, `doctrine` or `station_trading`, and the
  scheduler syncs only the realized tools that tenant still holds.
- Characters page mirror (`frontend/src/esiRegistry.ts`) now lists
  `production` and `station_trading` on Wallet, and `production` on
  Contracts. The backend already had Production on both; the hand-kept
  mirror did not, so those switches were not on the page.
- Tests: union scope (Trading-only wallet, Doctrine-only contracts,
  Station Trading keeps the wallet visible until the last share is
  revoked, fingerprint changes), retention, scheduler. Full suite
  3264 passed, 4 skipped. Frontend lint, vitest and build passed.
- Live check on the local database: a character shared only with
  Trading for wallet was in `shared_owners`, absent from
  `shared_owners_for(..., "production")`, visible to the archive read,
  and `archive_status` marked wallet shared. The seed was deleted.
  `GET /api/production/realized/archive-status` on the running server
  returned 401 because the access gate is on; the read above is the
  same function that route calls.

### Phase 2 - channels in the costing - done 2026-10-17

- Channel on every external item sale. Doctrine item-exchange is one
  result (`quantity` 1, `sale_ref` `contract:<id>`, `fitting_id` from
  `doctrine_contract_history` when present, warning `unmatched_doctrine`
  when it is not). A market sale at `StationTradingConfig.station_id` by
  an owner whose wallet is shared with Station Trading is
  `station_trading`. Other contract sales and market sales of Production
  types stay `production`. Other market sales, and contract sales of
  non-Production item types, are `trading`. A lone Production type on a
  mixed contract keeps `sale_ref` `contract:<id>`; the trading lines use
  `contract:<id>:<type>`.
- Station pool (`CostLayer.pool`): a buy at the station enters that pool.
  A sale at the station draws the station pool, then global. Every other
  consumption draws global, then the station pool. Production types still
  use the D6 order inside a pool. `COSTING_VERSION` is 4, so the next
  rebuild resets `prod_opening_frozen` once.
- Freight freezes from the shared hub table (`hubs.hub_freight_per_m3`,
  fallback `haul_cost_per_m3`) for every hub region, not only The Forge
  and not only in All-hubs mode. Home location, home region, and the
  exact station-trading station freeze no freight. Reapply writes one
  rate per region and clears freight on those three. It does not invent
  freight for a row archived with `freight_m3` NULL, and it does not
  rewrite contract-item freight.
- Market broker fees stay one pool per owner and UTC day, spread over
  that day's order-based fills of every channel, and the sell share sits
  on the result. `contract_brokers_fee` is a second pool per wallet owner
  and day (personal contracts key on the character, corp-issued on the
  corporation), spread by contract price. `fees_on_other_trading` stays
  on the result shape and is 0 for new runs. Sales tax is unchanged,
  including the journal-id gap; contract sales still have no sales tax.
- The Production report filters `channel = 'production'` and owners
  shared with Production (a NULL owner stays visible until the rebuild).
  Inventory layers are not filtered by channel.
- Tests: the pure costing cases named in the task (each channel rule,
  doctrine origin split, internal transfer, corp-issued contract, station
  pool order, hub freight vs home buy, one broker-fee pool across
  channels, contract broker fee). Production regression: the same
  Production results except the doctrine contract moving off the
  Production channel. Full suite 3278 passed, 1 skipped. Frontend lint,
  vitest and build passed (the only UI change is the freight dialog
  copy).
- Live check on a throwaway tenant (deleted afterwards): a Production-type
  sale at the home structure booked `production` (revenue 40, cost 10);
  a non-production sale at Jita 4-4 by a Station Trading wallet owner
  booked `station_trading` (revenue 9, cost 5); a Forge buy away from
  that station froze freight 1600. `do_get_realized_summary(days=30)`
  reported revenue 40, profit 30, units 1. `GET /api/production/realized/summary`
  on the running server returned 401 because the access gate is on.

### Deviations

- Blueprint contract lines stay off the result list. A BPC sale would mix
  run units into the Trading report, so those lines are consumed and not
  booked. BPO hand-overs stay capital, as before.
- U4's hub rate is used even when the tool is not in All-hubs mode.
  `_freight_rate_for_forge` used to read the hub table only in that mode.
  On a tenant whose hub table sets The Forge below `haul_cost_per_m3`,
  the next ingest and a freight reapply move Forge purchases to the
  table rate.
- Reapply clears station and home freight that was already frozen, and
  updates rows that already have `freight_m3`. It does not backfill a
  historical non-Forge buy archived with `freight_m3` NULL, and it does
  not rewrite contract-item freight. New ingests use U4.
- `test_contract_sale_has_no_tax_and_follows_produced_fifo` now expects a
  Trading result for the non-Production line (`sale_ref`
  `contract:9:999`). That is U1 rule 5. The Production line on the same
  contract is unchanged.

### Phase 3 - Trading report and page - done 2026-10-17

- `eve_trader/trade_realized/` reads the ledger and keeps channel
  `trading`. Summary, per item (units, average buy landed, average net
  sell, profit, margin), per sale with the purchase lots, inventory of
  unsold Trading goods in the global pool, and warnings. Routes under
  `/api/trading/realized/*`. `POST /realized/rebuild` starts the same
  background job as Production.
- The Realized Trades page (`/trading/trades`) shows those API numbers.
  The side-menu button and the spotlight command say Recompute and start
  that job. The trading pipeline's last step calls the same rebuild
  in-process (`rebuild_realized`) instead of `do_reconcile_trades`.
- Portfolio's trading profit, margin, trade count and daily volatility
  come from the last 30 days of the Trading channel. The three trading
  stats and the trading history charts render only when the session's
  gate status includes `trading`.
- `eve-trader realized compare-trading [--days N]` prints the latest
  `realized_trades` run next to the Trading channel for the same window,
  per item and overall. It does not rebuild and does not write.
  `realized_trades` and `do_reconcile_trades` stay (U10). The page no
  longer reads them.
- Tests: pure comparison and volatility; Postgres report (trading-only
  wallet sale, station-trading sale and a doctrine-only contract sale
  stay off the Trading page, revoking the share hides the run, compare
  does not write); router tests for every new route. Full suite
  3286 passed, 1 skipped. Frontend lint, vitest and build passed.
- Live check on a throwaway tenant (deleted afterwards): a non-production
  sale booked trading, revenue 40, cost 10, profit 30, margin 0.75.
  `compare-trading` printed old profit 12 beside new profit 30.
  `GET /api/trading/realized/summary` on the running server returned 401
  because the access gate is on. The page was exercised in vitest
  (empty / needs-rebuild, item table, period, sale and lot drill-down,
  Portfolio gating). A browser session was not opened: the gate has no
  local login that does not touch `config.yaml`.

### Deviations (Phase 3)

- A contract sale on the trading channel is shown when its archive owner
  shares **wallet** with Trading. Trading has no contracts share (U3a
  names wallet for market sales and contracts-with-Doctrine for doctrine).
  A doctrine-only contract owner therefore stays off the Trading page
  until that owner also shares wallet with Trading.
- Portfolio's average margin is profit divided by cost of goods for the
  last 30 days (freight and fees included), not profit divided by the
  old reconcile run's buy cost.
- Trading inventory is the global pool only. Leftovers in the station
  pool wait for the Station Trading report (Phase 4b).
- The pipeline result key is `rebuild_realized`. `POST /api/trading/trades/reconcile`
  still exists and still writes `realized_trades`; nothing in the UI or
  the pipeline calls it. Phase 5 removes that use.

### Phase 4 - Doctrine report and page - done 2026-10-17

- `eve_trader/doctrine/realized.py` reads the doctrine channel. Summary
  (contracts, revenue, cost, profit, margin, coverage, production saving,
  cost by origin), per fitting, per contract with item lines and the
  production chain, and finished doctrine history rows that are not in
  the archive (`before_archive`, profit null). Routes under
  `/api/doctrine/realized/*`. Contract detail takes `sale_ref` as a query
  parameter because the ref contains a colon. Recompute starts the same
  background job as Production.
- A doctrine result with no `doctrine_contract_history` fitting is
  resolved once per rebuild by `match_and_validate_contract`. History
  wins and the matcher is not called. An unmatched contract keeps
  `fitting_id` null and the warning `unmatched_doctrine`.
- The Doctrine page (`/doctrine/realized`) shows those API numbers, the
  origin split, the fitting and contract tables, the before-archive list,
  and the contract drill-down (lines and `CostChainTree` for produced
  lines). The side-menu tab says Realized.
- Production's overview adds "Produced goods sold through Doctrine"
  (`sold_through_doctrine`: contracts, units, cost of produced-origin
  draws). That cost is not added to Production profit.
- Portfolio shows a Doctrine card only when the session holds `doctrine`.
  `GET /api/portfolio/realized-cards` returns last-30-day profit per
  granted tool among production, trading and doctrine, and a total only
  when at least two of those tools have a figure. The page shows that
  total; it does not add the cards itself. Station Trading is not in the
  sum yet (Phase 4b).
- Tests: Postgres report (origin split sums to cost, doctrine-only share,
  trading and station-trading sales stay off the Doctrine page, a
  production-only contract at the doctrine structure stays off it,
  before-archive row, revoking the doctrine share sets `sharing_changed`,
  matcher fills a missing fitting); router tests for every new route;
  `realized_cards` unit test. Full suite 3292 passed, 1 skipped.
  Frontend lint, vitest (371) and build passed.
- Live check on a throwaway tenant (deleted afterwards, id
  `a0696dad-6ae1-446d-ace0-303069eaec93`): one finished doctrine contract
  booked revenue 100, cost 35 (produced 15 + market 20), profit 65,
  production saving 65, fitting "Stabber Fleet". Production revenue 0 and
  sold-through cost 15. The cross-tool total stayed null because only
  Doctrine had a figure. `GET /api/doctrine/realized/summary` on the
  running server returned 401 because the access gate is on. The page
  was exercised in vitest (needs-rebuild, summary and tables, period,
  drill-down, Portfolio gating). A browser session was not opened: the
  gate has no local login that does not touch `config.yaml`.

### Deviations (Phase 4)

- Contract detail loads the scoped results for a wide date window and
  filters to the requested `sale_ref` in Python. The test server has on
  the order of 50 doctrine contracts, so a dedicated SQL lookup was not
  required.
- The production-chain walk stays in `production.realized.report`
  (`_children`, `_walk`). The doctrine report imports those two helpers.
  Production does not import the doctrine report.
- Production saving is null unless every produced line has a frozen
  `buy_home` or `buy_hub` price on or before the sale date. A partial
  saving is not shown.
- The cross-tool total does not include Station Trading until Phase 4b.

### Phase 4b - Station Trading report and page - done 2026-10-17

- `eve_trader/station_trading/realized.py` reads the station_trading
  channel for owners whose wallet is shared with Station Trading.
  Summary (revenue, cost, profit, margin, average buy, average sell net,
  spread, turnover days, fees per day, open-position cost), per item,
  per sale with the buy lots, and the station-pool leftover at cost.
  Routes under `/api/station-trading/realized/*`. Recompute starts the
  same background job as Production.
- Turnover days are the quantity-weighted time from a draw's layer date
  to the sale. A draw with no layer date stays out of that average.
- The Station Trading page (`/station-trading/realized`) shows those API
  numbers, the fee table, the item table, the sale and lot drill-down,
  and the open position. The side-menu tab says Realized.
- Portfolio shows a Station Trading card only when the session holds
  `station_trading`. `GET /api/portfolio/realized-cards` now includes
  that tool in the per-tool rows and in the total.
- Tests: Postgres report (station sale of a stock-target hull stays on
  this channel, a trading-only Jita sale stays off it, a home sale by a
  station-trading owner stays off it, leftover station quantity is the
  open position, turnover is three days, revoking the share sets
  `sharing_changed`); router tests for every new route; realized-cards
  sum includes station trading. Full suite 3296 passed, 1 skipped.
  Frontend lint, vitest and build passed.
- Live check on a throwaway tenant (deleted afterwards, id
  `c4530b59-095a-46d5-bbf2-91d3d724f80d`): one station sale booked
  revenue 12, cost 5, profit 7, spread 7, turnover 3 days, open position
  5. The cross-tool total stayed null because only Station Trading had
  a figure. `GET /api/station-trading/realized/summary` on the running
  server returned 401 because the access gate is on. The page was
  exercised in vitest (needs-rebuild, summary, fees, open position,
  period, drill-down, Portfolio gating). A browser session was not
  opened: the gate has no local login that does not touch `config.yaml`.

### Deviations (Phase 4b)

- Fees per day are the broker fee and sales tax booked on
  station-trading sales. The unmatched fee pool is shared across
  channels, so this report does not take it.
- Average buy is cost of goods per unit. A broker fee that landed on
  the sale is inside that average, and the spread is average sell net
  minus that average.
- A sale away from the station by an owner who shares wallet only with
  Station Trading is channel `trading`. It stays off this page, and it
  stays off the Trading page until that owner also shares wallet with
  Trading (same U3a shape as Phase 3).
- This page matches contract-channel rows with the wallet share.
  Station Trading has no contracts share of its own.

### Phase 5 - stop calling the old matcher - done 2026-10-17

- The app no longer calls `do_reconcile_trades`. `POST /api/trading/trades/reconcile`,
  `GET /api/trading/trades/realized` and `eve-trader reconcile-trades` are
  gone. The trading pipeline, the pages and Portfolio already read the
  ledger.
- `trade_reconciliation.py` stays in the tree. Nothing in the running app
  imports it except `fetch_recent_transactions`, which the Transactions
  page still uses. `eve-trader realized compare-trading` is the one
  remaining read of `realized_trades`.
- CLAUDE.md (realized section, price-sources matrix, fetcher import,
  costing version 4, Trading Profit / Day), the root README and
  `deploy/README.md` describe the ledger instead of Reconcile Trades.
- Full suite after the removal: 3293 passed, 1 skipped. Frontend lint,
  vitest and build passed. No new seeded live check: this phase deletes
  call sites and does not change a figure. The earlier throwaway-tenant
  checks still cover the four reports.
- With the access gate off, a TestClient gets 404 for
  `GET /api/trading/trades/realized` and `POST /api/trading/trades/reconcile`.
  `GET /api/trading/realized/summary?days=30` still returns 200. The running
  server answers 401 on all three because the gate runs before routing, so
  that 401 is not evidence the old routes are gone.

### TODO (U10, not this release)

Drop `realized_trades`, `storage.save_realized_trades`,
`trade_reconciliation.py` (keep `fetch_recent_transactions` if the
Transactions page still needs it, or move that helper first) and
`eve-trader realized compare-trading` on the release after this one.
The comparison window is not over. Do not drop the table in this change.

## 1. Goal

One realized-profit ledger for the whole tenant, read by four tools:

- **Production** - goods it produced or that are Production types, sold on
  the market or by contract (what exists today).
- **Trading** - imported goods (bought at a hub, hauled, sold at the home
  structure). Replaces today's Realized Trades reconciliation.
- **Doctrine** - finished doctrine contracts (fitted ships sold to corp or
  alliance members). New page, analogous to the other two, that shows for
  every contract how much of its cost came from **own production** and how
  much from **purchases/imports**.
- **Station Trading** - buy and sell orders at one hub station (default
  Jita 4-4), profit from the spread. New page.

Every sale belongs to exactly one tool, so the four realized profits add up
to the tenant's total realized profit without double counting. All three use
the same inventory (FIFO layers), the same purchase prices, fees, freight
and valuation, so the same item never has two different costs in two tools.

## 2. Today (verified 2026-10-18)

### Production (built, Phases 0-5)

Archive of wallet transactions, journal, jobs, orders, contracts and
blueprints (`ledger_*`), filled by the ESI fetchers for owners shared with
**Production** (D11). A pure FIFO costing engine
(`production/realized/costing.py`) builds layers (bought, produced,
opening, unknown, contract) and realized results. Only **Production types**
(types the tenant produces, plus stock targets) become results; every other
sale only consumes layers ("Trading's business", Production plan D6).
Opening and unknown stock is valued at the planner price (cheaper of the
buy-list price and the build cost, frozen per type and day). Real sales tax
from the journal, real broker fees spread per day (D12), freight frozen per
purchase at Production's rate. Report API, page, Portfolio card.

### Trading Realized Trades (`trade_reconciliation.py`)

- Each "Reconcile" run fetches the wallet for the lookback window (30 days,
  buys 60) live and **replaces** `realized_trades` wholesale. No history.
- FIFO per item, Jita buys against home-structure sells. A sell without an
  earlier buy in the window is **dropped** (PB-05, known under-report).
- Broker fees are modeled on both sides (`jita_buy_broker_fee`,
  `structure_broker_fee`). Sales tax is real when the tax entry sits at
  `journal_id + 1`, else the modeled `structure_sell_haircut`.
- Freight is `import_cost_per_m3` x the item's volume, today's rate applied
  to every past trade; for items no longer on the shortlist the volume is
  ESI's **unpackaged** volume (wrong for ships and capital modules).
- Read by the Realized Trades page and by Portfolio
  (`trading_realized_profit`, daily volatility); the shortlist no longer uses
  it (issue #100).

### Station Trading

Trades on one station's order book (`StationTradingConfig.station_id`,
default Jita 4-4, the same station Trading imports from). Reads market
orders and skills only - **no wallet** (`registry.py`: wallet is consumed by
Trading and Production). Shows live spreads; no cost or profit anywhere.
Its own broker fee and sales tax settings are planning inputs.

### Doctrine

`doctrine_contracts` is the active snapshot; `doctrine_contract_history`
keeps finished contracts (fitting, price, acceptor, dates). A doctrine
contract is an item exchange issued by the tenant (character or its corp)
at the doctrine structure. No cost or profit anywhere.

### Facts from the test server (Default tenant)

| Fact | Value |
|---|---|
| Freight rates | Production `haul_cost_per_m3` 900; Trading `import_cost_per_m3` 800; Doctrine `import_cost_per_m3` 900; hub table: The Forge 800, Domain 750 |
| Home structure | Trading, Production and Doctrine all use 1049588174021 |
| Market sells (seller) | 12,170 sells, 134.2 bn ISK; Production types 6,978 sells, 64.6 bn - the other ~70 bn are Trading goods |
| Market buys | Jita 4-4 by the buyer: 82.5 bn (imports); **at the home structure itself** (Insmother region, where C-J is) by the seller: 31.3 bn - home-market buys, correctly without freight; a few small buys at other stations |
| Station trading | **no sell at Jita 4-4 at all** - the tool is not in use for real trades on this tenant; one character shares data with it |
| Doctrine contracts | 47 finished in history since 2026-08-15 (9.86 bn); only 18 of them in the ledger archive (ESI lists ~30 days) |
| Realized Trades | 1,366 rows, 2.69 bn profit, last sell 2026-09-22 (reconcile has not run since) |
| Sharing | wallet: Production 2, Trading 2; contracts: Production 2, Doctrine 2 |

## 3. Core idea: sale channel and goods origin are two different things

Every realized result gets two independent labels:

- **Channel** - *who sold it and how*. Decides which tool reports the sale.
  `production`, `trading`, `doctrine`, `station_trading`.
- **Origin** of each unit's cost - *where the goods came from*. Already on
  every draw: `produced`, `bought` (market, with the hub region), `contract`
  (bought by contract), `opening`, `unknown`.

A doctrine contract can sell a hull the tenant built and modules it
imported: channel `doctrine`, origins `produced` + `bought`. The Doctrine
page shows exactly that split. A Production type sold on the market can come
from an import (origin `bought`): it stays a Production sale, with a lower
coverage of "own production". Trading goods that were never produced keep
origin `bought`/`opening`.

Channel rules (decision U1):

1. A finished item-exchange contract that is a **doctrine contract** (U7)
   -> `doctrine`, for **all** its items, at the contract price.
2. A market sale **at the Station Trading station** by an owner whose
   wallet is shared with Station Trading -> `station_trading` (ST2).
3. Any other contract sale and any market sale of a **Production type**
   -> `production` (today's rule).
4. Any other market sale (home structure or elsewhere) -> `trading`.
5. Other contract sales of non-Production types -> `trading`.

The tenant's total realized profit is the sum over all four channels.

## 4. Decisions

U1-U11 and ST1-ST3 confirmed with the user on 2026-10-18 (U11 changed:
Station Trading is in scope, see ST1-ST3). The alternatives are kept so the reasoning stays
visible.

**U1 Channel rules** - as in section 3. A doctrine contract wins over the
Production-type rule: a produced hull sold in a doctrine contract counts
for Doctrine, and Production's own report no longer shows it as a Production
sale (today it does, via Phase 5's contract sales). Production's page gets a
line "Produced goods sold through Doctrine" so that output is still visible.

**U2 One inventory, one FIFO** (confirmed). The three tools draw from one
set of layers per type (Production plan D2). Sale order stays Production
plan D6 for Production types (produced, then opening, then the rest); for
all other types plain FIFO. Alternative: separate inventories per tool -
rejected, the same physical item would carry two costs.

**U3 Sharing (D11) across the tools** - the important one.
- (a) *Confirmed:* the archive and the costing use the **union** of
  owners shared with any realized tool for a kind (wallet: Trading,
  Production, Station Trading; contracts: Doctrine, Production; jobs,
  blueprints: Production). Each tool's page shows only results of its own
  channel, and only when the selling owner is shared with that tool (market
  sales: wallet; doctrine contracts: contracts with Doctrine). A buy shared
  only with Trading can therefore cost a doctrine sale. Revoking a share
  hides the owner's rows from the archive only when no realized tool holds
  it any more; the owner-scope fingerprint becomes the union.
- (b) Strict per-tool scope: three costing runs, each over its own shares.
  Correct D11, but three different costs for the same item and no single
  total. Not recommended.
- (c) A new sharing axis "Realized profit" per kind on the Characters page.
  Cleanest semantically, more UI work, every tenant has to set it once.

**U4 Freight** - one rate per hub for a purchase, frozen at ingest
(confirmed): the shared hub freight table (`/api/hubs/freight`, already
tenant-wide) for every hub, fallback Production's `haul_cost_per_m3`. The
tools' own `import_cost_per_m3` fields stay planning inputs only. A purchase
at the home structure (or in its region) gets none; a purchase at another
station of a hub region gets that hub's rate. Changing the table
later is applied with the existing "re-apply current freight rate" (dry run,
then confirm).

**U5 Trading's opening and unknown stock** - valued like Production
(planner price, frozen per type and day; Forge average only as fallback),
flagged as estimated, coverage shown. This replaces PB-05's dropped sales.
For an item the planner could also build, the planner price may be the
build cost - acceptable, the value is "what it would cost the tenant now".

**U6 Fees** - Trading's modeled broker fees and sell haircut are retired;
real fees from the journal (D12) and real sales tax (with the id-gap fix)
apply to every channel. Contract sales carry no sales tax or broker fee
(contract broker fees from the journal are spread over the issuer's
contracts of that day, same idea as D12).

**U7 What is a doctrine contract** (confirmed): a finished item exchange
in the ledger archive whose issuer is a tenant character (including a corp
contract issued by that character, Production plan D7), at the doctrine
structure (`DoctrineConfig.effective_structure_id`), and whose owner shares
contracts with Doctrine. The fitting comes from `doctrine_contract_history`
when present, else from running the existing matcher
(`engine.match_and_validate_contract`) on the archived items; no match ->
"unmatched doctrine contract", still counted. Contracts between the
tenant's own characters or with its own corp stay internal transfers.

**U8 Corp-issued doctrine contracts** - ISK lands in the corp wallet, but
the contract is attributed to the issuing character (same as D7 for corp
jobs). Confirmed for Production contracts already; applies here too.

**U9 No internal transfer price** (confirmed). A produced hull sold in a
doctrine contract enters at its production cost; the whole margin is
Doctrine's. The Doctrine page shows for the produced part an informational
"production saving" = planner buy price - production cost, so the value
own production adds is visible without moving profit between tools.
Alternative: book an internal sale from Production to Doctrine at market
value - two profits for one ISK flow, harder to reconcile, not recommended.

**U10 Retire `realized_trades`** after the new Trading report is live:
Portfolio's Trading figures read the ledger; the Reconcile button becomes
"Recompute"; the table and `trade_reconciliation.py` are removed one
release later (kept read-only for comparison in between).

**U11 Scope** (confirmed, changed from the draft): **Station Trading is a
fourth channel** (ST1-ST3). Still out of scope: courier income, PI income,
mining. Their items still move stock; a warning lists sales that fit no
channel rule so nothing is silently lost.

**ST1 Where station-trading stock comes from** (confirmed: station pool first). The Production
plan's D2 ignores location because moves between stations are invisible in
ESI. Station Trading breaks that assumption: it buys and sells at the same
station Trading imports from, and a Jita buy can be a station trade (stays
and is resold there) or an import (hauled home).
- *Confirmed - station pool first:* a buy **at the Station Trading
  station** goes into that station's pool. A sell at that station draws
  from the station pool first (FIFO), then from the global pool. A sell
  anywhere else (home structure) draws from the global pool, and when that
  runs out, from the station pool (the item was evidently hauled). Station
  trades then match their own buys; imports still find their Jita buys.
- Alternative - by character: buys of characters that share their wallet
  only with Station Trading form the station pool. Simpler, but breaks when
  one character does both.
- Alternative - global FIFO (D2 unchanged): a station sale may draw an
  import lot and the reverse; Station Trading margins become meaningless.

**ST2 Station Trading needs the wallet** (confirmed). `wallet` gets
`station_trading` as a consuming tool (`esi_data/registry.py`), so the
Characters page offers the switch. Only sales by owners sharing their wallet
with Station Trading get the `station_trading` channel; a sale at that
station by anyone else stays `trading`/`production`.

**ST3 Station Trading's fees** (confirmed): real fees like every channel
(U6) - broker fees per day spread over that day's fills, which already
covers both legs (buy orders and sell orders) and relists; sales tax from
the journal. The tool's `broker_fee_rate`/`sales_tax_rate` stay planning
inputs. Station-trade buys get no freight (same station).

## 5. Data and engine changes

### 5.1 Archive scope (Phase 1)

- `production/realized/access.py` becomes `realized/access.py` at a shared
  level (`eve_trader/realized/`), with `shared_owners(kind)` returning the
  union over the realized tools (U3) and `shared_owners_for(kind, tool)` for
  report filtering. `owner_scope_hash` covers the union.
- The fetcher hook (`esi_data/fetchers.py`) archives an owner/kind when it
  is shared with any realized tool (today: Production only). CLAUDE.md's
  one-exception note about the lazy import is updated, not widened: it stays
  one import, of the shared package.
- D10 retention exception extends to tenants holding `trading` or
  `doctrine`.
- Courier contracts and contracts archive unchanged.

### 5.2 Package layout (Phase 1-2)

Move the generic parts out of Production so Trading and Doctrine do not
import `production.*`:

```
eve_trader/realized/            # shared: ingest, access, db, costing, loader, names, valuation
eve_trader/production/realized/ # Production report + actions (thin)
eve_trader/trade_realized/      # Trading report + actions (new; trade_reconciliation retired)
eve_trader/doctrine/realized.py # Doctrine report + actions (new)
eve_trader/station_trading/realized.py # Station Trading report + actions (new)
```

Valuation (`planner_unit_values`) stays in `production/engine.py`; the
shared loader calls it through one function (the planner is the tenant's
price model regardless of tool). Schema stays in
`docs/production_ledger_schema.sql` (renaming the file would need every
deploy list touched for no gain); new columns are idempotent additions.

### 5.3 Costing (Phase 2)

- `RealizedResult` gets `channel` (U1). Results are produced for **every**
  sale, not only Production types: a Trading sale becomes a result instead
  of a bare consumption.
- `ItemExchangeContract` gets `is_doctrine` (set by the loader, U7) and
  `fitting_id`. A doctrine contract books **one result per contract**
  (`sale_ref = contract:<id>`) with all items' draws, revenue = contract
  price, channel `doctrine`. Per-type results inside it are not created.
- Market-sale channel: `station_trading` at the Station Trading station
  for an owner sharing the wallet with it (ST2), else `production` when the
  type is a Production type, else `trading`.
- Layers get a `pool` (`global` or the Station Trading station id) and the
  draw order of ST1.
- Purchases in any hub region other than the home region get frozen freight
  from the hub table (U4); the Forge-only rule goes.
- Sales tax lookup keeps the id-gap fix; broker fees per day (D12) are
  spread over that day's order fills of **all** channels (today only the
  Production-relevant ones share the pool - check, and keep one pool per
  owner and day so the total is paid exactly once).
- Derived tables get a `channel` column (`prod_realized_results`,
  `prod_cost_draws` per consumer). `COSTING_VERSION` 4 forces one opening
  reset (opening quantities change: Trading types now have opening layers
  too).

### 5.4 Reports (Phases 3-4)

All three reports share the report helpers (period, flags, coverage, names,
cost structure, chain) and filter by channel.

**Trading** (`/api/trading/realized/...`, page replaces Realized Trades):
summary (revenue, cost of goods sold, profit, margin, coverage), per item
(units, avg buy landed, avg net sell, profit, margin), per sale with the
purchase lots it drew (date, hub, price, freight, fees), inventory of
Trading goods at cost, warnings with names, period selector, daily chart.
The old "top 3 items" becomes the sorted item table; cumulative chart stays.

**Doctrine** (`/api/doctrine/realized/...`, page under Doctrine):
- Summary: contracts sold, revenue, cost, profit, margin, coverage;
  **cost by origin**: own production, market purchases (per hub),
  contract purchases, opening/unknown (estimated).
- Per fitting: contracts, avg price, avg cost, avg profit, share produced
  vs bought.
- Per contract: date, fitting, buyer (name), price, cost, profit, and a
  drill-down of every item line: quantity, origin, unit cost, and for
  produced items the job chain (reuses the Production cost chain).
- Production saving per contract (U9, information only).

**Station Trading** (`/api/station-trading/realized/...`, page under
Station Trading): summary, per item (units traded, avg buy, avg sell net of
fees, spread captured, profit, margin, turnover days = time from buy to
sell), per sale with the buy lot, fees per day, open position at cost
(station pool left).

**Production**: unchanged except channel filter, the new "sold through
Doctrine" line (U1), and its contract sales no longer including doctrine
contracts.

**Portfolio**: one "Realized profit" card per tool the session holds, plus
a total when it holds more than one (up to four).

## 6. Phases

Each phase ends with: full `pytest`, frontend lint/test/build, a review, a
test-server run against the Default tenant's real data, and an update of
this plan's status section.

**Phase 0 - confirm and measure (no code).** Confirm U1-U11. On the test
server: count doctrine contracts per month and their items' types; compare
Trading's current `realized_trades` totals with what the new rules will
produce for the same window (a read-only script against the archive) so the
expected difference is known before switching; list buy locations per region
and which of them get freight under U4.

**Phase 1 - shared archive scope.** Package move (5.2) without behaviour
change, then the union scope (U3) and retention. Tests: archive filled for
an owner shared only with Trading or only with Doctrine; revoking the last
realized tool hides; fingerprint changes. Production report numbers
unchanged on the test server (regression check).

**Phase 2 - channels in the costing.** Channel on results and draws,
Trading results, doctrine contract results, hub freight, fee pool check,
`COSTING_VERSION` 4. Tests: pure costing cases for every channel rule, a
doctrine contract with produced hull plus bought modules (origin split sums
to cost), internal transfers, corp-issued contracts, a hub buy with freight and a home-market buy
without. Production regression: same numbers except the documented doctrine
move.

**Phase 3 - Trading report and page.** API, page, Portfolio, Recompute
button wiring (the trading pipeline triggers the shared rebuild instead of
reconciliation), side-by-side comparison with `realized_trades` on the test
server. `realized_trades` stays read-only (U10).

**Phase 4 - Doctrine report and page.** Doctrine identification (U7) incl.
fitting resolution, API, page, Portfolio card. Test server: every finished
doctrine contract in the archive appears once, with an origin split.

**Phase 4b - Station Trading.** Wallet sharing for Station Trading (ST2),
station pool (ST1), channel, report, page, Portfolio card. Needs a
character that actually station-trades on the test server; if none does,
the phase is verified with a seeded test tenant and the Default tenant only
confirms nothing else changed.

**Phase 5 - clean-up.** Remove `trade_reconciliation.py` and
`realized_trades` (with the Portfolio history figures that need it switched
first), update CLAUDE.md (price sources matrix, realized section, the
fetcher exception), README.

## 7. Risks and known limits

- **History depth.** ESI lists ~30 days of transactions and contracts; the
  archive starts when the feature is live. Doctrine history before that
  (back to 2026-08-15) has no costs and is not shown as realized profit -
  the page can list those contracts as "before the archive" without profit.
- **Sharing semantics (U3a)** widen what a cost can draw on; the page must
  say which owners are included. If the user prefers strict scopes, U3c is
  the clean path, at the cost of a Characters-page change.
- **Station pool (ST1)** is a heuristic: an item bought at the station and
  hauled home looks like station stock until a home sale needs it. Drift
  checks per pool would be wrong for the same reason, so drift stays per
  type (global).
- **Performance.** All channels in one rebuild: ~2x the results of today
  (12k sells instead of 7k on the test server); rebuild stays a background
  job (today 15-45 s).
- **Planner valuation for Trading goods** depends on live market quotes at
  first use, like Production (frozen afterwards).
