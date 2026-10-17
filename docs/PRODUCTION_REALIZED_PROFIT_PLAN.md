# Production realized profit - plan

Status: **planned, not started** (2026-10-17). Decisions D1-D9 (section 10)
are confirmed with the user.

## 1. Goal

Production today only shows *planned* figures: build cost, margin and
`potential_daily_profit` come from Goonmetrics quotes, the Jita price cache and
modeled fees (`production/pricing.py`, `production/engine.py`). Nothing tells
the user what a finished product **actually** earned.

This plan adds a **realized profit and loss for Production** over the whole
value chain:

```
bought inputs ─┐
contract buys ─┼─> reaction jobs ─> intermediates ─┐
freight paid ──┘                                   ├─> manufacturing job ─> product ─> sale (market / contract)
invention: datacores + decryptor + copy job ─> T2 BPC ─┘
job install costs, taxes, broker fees, sales tax at every step
```

Every number comes from ISK that was really paid or received (wallet
transactions, wallet journal, industry job costs, contracts). Market quotes are
used only where no real figure exists, and then always flagged (see section 6,
"Coverage").

Out of scope: changing how Production *plans* (buy-vs-build, stock targets).
The planner keeps using quotes; the realized figures are a separate, read-only
view. A planned-vs-actual comparison is a later phase (section 9, phase 5).

## 2. What exists today

| Data | Where | Problem for this feature |
|---|---|---|
| Wallet transactions | `esi_wallet_transactions` (snapshot, DELETE-then-INSERT per sync) | ESI only returns about 30 days; older rows disappear on the next sync. Consumed only by `trading`. |
| Wallet journal | `esi_wallet_journal` (snapshot) | Same 30-day window. No `context_id`/`context_id_type`, so a journal entry cannot be linked to a job, transaction or contract (`trade_reconciliation._journal_from_snapshot` documents this gap). |
| Industry jobs | `character_industry_jobs`/`corp_industry_jobs` (snapshot) | Fetched with `include_completed=false`. The ESI fields `cost`, `blueprint_id`, `facility_id`, `licensed_runs`, `successful_runs`, `completed_date` are not stored. |
| Blueprints | `character_blueprints`/`corp_blueprints` (snapshot) | ME/TE are known only while the blueprint exists. A BPC consumed by a job is gone after the next sync. |
| Contracts | consumed by Doctrine only | Prices/items of bought or sold item-exchange contracts are not kept for Production. |
| Trading realized P&L | `trade_reconciliation.py`, `realized_trades` | FIFO buy/sell matching for import trading only. Its real-tax linkage (journal `context_id`) is the model to copy. |

The central consequence: **the data needed for real costs exists in ESI, but
the app throws it away after 30 (wallet) or 0 (finished jobs) days.** A job
chain for a capital or T2 product easily spans more than 30 days from buying
the first mineral to selling the product. So the first deliverable must be an
append-only archive; it has to run for a while before the report is useful.
Nothing that ESI has already dropped can be backfilled.

## 3. Core idea: a perpetual inventory with cost layers

Track every type the tenant owns as a stock of **cost layers** (quantity, unit
cost, origin, date), fed only by real events:

| Event | Inventory effect | Cost source |
|---|---|---|
| Market buy (wallet transaction `is_buy`) | + layer | `unit_price x quantity` + buy-order broker fee (journal `brokers_fee`) |
| Contract buy (item exchange, we pay) | + layers per item | contract price (journal `contract_price`), split across items (D4) |
| Freight for every unit bought in The Forge | adds cost to that purchase's layer | configured freight rate x packaged m3 (section 5a, D9) |
| Industry job start (manufacturing, reaction, copy, invention) | - inputs consumed (FIFO) | inputs computed from SDE recipe + blueprint ME + structure bonus (section 5) |
| Job cost | adds to the job's output | ESI job `cost` + journal `industry_job_tax` linked by `context_id = job_id` |
| Job delivered | + layer of the product (or T2 BPC) | sum of consumed input cost + job cost, divided by output quantity |
| Market sell at the home structure | - product (FIFO) -> **realized result** | gross from transaction, minus real `transaction_tax` (journal, `context_id`), minus broker/relist fees |
| Contract sell (Special Orders, Doctrine contracts) | - items -> realized result | journal `contract_price` received |
| Own use (fitted, given away, Doctrine stock) | - items, no result | transfer at cost, reported separately |

