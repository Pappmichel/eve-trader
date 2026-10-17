# Realized profit for Production, Trading and Doctrine - unified plan

Status: **draft, decisions open** (2026-10-18). Nothing in this plan is built
yet. The decisions in section 4 need the user's confirmation before Phase 1
starts; each phase ends with a review and a test-server check like the
Production realized-profit work (`docs/PRODUCTION_REALIZED_PROFIT_PLAN.md`,
referred to as "the Production plan" below).

## 1. Goal

One realized-profit ledger for the whole tenant, read by three tools:

- **Production** - goods it produced or that are Production types, sold on
  the market or by contract (what exists today).
- **Trading** - imported goods (bought at a hub, hauled, sold at the home
  structure). Replaces today's Realized Trades reconciliation.
- **Doctrine** - finished doctrine contracts (fitted ships sold to corp or
  alliance members). New page, analogous to the other two, that shows for
  every contract how much of its cost came from **own production** and how
  much from **purchases/imports**.

Every sale belongs to exactly one tool, so the three realized profits add up
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
| Market buys | The Forge 83.9 bn; **Insmother (10000009) 31.3 bn** - not in the hub freight table, gets no freight today |
| Doctrine contracts | 47 finished in history since 2026-08-15 (9.86 bn); only 18 of them in the ledger archive (ESI lists ~30 days) |
| Realized Trades | 1,366 rows, 2.69 bn profit, last sell 2026-09-22 (reconcile has not run since) |
| Sharing | wallet: Production 2, Trading 2; contracts: Production 2, Doctrine 2 |

## 3. Core idea: sale channel and goods origin are two different things

Every realized result gets two independent labels:

- **Channel** - *who sold it and how*. Decides which tool reports the sale.
  `production`, `trading`, `doctrine`.
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
2. Any other contract sale and any market sale of a **Production type**
   -> `production` (today's rule).
3. Any other market sale at the home structure (or anywhere) -> `trading`.
4. Other contract sales of non-Production types -> `trading`.

The tenant's total realized profit is the sum over all three channels.

## 4. Decisions to confirm

Recommendations are marked; alternatives are listed so the choice is
explicit.

**U1 Channel rules** - as in section 3. A doctrine contract wins over the
Production-type rule: a produced hull sold in a doctrine contract counts
for Doctrine, and Production's own report no longer shows it as a Production
sale (today it does, via Phase 5's contract sales). Production's page gets a
line "Produced goods sold through Doctrine" so that output is still visible.

**U2 One inventory, one FIFO** (recommended). The three tools draw from one
set of layers per type (Production plan D2). Sale order stays Production
plan D6 for Production types (produced, then opening, then the rest); for
all other types plain FIFO. Alternative: separate inventories per tool -
rejected, the same physical item would carry two costs.

**U3 Sharing (D11) across three tools** - the important one.
- (a) *Recommended:* the archive and the costing use the **union** of
  owners shared with any of Production, Trading or Doctrine for a kind
  (wallet: Trading/Production; contracts: Doctrine/Production; jobs,
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
(recommended): the shared hub freight table (`/api/hubs/freight`, already
tenant-wide) for every hub, fallback Production's `haul_cost_per_m3`. The
tools' own `import_cost_per_m3` fields stay planning inputs only. This also
gives Insmother buys freight once the table has an entry (today: none). A
purchase in the home structure's own region gets none. Changing the table
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

**U7 What is a doctrine contract** (recommended): a finished item exchange
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

**U9 No internal transfer price** (recommended). A produced hull sold in a
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

**U11 Out of scope**: Station Trading (buys and sells at one station, own
tool), courier income, PI income, mining. They remain consumers of layers
where their items pass through (a station-traded item still moves stock),
but no channel and no report in this plan.

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
- Market-sale channel: `production` when the type is a Production type,
  else `trading`.
- Purchases in any hub region get frozen freight from the hub table (U4);
  the Forge-only rule goes.
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

**Production**: unchanged except channel filter, the new "sold through
Doctrine" line (U1), and its contract sales no longer including doctrine
contracts.

**Portfolio**: one "Realized profit" card per tool the session holds, plus
a total when it holds more than one.

## 6. Phases

Each phase ends with: full `pytest`, frontend lint/test/build, a review, a
test-server run against the Default tenant's real data, and an update of
this plan's status section.

**Phase 0 - confirm and measure (no code).** Confirm U1-U11. On the test
server: count doctrine contracts per month and their items' types; compare
Trading's current `realized_trades` totals with what the new rules will
produce for the same window (a read-only script against the archive) so the
expected difference is known before switching; list hubs with buys and
missing freight table entries.

**Phase 1 - shared archive scope.** Package move (5.2) without behaviour
change, then the union scope (U3) and retention. Tests: archive filled for
an owner shared only with Trading or only with Doctrine; revoking the last
realized tool hides; fingerprint changes. Production report numbers
unchanged on the test server (regression check).

**Phase 2 - channels in the costing.** Channel on results and draws,
Trading results, doctrine contract results, hub freight, fee pool check,
`COSTING_VERSION` 4. Tests: pure costing cases for every channel rule, a
doctrine contract with produced hull plus bought modules (origin split sums
to cost), internal transfers, corp-issued contracts, an Insmother buy with
freight. Production regression: same numbers except the documented doctrine
move.

**Phase 3 - Trading report and page.** API, page, Portfolio, Recompute
button wiring (the trading pipeline triggers the shared rebuild instead of
reconciliation), side-by-side comparison with `realized_trades` on the test
server. `realized_trades` stays read-only (U10).

**Phase 4 - Doctrine report and page.** Doctrine identification (U7) incl.
fitting resolution, API, page, Portfolio card. Test server: every finished
doctrine contract in the archive appears once, with an origin split.

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
- **Station Trading** sells from the same assets in some setups; its sales
  consume layers but are not reported (U11). A warning lists such sales so
  they are not silently lost.
- **Performance.** All channels in one rebuild: ~2x the results of today
  (12k sells instead of 7k on the test server); rebuild stays a background
  job (today 15-45 s).
- **Planner valuation for Trading goods** depends on live market quotes at
  first use, like Production (frozen afterwards).