Profit of a sale = net revenue - FIFO cost of the units sold. Because a
product's cost layer already contains its full input tree, the realized profit
covers the whole chain. Each produced layer keeps a link to its job, so the
report can drill down: product -> job -> consumed layers -> their jobs or
purchases.

**The ledger is the source of truth; cost layers are derived.** The archive
tables (section 4) hold raw, append-only ESI facts. Costing is a pure function
that rebuilds all layers from the ledger. A fix to the costing logic is then a
recompute, never a data migration, and never loses history.

## 4. Data model (new schema file `docs/production_ledger_schema.sql`)

All tables are per-tenant with RLS, `tenant_id` default from `app.tenant_id`,
composite primary keys starting with `tenant_id` (ESI ids are not
tenant-unique - see the corp T1-04 comments in `phase1_schema.sql`).
A new schema file must be added to `deploy/deploy.sh`, `deploy/README.md`, the
root `README.md` and `.cursor/start.sh` (CLAUDE.md, "A brand-new schema file").

Raw archive (append-only, upsert by natural key, never deleted by a sync):

- `ledger_wallet_transactions` - same columns as `esi_wallet_transactions`
  plus `region_id`, `freight_rate_per_m3`, `freight_m3` (frozen at ingest for
  The Forge buys, section 5a),
  and `first_seen_at`.
- `ledger_wallet_journal` - adds `context_id`, `context_id_type`,
  `first_party_id`, `second_party_id`, `description`, `tax`, `tax_receiver_id`
  to today's columns.
- `ledger_industry_jobs` - every ESI job field we need: `job_id`,
  `activity_id`, `blueprint_id`, `blueprint_type_id`, `product_type_id`,
  `runs`, `licensed_runs`, `successful_runs`, `cost`, `facility_id`,
  `station_id`, `output_location_id`, `installer_id`, `start_date`,
  `end_date`, `completed_date`, `status`, `probability`; plus owner columns.
- `ledger_job_inputs` - inputs computed **once, when the job is first seen**
  and then frozen: `job_id`, `type_id`, `quantity`, plus how it was computed
  (`blueprint_me`, `structure_material_bonus`, `source` = `esi_blueprint` /
  `manual_blueprint` / `assumed_me`). Freezing matters because the blueprint
  (and its ME) may be gone by the next sync, and config can change later.
- `ledger_contracts`, `ledger_contract_items` - item-exchange and courier
  contracts where a tenant character/corp is issuer or acceptor.
- `ledger_manual_entries` - user-entered costs/revenues and opening
  balances (purchases outside ESI, freight paid in cash, corrections). Same
  "additive, never replaces ESI" rule as Manual tracking.

Derived (rebuildable, safe to truncate):

- `prod_cost_layers` - one row per layer: type, quantity left, unit cost,
  origin (`bought`, `contract`, `produced`, `opening`, `unknown`), source ref.
- `prod_realized_results` - one row per sale/consumption match: sale ref,
  type, quantity, revenue, cost, profit, coverage share.
- `prod_ledger_runs` - when the last recompute ran, ledger high-water marks,
  warnings.

## 5. Computing consumed inputs

ESI does not report which materials a job consumed. They are computed from the
SDE recipe the same way the planner does (`engine._material_qty`), with:

1. **Blueprint ME**: `blueprint_id` of the job matched to the blueprints
   snapshot (`item_id`) at the time the job is first seen. A BPC consumed by
   the job may already be gone; then a manual blueprint, then the activity's
   default ME (0 for T2/faction, configured baseline for T1) - flagged as
   `assumed_me`. Jobs should be picked up quickly (industry_jobs is in the
   normal tier); a job finished and delivered between two syncs is still in
   ESI's completed list, but its BPC ME may then be unknown.
2. **Structure/rig material bonus**: from `facility_id`. If it is the home
   structure, use `ProductionConfig`'s structure/rig settings for the
   activity; otherwise use no bonus and flag it (or a per-facility override -
   later).
3. **Invention**: datacores + decryptor per attempt x `runs` (all attempts
   consume, success or not). Output = `successful_runs` x BPC runs per success
   (decryptor-adjusted). The T1 BPC used is costed at its copy-job cost. The
   T2 BPC becomes a cost layer of its own; a manufacturing job then consumes
   "BPC runs" from it.
4. **Reactions**: same as manufacturing (activity 11).
5. **Copying/research**: job cost only; copy output feeds invention. BPOs are a
   capital asset, not a consumed input (D3).

`order_integrity.py`/`engine.py` already contain the recipe and bonus logic;
reuse it, do not re-derive it.

## 5a. Freight for items bought in The Forge (D9)

Everything bought in The Forge (region 10000002, wallet transaction
`location_id` resolved to its region) still has to be hauled to the home
structure. The user ships it with courier contracts, but the freight cost used
here is **the rate configured in Settings**, not the courier rewards paid:

- Rate: `hubs.hub_freight_per_m3(10000002, fallback=ProductionConfig.
  haul_cost_per_m3)` - the Jita/The Forge entry of the shared hub freight
  table, else Production's own "haul cost per m3". Same lookup the planner
  uses, so planned and realized freight agree.
- Volume: packaged volume of the type (`storage.get_cached_packaged_volume`,
  SDE volume otherwise) x quantity bought.
- Freight cost = rate x m3, added to the purchase's cost layer (unit cost =
  (price + broker fee + freight) / quantity) and shown as its own cost
  component in the drill-down and the summary.
- The rate is **frozen per purchase at ingest time** (stored on the ledger
  row, like `ledger_job_inputs`). Settings have no history; a later rate
  change must not rewrite past results. A recompute keeps the frozen rate; an
  explicit "re-apply current freight rate" action can update it if wanted.
- Purchases at the home structure itself carry no freight. Purchases in other
  regions are not freighted in this version (listed as a warning if they
  occur), since only The Forge -> home is a regular route.
- Freight counts as a real cost in both D5 views (market value and cash cost).
  It is labeled "configured rate" in the report so it is never mistaken for a
  paid courier reward.

## 6. Unknown cost basis and coverage

Some inputs will have no real purchase behind them:

- stock that existed before the archive started (opening inventory),
- mined ore, refined minerals, PI products from own colonies,
- loot, gifts, items moved in from another tenant/corp,
- purchases older than the archive or outside ESI's view.

Stock present at go-live is an `opening` layer placed first in FIFO order
(D8). When a consumption or sale still finds no layer (or not enough), it
draws from an `unknown` layer. Both are valued per D5 (market value view and
cash cost view). Every result row stores which share of its cost came from
real figures. The report shows a **coverage %**
per product and in total, and a figure is labeled "estimated" below a
threshold. This is the honest version of the rule "only prices that were really
paid": where none exists, the user sees that, instead of a silently invented
number.

Inventory drift check: compare ledger stock (sum of layers) per type with the
ESI asset snapshot (assembled ships/fitting excluded, same filter as every
stock reader - CLAUDE.md "Assembled ships are not stock"). A large gap means
missing events (losses, gifts, transfers, untracked characters) and is shown as
a warning, not corrected silently.

## 7. Separation from Trading and other tools

The same characters can buy for Trading and for Production, and Trading has
its own FIFO in `trade_reconciliation.py`. Proposal (D6):

- One tenant-wide inventory per type. Production costing only *consumes*
  layers through job inputs and only *realizes* sales of types that have a
  `produced` layer.
- A sale of a type with produced and bought layers takes produced layers first
  for Production's report; bought-only types stay Trading's business.
- Trading's `realized_trades` is not changed. Double counting is possible only
  for a type that is both imported for resale and produced - listed in the
  report as an overlap warning.

Corp wallets/jobs (D7): corp data is shared across tenants (corp 98370861 is
in 5 tenants) and corp wallet entries do not say which member acted. Corp jobs
are attributed by `installer_id` (a tenant's own character); corp wallet
transactions only for corps the user marks as "mine alone" (same single-member
assumption Trading already documents).

## 8. Architecture (repo conventions)

- Package `eve_trader/production/realized/`: `ingest.py` (ESI rows -> ledger),
  `inputs.py` (section 5), `costing.py` (pure: ledger -> layers + results, no
  I/O), `report.py` (aggregation for the API).
- `do_*` actions in `production/actions.py`: `do_rebuild_realized`,
  `do_get_realized_summary`, `do_get_realized_product`, `do_add_ledger_entry`,
  `do_set_opening_inventory`. Router `/api/production/realized/...`, thin.
  CLI `eve-trader production realized rebuild|summary`.
- ESI: archive writes happen inside the existing fetchers
  (`esi_data/fetchers.py`), right after the snapshot write, in the same sync.
  No new scheduler job. `"production"` is added to `consuming_tools` of
  `wallet` and `contracts` in `esi_data/registry.py`, so sharing stays
  explicit and fail-closed. Industry jobs: a second ESI call with
  `include_completed=true` for the archive only - the snapshot keeps
  `include_completed=false`, so `jobs.py` and the planner are not affected.
- Journal `context_id` is also added to `esi_wallet_journal`; this unblocks the
  deferred follow-up in `trade_reconciliation._journal_from_snapshot` (real
  sales tax from snapshots) as a side effect.
- Recompute: on demand (button / CLI) and after each wallet/jobs sync for the
  tenant, cached with explicit invalidation (CLAUDE.md caching pattern 3).
- Retention risk: tenants inactive for `inactive_tenant_days` (14) get no
  scheduled syncs; after about 30 days of inactivity wallet history is lost.
  The report shows "archive gaps" (periods with no successful wallet sync).

## 9. Phases

**Phase 0 - live verification (read-only, one real character).** Confirm
before building on it:
- V1: does job `cost` already include SCC surcharge and facility tax, or is
  `industry_job_tax` in the journal an additional amount? (Avoid counting it
  twice.)
- V2: journal `context_id_type` for `industry_job_tax`, `brokers_fee`,
  `contract_price`, `contract_reward` - which id do they carry?
- V3: how long ESI keeps completed jobs; is `successful_runs` filled for
  invention; does `blueprint_id` equal the blueprints snapshot `item_id`?
- V4: can a buy-order fill be told apart from an instant buy (broker fee
  only applies to the first)? Via `orders/history` + `brokers_fee` context.
- V5: contract items endpoint for finished contracts (retention).

**Phase 1 - archive (start as early as possible).** Ledger tables, ingest in
fetchers, journal `context_id`, completed-jobs fetch, frozen job inputs.
No UI. Tests: upsert idempotence, no deletion on resync, RLS. Every week this
runs earlier is a week of real history the report can use later.

**Phase 2 - costing engine.** Pure `costing.py` with FIFO layers, job
consumption, invention and reaction chains, unknown-basis fallback, coverage.
Golden tests on hand-built chains (minerals -> reaction -> T2 component ->
invention -> T2 module -> sale), including partial sales and a job with an
unknown-ME BPC. Heavy enough to delegate to an Opus subagent (CLAUDE.md
preference).

**Phase 3 - report and API.** Realized P&L per period, per product, per job;
drill-down tree; coverage; drift and overlap warnings; CLI.

**Phase 4 - frontend.** New Production page "Realized Profit": summary
(revenue, cost, profit, margin, coverage), table per product, drill-down of
one sale's cost chain, warnings. Opening-inventory and manual-entry forms.

Freight (section 5a) is part of phases 1-3: the rate is frozen in phase 1's
ingest, applied in phase 2's costing, shown as its own component in phase 3.

**Phase 5 - extensions.** Contract buys/sells; optional comparison of the
configured freight rate with courier rewards actually paid; PI and
refining origin costs (customs/reprocessing tax from the journal); planned vs
actual per product (engine estimate at job start vs realized); Portfolio
integration (realized Production profit next to Trading's).

## 10. Decisions (confirmed with the user 2026-10-17)

- **D1 cost method: FIFO layers**, not moving average. Matches Trading, keeps
  the drill-down to individual jobs/purchases, deterministic.
- **D2 one inventory per type for the whole tenant**, ignoring location.
  Moves between stations are invisible in ESI; per-location layers would
  mismatch constantly. Freight is added at purchase time instead (D9).
- **D3 BPOs are not part of unit cost.** Shown as a separate "capital in
  BPOs" line, no amortization; a BPO sale shows its own gain/loss (sale price
  minus purchase price). Copy/research/invention job costs are counted.
- **D4 contract price split across items by reference market value** at the
  contract date. The quote is only a weight; the split always sums to the real
  price paid. A single-type contract needs no split. Bought BPCs get their
  share and are then consumed per run.
- **D5 unknown cost basis: both views.** Every cost is tagged by origin
  (`bought`, `contract`, `produced`, `own` = mined/refined/PI, `opening`,
  `unknown`). The report shows two profits side by side:
  - *Profit at market value*: own/unknown inputs valued at the reference
    price on the day of use - the real production profit.
  - *Profit at cash cost*: own/unknown inputs count only with the ISK
    actually paid for them (customs, reprocessing tax from the journal, else
    0) - what really came in.
  Coverage % (share of cost from real figures) is shown with both.
- **D6 Trading overlap: produced layers first, Trading untouched.** A sale
  counts for Production up to the produced quantity; any remainder stays
  Trading's. `trade_reconciliation.py` is not changed; types that are both
  produced and imported are listed as an overlap warning.
- **D7 corp data:** corp jobs are always included when `installer_id` is one
  of the tenant's characters. Corp wallets are used only for corps (optionally
  per wallet division) the user explicitly marks as "mine alone" - a per-tenant
  setting, default off. Without it, inputs bought from a corp wallet have
  `unknown` origin (D5).
- **D8 opening inventory: automatic, manually overridable.** At go-live the
  opening quantity per type is derived as: current ESI stock minus net ledger
  flows since the archive's earliest event (bought + produced - consumed -
  sold). It becomes the *oldest* layer (so FIFO consumes it first, not the
  newer known purchases), origin `opening`, valued at the reference price of
  the go-live day and treated like `unknown` in D5. The user can override the
  unit cost per type with a real purchase price (`ledger_manual_entries`).
- **D9 freight (added 2026-10-17):** every unit bought in The Forge gets
  freight to the home structure added to its cost, at the freight rate
  configured in Settings (hub freight table entry for The Forge, else
  Production's `haul_cost_per_m3`) x packaged m3, frozen per purchase at
  ingest. Courier rewards actually paid are not used as the cost (user's
  choice); see section 5a.

## 11. Known limitations (by design)

- Consumed inputs are computed, not observed (ESI has no such data). A wrong
  ME or structure bonus shows up as inventory drift, not as a wrong-looking
  number.
- Losses (destroyed ships/cargo), gifts, and transfers between characters of
  different tenants are invisible unless entered manually.
- History before Phase 1 goes live is limited to what ESI still holds at that
  moment (about 30 days of wallet, the completed jobs ESI still lists).
- Broker fees are charged per order, not per fill; they are spread over the
  order's fills by quantity.
