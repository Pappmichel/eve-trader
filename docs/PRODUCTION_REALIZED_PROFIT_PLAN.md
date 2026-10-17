# Production realized profit - plan

Status: **Phase 4 done** (2026-10-17). Decisions D1-D12 (section 10)
are confirmed with the user. Revised the same day after a critical review
against the code (section 12 lists what changed and why). See
**Implementation status** at the end of this file.

## 1. Goal

Production today only shows *planned* figures: build cost, margin and
`potential_daily_profit` come from Goonmetrics quotes, the Jita price cache and
modeled fees (`production/pricing.py`, `production/engine.py`). Nothing tells
the user what a finished product **actually** earned.

This plan adds a **realized profit and loss for Production** over the whole
value chain:

```
bought inputs (+ freight from The Forge) ─┐
contract buys ────────────────────────────┼─> reaction jobs ─> intermediates ─┐
                                          │                                   ├─> manufacturing job ─> product ─> sale
invention: datacores + decryptor + T1 BPC runs (copy job) ─> T2 BPC ──────────┘
job install costs, broker fees, sales tax at every step
```

Every number comes from ISK that was really paid or received (wallet
transactions, wallet journal, industry job costs, contracts). Two deliberate
exceptions, both always labeled in the report:

- **Freight** from The Forge uses the rate configured in Settings (D9).
- **Inputs without a known purchase** (stock older than the archive, mined,
  refined, PI, loot) use a reference market price (D5), and the report shows
  how much of each figure rests on such estimates (coverage %).

Out of scope: changing how Production *plans* (buy-vs-build, stock targets).
The planner keeps using quotes; the realized figures are a separate, read-only
view. A planned-vs-actual comparison is a later phase (section 9, phase 5).

## 2. What exists today

| Data | Where | Problem for this feature |
|---|---|---|
| Wallet transactions | `esi_wallet_transactions` (snapshot, DELETE-then-INSERT per sync) | ESI returns at most 30 days, for a busy seller only about a week (journal capped at 10,000 entries, section 13); older rows disappear on the next sync. Consumed only by `trading`. No `order_id`. |
| Wallet journal | `esi_wallet_journal` (snapshot) | Same window (10 pages max). No `context_id`/`context_id_type`, so a journal entry cannot be linked to a job, transaction or contract (`trade_reconciliation._journal_from_snapshot` documents this gap). |
| Industry jobs | `character_industry_jobs`/`corp_industry_jobs` (snapshot) | Fetched with `include_completed=false`. The ESI fields `cost`, `blueprint_id`, `facility_id`, `licensed_runs`, `successful_runs`, `completed_date` are not stored. Invention jobs do not say which decryptor was used. |
| Blueprints | `character_blueprints`/`corp_blueprints` (snapshot) | ME/TE are known only while the blueprint exists. A BPC consumed by a job is gone after the next sync. |
| Contracts | consumed by Doctrine only | Prices/items of bought or sold item-exchange contracts are not kept for Production. |
| Order history | not fetched | Needed to tie fills to orders (broker fees). Same scope as the `market_orders` kind. |
| Trading realized P&L | `trade_reconciliation.py`, `realized_trades` | FIFO buy/sell matching for import trading only. Its real-tax linkage (journal `context_id`) is the model to copy. |

The central consequence: **the data needed for real costs exists in ESI, but
the app throws it away after 30 (wallet) or 0 (finished jobs) days.** A job
chain for a capital or T2 product easily spans more than 30 days from buying
the first mineral to selling the product. So the first deliverable must be an
append-only archive; it has to run for a while before the report is useful.
Nothing that ESI has already dropped can be backfilled.

## 3. Core idea: a perpetual inventory with cost layers

Track every type the tenant owns as a stock of **cost layers** (quantity, unit
cost, origin, date, cost components), fed only by real events:

| Event | Time | Inventory effect | Cost source |
|---|---|---|---|
| Market buy (wallet transaction `is_buy`) | transaction date | + layer, origin `bought` | `unit_price x quantity` + buy-order broker fee (section 5b) + freight if bought in The Forge (section 5a) |
| Contract buy (item exchange, we pay) | contract completed | + layers per item, origin `contract` | contract price, split across items (D4) |
| Industry job (manufacturing, reaction, copy, invention) | `start_date` | - inputs consumed (FIFO) | inputs computed from SDE recipe + blueprint ME + structure bonus (section 5) |
| Job cost | `start_date` | adds to the job's output | ESI job `cost` only - it equals the sum of the job's journal entries (Phase 0 V1), which are never added again |
| Job output | `end_date` | + layer of the product, T1 BPC runs or T2 BPC runs, origin `produced` | consumed input cost + job cost, divided by output quantity |
| Cancelled job | cancel date | inputs stay consumed (lost), no output (Phase 0 V7) | input and job cost go to a "cancelled jobs" loss line |
| Market sell of a Production type (any location) | transaction date | - units (D6 order) -> **realized result** | gross, minus real `transaction_tax` (journal, `context_id`), minus the order's broker fee share (section 5b) |
| Contract sell (Special Orders, Doctrine contracts) | contract completed | - items -> realized result | contract price received |

There is no "own use" event: ESI does not report items that are fitted,
given away or lost. They stay in the layers and show up in the inventory
drift check (section 6).

Profit of a sale = net revenue - cost of the units sold. Because a product's
cost layer already contains its full input tree, the realized profit covers
the whole chain. Each produced layer keeps a link to its job, so the report can
drill down: product -> job -> consumed layers -> their jobs or purchases. Each
layer also carries its cost split into components (materials bought,
materials estimated, freight, job costs, invention, fees) so the summary can
show where the money went.

Events are ordered by time, with a fixed tie-break (event kind, then id), so a
recompute always gives the same result.

**The ledger is the source of truth; cost layers are derived.** The archive
tables (section 4) hold raw, append-only ESI facts plus the few values that
must be frozen when first seen. Costing is a pure function that rebuilds all
layers from the ledger. A fix to the costing logic is then a recompute, never
a data migration, and never loses history.

## 4. Data model (new schema file `docs/production_ledger_schema.sql`)

All tables are per-tenant with RLS, `tenant_id` default from `app.tenant_id`,
composite primary keys starting with `tenant_id` (ESI ids are not
tenant-unique - see the corp T1-04 comments in `phase1_schema.sql`).
A new schema file must be added to `deploy/deploy.sh`, `deploy/README.md`, the
root `README.md` and `.cursor/start.sh` (CLAUDE.md, "A brand-new schema file").

Raw archive (append-only, upsert by natural key, never deleted by a sync):

- `ledger_wallet_transactions` - same columns as `esi_wallet_transactions`,
  plus `region_id` (resolved at ingest: NPC station via `sde_stations`,
  structure via the cached `solar_system_id` in `structure_names`/
  `global_structure_names`; unresolved = NULL + warning), `freight_rate_per_m3`
  and `freight_m3` (frozen at ingest for The Forge buys, section 5a) and
  `first_seen_at`.
- `ledger_wallet_journal` - adds `context_id`, `context_id_type`,
  `first_party_id`, `second_party_id`, `description`, `tax`, `tax_receiver_id`
  to today's columns.
- `ledger_market_orders` - order history (`order_id`, type, location,
  `is_buy_order`, price, `volume_total`, `issued`, state), for broker fee
  attribution (section 5b).
- `ledger_industry_jobs` - every ESI job field we need: `job_id`,
  `activity_id`, `blueprint_id`, `blueprint_type_id`, `product_type_id`,
  `runs`, `licensed_runs`, `successful_runs`, `cost`, `facility_id`,
  `station_id`, `output_location_id`, `installer_id`, `start_date`,
  `end_date`, `completed_date`, `status`, `probability`; plus owner columns.
- `ledger_job_inputs` - inputs computed **once, when the job is first seen**
  and then frozen: `job_id`, `type_id`, `quantity`, plus how it was computed
  (`blueprint_me`, `material_multiplier`, `decryptor_type_id`, the installer's
  invention skill levels used, `source` = `esi_blueprint` /
  `archived_blueprint` / `manual_blueprint` / `assumed_me` /
  `probability_decryptor` / `assumed_decryptor`). Freezing matters because
  the blueprint (and its ME) may be gone by the next sync, and config can
  change later.
- `ledger_blueprints` - last seen ME/TE/runs per blueprint `item_id`, from
  every blueprints sync. A BPC whose last run a job uses is gone from the next
  snapshot; its ME is still known from here (Phase 0 V3).
- `ledger_contracts`, `ledger_contract_items` - item-exchange contracts where
  a tenant character/corp is issuer or acceptor. Archived from Phase 1 even
  though costing uses them only in Phase 5, because ESI drops finished
  contracts.
- `ledger_reference_prices` - the reference price per (type, date) used for
  D4/D5/D8, written once when first needed (section 6).
- `ledger_manual_entries` - user-entered costs/revenues, opening-cost
  overrides and corrections. Same "additive, never replaces ESI" rule as
  Manual tracking.

Derived (rebuildable, safe to truncate):

- `prod_cost_layers` - one row per layer: type, unit (`item` or `bpc_run`),
  quantity left, unit cost, cost components, origin (`bought`, `contract`,
  `produced`, `opening`, `unknown`), source ref.
- `prod_realized_results` - one row per sale match: sale ref, type, quantity,
  revenue, cost, profit, coverage share.
- `prod_ledger_runs` - when the last recompute ran, the cutover date T0
  (section 6), ledger high-water marks, warnings.

## 5. Computing consumed inputs

ESI does not report which materials a job consumed. They are computed from the
SDE recipe with the planner's own formula and bonuses - `engine._material_qty`
(per-batch rounding, never below `runs`), `engine._activity_mods`/
`_tech_ii_mods` and `constants.structure_rig_multiplier` - not re-derived:

1. **Blueprint ME**: `blueprint_id` of the job matched to the blueprints
   snapshot (`item_id`) when the job is first seen. A BPC consumed by the job
   may already be gone; then a manual blueprint, then the activity's default
   ME (0 for T2/faction, configured baseline for T1) - flagged `assumed_me`.
   Jobs should be picked up quickly (`industry_jobs` is in the normal tier).
2. **Structure/rig material bonus**: Production's structure config is per job
   *category* (reaction, component, supercapital, manufacturing), each with a
   location in `job_category_locations`. If the job's `facility_id` is that
   category's configured location, use its structure type, rig tier and the
   system's security band; otherwise no bonus, flagged.
3. **Invention**: each attempt consumes the datacores, one decryptor (if
   any) and **one run of the T1 BPC**; all `runs` attempts consume, success or
   not. Output = `successful_runs` T2 BPCs. ESI does not say which decryptor
   was used, but the job's `probability` does (Phase 0 V8): implied
   multiplier = `probability` / (base probability x skill factor), with the
   skill factor from the installer's real skills (`skills` kind: encryption
   skill / 40, the two science skills / 30). It matched a decryptor exactly
   on every live job. `None` and `Symmetry` share multiplier 1.0; for those
   the decryptor selected in Production (`selected_decryptors`) decides, else
   `None`, flagged `assumed_decryptor`. Skills are frozen with the inputs.
   The T2 BPC layer is kept in **runs**; a manufacturing job consumes runs
   from it.
4. **Copying**: job cost only; output is `runs` copies x `licensed_runs`
   runs, kept as a T1 BPC runs layer for invention. Bought BPCs get a runs
   layer the same way.
5. **Reactions**: same as manufacturing (activity 11).
6. **Research (ME/TE)**: job cost only, booked to the BPO's capital line (D3),
   not to products.

## 5a. Freight for items bought in The Forge (D9)

Everything bought in The Forge (region 10000002) still has to be hauled to
the home structure. The user ships it with courier contracts, but the freight
cost used here is **the rate configured in Settings**, not the courier
rewards paid:

- Rate: exactly what the planner uses (`production/pricing.py`
  `_freight_rate`): Production's `haul_cost_per_m3` in the normal single-hub
  mode; in "All hubs" mode the The Forge entry of the shared hub freight table
  (`hubs.hub_freight_per_m3(10000002, fallback=haul_cost_per_m3)`). Planned
  and realized freight therefore agree.
- Volume: `engine._haul_volume` (packaged volume via
  `esi_client.resolve_effective_volume`, SDE volume otherwise) x quantity.
- Freight cost = rate x m3, added to the purchase's layer as its own cost
  component.
- The rate is **frozen per purchase at ingest time**. Settings have no
  history; a later rate change must not rewrite past results. An explicit
  "re-apply current freight rate" action can update it if wanted.
- Purchases at the home structure itself carry no freight. Purchases in other
  regions are not freighted (listed as a warning if they occur).
- Labeled "configured rate" in the report so it is never mistaken for a paid
  courier reward.

## 5b. Broker fees

Order fees come as two journal entries at the same second: `brokers_fee`
(no context id) and `market_provider_tax` (context = structure id). Neither
names the order, the type or the side (Phase 0 V4). Matching a fee's time to
an order's `issued` works for only about a third of them, because `issued`
is overwritten by every price change. A per-fill link is therefore not
possible. Attribution (D12):

- Per character and period, the real order fees (both entry types) are
  summed and spread over that character's *order-based* fills of the same
  period pro rata by fill value, so the total always equals what was really
  paid.
- A fill is order-based when the character has an order for the same type,
  location and side. The price is not compared: the archive keeps only an
  order's last price. Instant buys from sell orders (98 % of the buyer's
  fills) carry no fee.
- A buy fill keeps its fee share in its cost layer, whatever the type: the
  fee is part of what the units cost and reaches Production only if a job
  consumes them (clarified with the user in the Phase 2 review). A sell fill
  of a Production type carries its share into the realized result; sell
  fills of other types are listed as "fees on other trading", not a
  Production cost.
- Fees of a period without any matching fill (unfilled or cancelled orders)
  are a separate "fees without sale" line, not a unit cost.

## 6. Cutover, opening inventory and coverage

**Cutover T0.** ESI windows differ (wallet about 30 days, completed jobs about
90 days, order history 90 days). Costing starts at one date T0 = the start of
the *shortest* complete window when the archive went live. In practice that
is the journal: transactions reach 30 days back, the journal only its newest
10,000 entries. T0 = the oldest archived transaction, moved forward to the
oldest archived journal entry of every wallet whose transactions are older
than its journal (that journal was truncated; a quiet wallet whose journal
starts with its first activity does not move T0). Events before T0 are ignored:
a job that started before T0 consumes nothing in the ledger; its output, if
still in stock, is part of the opening inventory. Without one common T0, jobs
from day -90 would consume materials whose purchase was never archived.

**Opening inventory (D8).** Per type: ESI stock today (assembled ships and
their fitting excluded, same filter as every stock reader - CLAUDE.md
"Assembled ships are not stock") minus net ledger flows since T0 (bought +
produced - consumed - sold). Negative results are set to 0 and listed as a
warning. The result is the *oldest* layer (FIFO consumes it first), origin
`opening`, valued at the reference price on T0. The user can override the
unit cost per type with a real purchase price.
The quantity is computed the first time a rebuild sees a type and then
**frozen** (`prod_opening_frozen`, decided in the Phase 3 review). Re-deriving
it on every rebuild from today's stock would make ledger stock equal ESI
stock by construction, so the drift check below could never fire. Frozen
quantities are re-derived when T0 or the owners shared with Production
change, or on an explicit reset (`rebuild --reset-opening`).

**Unknown origin (D5).** When a consumption or sale finds no layer (or not
enough), it draws from an `unknown` layer valued at the reference price on the
day of use. There is no separate "own" origin (mined/refined/PI items produce
no ESI event; decision D5): they are `opening` or `unknown` like any other
item without a purchase.

**Valuation (revised 2026-10-17 with the user).** Stock without a purchase
(opening, unknown) and the D4 split weights use what the planner would
actually pay for one unit: the cheaper of the buy-list purchase price
(`pricing.buy_price`: home market, or hub plus freight, both with the buy
broker fee) and the planner's build cost (`engine._unit_cost`, via
`engine.planner_unit_values`). Example: Tritanium is cheaper at C-J, so it is
valued at the C-J price, not at The Forge average. The value is frozen per
(type, day) in `ledger_valuation_prices` the first time a rebuild needs it;
the home structure's market has no history, so a day already past at that
point gets that rebuild's price. Where the planner has no value (no sell
order anywhere and not buildable) the reference price below is the
fallback, labeled "Forge average".

**Reference price** (fallback for the valuation above): The Forge daily average from
the app's existing daily-history read (`price_history`: Goonmetrics region
history for The Forge, ESI daily history for types Goonmetrics lacks), cached
per (type, date) in `ledger_reference_prices` so a
recompute is deterministic. This is a new row in CLAUDE.md's "Price sources
matrix" (question: "what was an item without a purchase worth on that day?").

**Coverage.** Every result row stores which share of its cost came from real
figures (purchases, job costs, real fees) versus estimates (opening, unknown,
modeled broker fees, assumed ME/decryptor). The report shows a **coverage %**
per product and in total; below a threshold a figure is labeled "estimated".
Freight is shown separately as "configured rate" and does not lower coverage.

**Inventory drift check.** Compare ledger stock (sum of layers) per type with
the ESI asset snapshot. A large gap means missing events (losses, gifts,
fitted ships, transfers, untracked characters, wrong ME/bonus assumptions) and
is shown as a warning, never corrected silently.

## 7. Separation from Trading and other tools

The same characters can buy for Trading and for Production, and Trading has
its own FIFO in `trade_reconciliation.py` (D6):

- **Production type**: a type that is the product of any job in the ledger,
  or is in Production's stock targets or an integrated Special Order. Only
  sales of Production types are realized here - at any location, since
  Production sells only at C-J by design but the user may still sell elsewhere
  (a sale outside the home structure is listed as a warning, no return
  freight).
- One tenant-wide inventory per type (D2). Production costing *consumes*
  layers only through job inputs; bought-only types that are not Production
  types stay Trading's business.
- A sale of a Production type takes layers in this order: `produced` first,
  then `opening`, then the rest in FIFO order. Trading's `realized_trades` is
  not changed. A type that is both a Production type and imported by Trading
  is listed as an overlap warning (its sales may appear in both reports).

**Corp data (D7):** corp data is shared across tenants (corp 98370861 is in 5
tenants) and corp wallet entries do not say which member acted.

- Corp jobs count when `installer_id` is one of the tenant's characters. Their
  cost comes from the job's `cost` field; a journal entry for the same job in
  a corp wallet is not added again.
- Corp wallets are used only for corps (optionally per wallet division) the
  user marks as "mine alone" - a new per-tenant config field, default off.
  Without it, inputs bought from a corp wallet have `unknown` origin.
- Known gap: a corp job of the tenant's character may use corp-hangar
  materials another member bought; that shows up as drift, not as cost.

## 8. Architecture (repo conventions)

- Package `eve_trader/production/realized/`: `ingest.py` (ESI rows -> ledger),
  `inputs.py` (section 5), `costing.py` (pure: ledger -> layers + results, no
  I/O), `report.py` (aggregation for the API).
- `do_*` actions in `production/actions.py`: `do_rebuild_realized`,
  `do_get_realized_summary`, `do_get_realized_product`, `do_add_ledger_entry`,
  `do_set_opening_cost`, `do_reapply_freight_rate`. Router
  `/api/production/realized/...`, thin. CLI
  `eve-trader production realized rebuild|summary`.
- ESI: archive writes happen inside the existing fetchers
  (`esi_data/fetchers.py`), right after the snapshot write, in the same sync.
  No new scheduler job. `"production"` is added to `consuming_tools` of
  `wallet` and `contracts` in `esi_data/registry.py`; order history is
  fetched with the `market_orders` kind (already consumed by Production).
  Industry jobs: a second ESI call with `include_completed=true` for the
  archive only - the snapshot keeps `include_completed=false`, so `jobs.py`
  and the planner (which filter on `active/paused/ready`) are not affected.
- **Access (D11):** every read of the archive checks `is_shared(owner, kind,
  "production")` at read time, fail-closed, like `read_esi`. Revoking a share
  hides that owner's rows from costing and the report; they are kept and come
  back if the share is granted again. Deleting them is an explicit user action.
- Journal `context_id` is also added to `esi_wallet_journal`; this unblocks the
  deferred follow-up in `trade_reconciliation._journal_from_snapshot` (real
  sales tax from snapshots) as a side effect.
- **Retention (D10):** tenants inactive for `inactive_tenant_days` (14) get no
  scheduled syncs, so ESI history would be lost from about day 44 without a
  login. The `wallet`, `industry_jobs`, `market_orders` and `contracts` kinds
  keep syncing for inactive tenants that hold the `production` grant (an
  exception in `tenant_eligibility`). The report also shows "archive gaps"
  (periods with no successful sync of a kind).
- Recompute: on demand (button / CLI) and after each wallet/jobs sync for the
  tenant, cached with explicit invalidation (CLAUDE.md caching pattern 3).
  It is a full rebuild; once the ledger is large, make it incremental from the
  last unchanged event.

## 9. Phases

**Phase 0 - live verification - done 2026-10-17, results in section 13.**
The checks were:
- V1: does job `cost` already include SCC surcharge and facility tax, or is
  `industry_job_tax` in the journal an additional amount? (Avoid counting it
  twice.)
- V2: journal `context_id_type` for `industry_job_tax`, `brokers_fee`,
  `contract_price` - which id do they carry?
- V3: how long ESI keeps completed jobs; is `successful_runs` filled for
  invention; does `blueprint_id` equal the blueprints snapshot `item_id`?
- V4: can buy-order fills be matched to `orders/history` reliably (price,
  type, location, time)?
- V5: contract items endpoint for finished contracts (retention).
- V6: does the character jobs endpoint also list corp jobs that character
  installed, or only the corp endpoint (Director role)?
- V7: what happens to materials of a cancelled manufacturing/reaction job?
- V8: can the decryptor be derived from the delivered T2 BPCs (runs/ME) at the
  output location?

**Phase 1 - archive (start as early as possible).** Ledger tables, ingest in
fetchers (wallet, journal with `context_id`, order history, completed jobs,
contracts), frozen job inputs and freight, retention exception, read gate.
No UI. Tests: upsert idempotence, no deletion on resync, RLS, share revoke.
Every week this runs earlier is a week of real history the report can use
later.

**Phase 2 - costing engine.** Pure `costing.py`: T0 cutover, opening layer,
FIFO layers with cost components, job consumption, invention (BPC runs,
decryptor), reaction chains, broker fee attribution, unknown-origin fallback,
coverage. Golden tests on hand-built chains (minerals -> reaction -> T2
component -> invention -> T2 module -> sale), including partial sales, a job
with an unknown-ME BPC, a job before T0, and a negative opening quantity.
Heavy enough to delegate to an Opus subagent (CLAUDE.md preference).

**Phase 3 - report and API.** Realized P&L per period, per product, per job;
cost components (incl. freight); drill-down tree; coverage; "fees without
sale"; drift, overlap and archive-gap warnings; CLI.

**Phase 4 - frontend.** New Production page "Realized Profit" (section 9a).

### 9a. Frontend: Production -> "Realized Profit"

One new tab in `ProductionLayout` (route `/production/realized`, lazy-loaded
like every other Production page), placed after "Margin" so planned and
realized margin sit next to each other. Built from existing components only:
`DataTable` (sorting, filters, saved views, CSV export), `RowDetailDrawer`,
`HintCard`, `FieldState`, `EditableCell`, recharts (as on Trading's Realized
Trades page), `isk`/`pct`/`qty` formatters. Sub-tabs inside the page:

1. **Overview**
   - Header: period selector (7 / 30 / 90 days, since T0, custom range;
     default 30 days), status line (archive since, T0, last recompute, last
     wallet/jobs sync), "Recompute" button (`useAction`, background job like
     other long actions).
   - KPI cards: revenue, cost of goods sold, realized profit, margin %,
     coverage % (color: green >= 90 %, yellow >= 70 %, red below; the profit
     card says "estimated" below the threshold), units sold.
   - Separate cards, *not* part of profit: "fees without sale" (unfilled/
     relist broker fees), "capital in BPOs" (D3), "inventory at cost"
     (unsold produced and opening stock).
   - Chart: realized profit per day/week (bars), cumulative line.
   - Cost structure: one stacked bar plus table - materials bought, materials
     estimated (opening/unknown), freight (labeled "configured rate"), job
     costs, invention, broker fees, sales tax.
2. **Products** - one row per Production type sold in the period: units sold,
   revenue, cost/unit, net sell/unit, profit, margin, coverage badge, warning
   icon. Phase 5 adds "planned cost/unit" and the difference. Click a row ->
   drawer with:
   - the product's sales in the period (date, location, qty, net price,
     profit),
   - the **cost chain** as an expandable tree (same shape as Material Tree's
     `TreeRow`): product -> job (#id, date, runs, job cost, facility) ->
     inputs (qty, unit cost, origin badge). A `bought` input shows date,
     location, price, freight; a `produced` input expands into its own job;
     `opening`/`unknown` inputs are shown in the estimate color with the
     reference price used; assumed ME/decryptor carry a small marker.
3. **Jobs** - every job since T0: product, activity, runs, start/end,
   input cost, job cost, unit cost of the output, share already sold, flags
   (assumed ME/decryptor, no structure bonus, before T0). Invention jobs show
   attempts, successes and cost per T2 BPC run.
4. **Inventory** - remaining layers per type: quantity, cost basis per unit,
   total, origin mix (bought/produced/opening/unknown), ESI stock and the drift
   between them.
5. **Warnings** - grouped `HintCard`s: inventory drift, Trading overlap,
   archive gaps, sales outside the home structure, unresolved regions,
   unmatched broker fees, assumed ME/decryptor, negative opening quantities.
   Each links to the affected rows.
6. **Ledger** - manual entries and overrides:
   - opening inventory table (type, qty, reference price on T0, editable
     real unit cost via `EditableCell`),
   - manual ledger entries (cost or revenue, type, qty, amount, date, note),
   - corp "mine alone" switches per corp/division (D7),
   - "re-apply current freight rate" (confirm dialog showing how many
     purchases change),
   - per-character archive status; a character whose wallet/jobs are not
     shared with Production shows `FieldState`'s "not shared" with a link to
     the Characters page; hidden archive rows (D11) can be deleted here
     (count-then-confirm, like turning off the mail archive).

Early state: until the archive has data past T0 the page shows one hint
("archive running since X; results start at T0") instead of empty tables.
UI text is English like the rest of the app. Live verification per
CLAUDE.md with a throwaway Playwright script before calling it done.

**Phase 5 - extensions.** Contract buys/sells in costing (data archived since
Phase 1); optional comparison of the configured freight rate with courier
rewards actually paid; planned vs actual per product (engine estimate at job
start vs realized); Portfolio integration (realized Production profit next to
Trading's).

## 10. Decisions (confirmed with the user 2026-10-17)

- **D1 cost method: FIFO layers**, not moving average. Matches Trading, keeps
  the drill-down to individual jobs/purchases, deterministic.
- **D2 one inventory per type for the whole tenant**, ignoring location.
  Moves between stations are invisible in ESI; per-location layers would
  mismatch constantly. Freight is added at purchase time instead (D9).
- **D3 BPOs are not part of unit cost.** Shown as a separate "capital in
  BPOs" line (purchase price plus research job costs), no amortization; a BPO
  sale shows its own gain/loss. Copy and invention job costs are product
  costs.
- **D4 contract price split across items by reference market value** at the
  contract date. The quote is only a weight; the split always sums to the real
  price paid. A single-type contract needs no split. Bought BPCs get their
  share as a runs layer.
- **D5 inputs without a purchase: one profit figure.** Opening and unknown
  inputs are valued at the planner valuation (section 6; revised
  2026-10-17, The Forge average is only the fallback), flagged, with a
  coverage %. No separate "own" origin for mined/refined/PI items (not
  detectable from ESI; the user chose no split), and therefore no second
  "cash cost" profit figure - with no own origin and estimates at market price
  in both views, the two figures would always be equal. (Revised in review;
  the earlier "two views" version is superseded.)
- **D6 Trading overlap: produced layers first, Trading untouched** (section 7,
  with the "Production type" definition).
- **D7 corp data:** corp jobs by `installer_id`; corp wallets only for corps
  or divisions marked "mine alone" (default off).
- **D8 opening inventory: automatic, manually overridable**, at one cutover
  T0, oldest layer, planner valuation on T0 (section 6).
- **D9 freight:** every unit bought in The Forge gets freight to the home
  structure at the rate the planner uses (`haul_cost_per_m3`, or the hub table
  entry in "All hubs" mode) x packaged m3, frozen per purchase at ingest.
  Courier rewards actually paid are not used as the cost (section 5a).
- **D10 retention:** archive-relevant kinds keep syncing for inactive tenants
  holding `production` (section 8). Confirmed with the user 2026-10-17.
- **D11 revoked share:** archive rows are hidden, not deleted; deletion only
  by explicit user action.
- **D12 broker fees:** real order fees per character and period, spread pro
  rata by value over that period's order-based fills (section 5b).
  Confirmed with the user 2026-10-17.

## 11. Known limitations (by design)

- Consumed inputs are computed, not observed (ESI has no such data). A wrong
  ME, structure bonus or decryptor assumption shows up as inventory drift, not
  as a wrong-looking number.
- Losses (destroyed ships/cargo), fitted ships, gifts, and transfers between
  characters of different tenants are invisible unless entered manually.
- Mined, refined and PI materials have no cost of their own; they are valued
  at market price like any item without a purchase.
- History before Phase 1 goes live is limited to what ESI still holds at that
  moment; everything before T0 is opening inventory.
- Corp jobs in a corp shared with other tenants may use materials another
  member paid for.

## 12. Review log (2026-10-17)

Corrections after checking the first draft against the code:

- Freight lookup: the planner uses `haul_cost_per_m3` in single-hub mode and
  the hub table only in "All hubs" mode (`pricing._freight_rate`); the draft
  had it reversed. Volume goes through `engine._haul_volume`.
- Decryptor is not in ESI's job data; invention consumes one T1 BPC run per
  attempt - both were missing.
- "Own" origin is not detectable; D5's two profit views collapsed into one.
- Opening inventory needed one common cutover T0 (wallet and job windows
  differ), and negative quantities handled.
- "Own use" event removed: ESI has no such event.
- Reference prices must be cached per day for deterministic recomputes.
- Broker fees need order history (no `order_id` on transactions); unfilled
  order fees are a period line, not unit cost.
- Sales of produced types outside the home structure count; "Production
  type" defined so opening finished goods are not handed to Trading.
- Structure bonus is per job category via `job_category_locations`, not "home
  structure or not". `order_integrity.py` (wrongly cited as recipe logic) is
  the Special Orders integrity scan.
- Contracts are archived from Phase 1; ESI drops finished contracts.
- Inactivity data loss starts around day 44, not 30; retention exception
  added (D10, confirmed).
- Revoked shares: read gate at read time, rows hidden not deleted (D11).
- Added Phase 0 checks V6 (corp jobs on the character endpoint), V7
  (cancelled jobs), V8 (decryptor derivation).

## 13. Phase 0 findings (2026-10-17, test server, Default tenant, read-only)

Checked with throwaway scripts against live ESI using the stored tokens of the
Default tenant's characters (seller/producer 1560510246, buyer 92267134,
doctrine 624503518). Only aggregates were printed; nothing was written.

- **V1 job cost - confirmed.** For all 349 jobs inside the journal window,
  `industry_job_tax` plus the activity fee entry (`manufacturing`,
  `reaction`, `researching_technology` for invention) sum to exactly the
  job's `cost` (ratio 1.0 on 349/349). `cost` is the full amount; the journal
  entries are its breakdown and must not be added.
- **V2 journal context types.** `industry_job_tax`/`manufacturing`/
  `reaction`/`researching_technology`: `industry_job_id`.
  `market_transaction`, `market_escrow`: `market_transaction_id`.
  `transaction_tax`: none (linked by journal id + 1, as in
  `trade_reconciliation`). `brokers_fee`: none. `market_provider_tax`:
  `structure_id`. `contract_brokers_fee`, `contract_reward_deposited`:
  `contract_id`.
- **V3 jobs.** `include_completed=true` returns `cost`, `blueprint_id`,
  `facility_id`, `licensed_runs`, `completed_date`, `completed_character_id`
  and `successful_runs` (on every delivered invention job). Oldest completed
  job seen: 22 days (the character's history starts there; CCP documents 90
  days - not contradicted). `blueprint_id` is in the blueprints snapshot for
  all active manufacturing and copy jobs (12/12); invention consumes its T1
  BPC at start (0/11 in snapshot) - irrelevant, ME does not matter for
  invention. A BPC used up by a manufacturing job disappears, hence
  `ledger_blueprints`.
- **V4 fees - refuted as planned.** `brokers_fee` has no context; a time
  match to an order's `issued` works for about a third (308 of 922 and 222 of
  715); the rest are price changes (`issued` keeps only the last one). Every
  order fee is a `brokers_fee` + `market_provider_tax` pair at the same
  second. Buyer: 2450 of 2500 buy fills have no own buy order (instant buys,
  no fee). Seller: 1041 of 2447 sell fills match no own order by price
  (orders whose price was changed). Redesigned in section 5b (D12).
- **V5 contracts - confirmed.** Items of finished item-exchange contracts are
  readable (4/4, up to 1914 items). The contract list covers about 30 days.
- **V6 corp jobs - important gap.** The character endpoint does **not** list
  corp jobs (0 of 185 stored corp jobs appear in their installers' character
  endpoints). The corp endpoint returns 403 "Character does not have
  required role(s)" for **every one of the 18 stored tokens**: no character
  currently has the Director role. The stored corp jobs (last sync
  2026-10-12) are stale, and corp-installed jobs stay invisible - for the
  planner too - until a Director re-authorizes with the corp jobs scope.
  Without that, D7's corp-job part delivers nothing.
- **V7 cancelled jobs.** The one cancelled job kept its full fees in the
  journal (not refunded). ESI cannot show materials; player sources (older
  forum posts) say materials of a cancelled job are lost and a BPO returns.
  Modeled that way (section 3).
- **V8 decryptor - T2 BPC route refuted, probability route confirmed.** No
  T2 BPCs were found at the invention output locations (already used or
  moved). But `probability` / (base probability x skill factor from the
  character's real skills) matched a decryptor multiplier exactly on 352/352
  invention jobs (Process 107, Attainment 76, Parity 76, None-or-Symmetry 64,
  Optimized Attainment 21, Accelerant 8). Section 5 now uses this.
- **New: journal depth.** ESI exposes only 10 journal pages (10,000
  entries). For the C-J seller that is about 7.5 days, not 30; the first
  transaction page (2500) covers about 6 days (older pages via `from_id`).
  The archive must sync each character at least every few days. The `wallet`
  kind's frequent tier (1 h) is enough while the scheduler runs, and the D10
  retention exception is required, not optional. Gap detection compares each
  sync's oldest returned entry with the newest archived one.

### 13a. Recheck after re-authorizing the Default tenant's characters (2026-10-17)

The user re-authorized the characters; all checks were run again on the test
server (same scripts, read-only, scripts deleted afterwards). V1-V5, V7 and
V8 gave the same results. New or changed:

- **V6 corp jobs - now readable for corp 98723738.** 15 of 18 tokens read
  its corp jobs. With `include_completed=true`: 2000 jobs (2 pages of 1000;
  355 active, 1645 completed back to 2026-09-23), `cost` and `probability`
  on all, `successful_runs` on all finished ones. Still none of them appears
  in the installers' character endpoints (0 of 350 checked), so corp jobs
  come only from the corp endpoint. Corp 98370861 (the C-J seller
  1560510246's corp) is still 403 for every token - jobs installed there
  stay invisible until a Director of that corp authorizes.
- **Corp job history depth.** 2000 rows, exactly 2 full pages (page 3 is
  404), sorted newest first, oldest start 2026-09-23. The corp was founded in
  2022 and its installers joined between 2022 and 2025, so "the corp is only
  24 days old" does not explain the edge. The ESI spec documents no limit.
  **Decision (user, 2026-10-17): treat 2000 rows as a hard cap** of the
  corp list (`include_completed=true`); not proven by a before/after check.
  At about 70 corp jobs a day the list covers roughly 24 days. Consequences
  for Phase 1:
  - the archive must sync corp `industry_jobs` well inside that window; the
    normal tier (6 h) plus the D10 retention exception is enough;
  - gap detection for jobs: if the oldest job of a sync is newer than the
    newest archived job of that owner, completed jobs in between were lost -
    report an archive gap for that period (same rule as the journal);
  - the same cap is assumed for the character endpoint (392 rows today) and
    handled by the same gap check.
- **Corp wallets not readable.** Only two tokens carry the corp wallet scope
  (`buyer:92267134`, `producer:1560510246`) and both get 403 (no Accountant
  role) on both corps. D7's "mine alone" corp-wallet option therefore has no
  data today; corp job costs are unaffected (they come from the job's
  `cost`).
- **Transactions depth.** Paging with `from_id` returns the full 30 days
  (12,009 transactions for the C-J seller).
- **Journal cap - proven.** The character journal returns only the newest
  10,000 entries (X-Pages 10, page 11 is 404), although the ESI spec says
  "going 30 days back". Proof: 8,168 of the C-J seller's sales between
  2026-09-17 and 2026-10-09 are in the transactions (inside the 30 days) and
  none of them has a `market_transaction` journal entry, while 3,674 of
  3,675 newer sales do. For that character the journal reaches back about
  7.5 days.
- **Bug found and fixed (commit 140c5bb).** The C-J seller's `wallet` sync
  had failed on every run since 2026-10-05 with "duplicate key ...
  esi_wallet_transactions_pkey": ESI returned a page of 2500 rows with only
  2499 distinct transaction ids, and `_page_wallet_transactions` only
  deduplicated against earlier pages. Now deduplicated per entry (also in
  `trade_reconciliation`'s two live paging loops), with a regression test.
  Without this fix the archive would receive nothing from the most important
  character.

### 13b. Implementability check against the code

Everything the plan relies on exists or is a small, local extension. Points
Phase 1 must handle explicitly (not new decisions, just work the plan did not
spell out):

- **Contracts:** `fetch_character_contracts`/`fetch_corporation_contracts`
  are Doctrine-specific (filtered to Doctrine's structure and statuses). The
  archive needs its own pass over the same ESI list in the same fetcher: all
  item-exchange contracts where a tenant character/corp is issuer or
  acceptor, items fetched once per finished contract.
- **Order history:** the `market_orders` fetcher reads only open orders
  (`client.character_orders`). Add `/characters/{id}/orders/history/` (and
  the corp equivalent) to the same fetcher for `ledger_market_orders`; same
  scope.
- **Per-skill rows for Production:** `esi_data/access.py` routes per-skill
  rows (`_read_character_skills`) only to `char_skills`, `char_skill_plans`
  and `pi`; Production gets only slot counts. Add `production` to that
  branch (or a filter) so the decryptor derivation can read the installer's
  encryption and science skill levels.
- **T2 BPC ME:** a BPC created and used up between two blueprint syncs (rare
  tier, 24 h) never appears in `ledger_blueprints`. For invented BPCs the ME
  follows from the derived decryptor (2 + decryptor ME bonus), so only
  bought BPCs fall back to `assumed_me`.
- **Corp job attribution:** `installer_id` is present on every corp job;
  matching it against the tenant's registered characters is enough.
- Retention exception (`tenant_eligibility`), `engine._haul_volume`,
  `pricing._freight_rate`, `job_category_locations`, `selected_decryptors`,
  Goonmetrics/ESI daily history for reference prices: all exist as the plan
  assumes.

## Implementation status

- **Phase 0 - done 2026-10-17.** Live ESI checks V1-V8 on the test server
  (Default tenant); results in sections 13 / 13a / 13b. Settled hard caps
  (2000 jobs, 10,000 journal), D12 fee spread, probability-derived
  decryptor, and the Phase 1 implementability list.
- **Phase 1 - done 2026-10-17.** Append-only ledger schema
  (`docs/production_ledger_schema.sql`) with RLS; ingest hooked into
  existing ESI fetchers (wallet+journal context, completed jobs second
  pass, order history, blueprints, contracts archive pass); frozen job
  inputs / freight / region at first sight; D10 inactive-tenant retention
  for archive kinds; D11 read gate via `is_shared`; Production per-skill
  rows via `read_esi(..., table="skills")`. Deviation: fetchers
  lazy-import `production.realized.ingest` (plan places archive writes in
  fetchers; one deliberate esi_data→tool exception, now documented in
  CLAUDE.md). Snapshot behaviour unchanged (`include_completed=false`).
  Review fixes (same day, checked against live data on the test server):
  - wallet archive enriches only rows new to the archive, with per-call
    region/volume caches (was 83 s of lookups per hourly sync of the C-J
    seller); unresolved regions are retried and then frozen;
  - decryptor derivation needs the installer's real skills and an exact
    match (0.005); no guessed skill factor, no loose band (72 of 352 live
    jobs were mislabeled before); `selected_decryptors` is looked up by the
    T2 item, not the T2 blueprint;
  - gap detection only where ESI can have dropped rows: previous archive
    sync older than the list's window (new `ledger_sync_state`), or a
    capped response (journal 10,000 per division, jobs 2000) whose oldest
    row is newer than the archive. Quiet periods are no longer gaps;
  - Tech II ME fallback = selected decryptor's ME, else 2 (was 0); Tech I
    fallback from `ACTIVITY_MODS`;
  - archive writes only for kinds shared with Production; D10 syncs only
    Production's own sharing rows; contract items fetched once per
    contract; jobs whose inputs could not be computed are retried.
- **Phase 2 - done 2026-10-17.** Pure `eve_trader/production/realized/costing.py`
  (no I/O) rebuilds FIFO layers and realized results from a `LedgerSnapshot`:
  T0 cutover, D8 opening inventory, job consume/produce (manufacturing,
  reaction, copy, invention with BPC-run layers, research → capital_in_bpos),
  cancelled-job losses, D5 unknown-origin at reference price, D6 sale order
  (produced → opening → rest), D7 corp jobs by installer, D12 broker-fee
  spread by UTC day, freight as its own component (does not lower coverage),
  coverage with `ESTIMATED_COVERAGE_THRESHOLD = 0.70`. Thin
  `loader.py` reads via `access.py` (D11), fills/caches
  `ledger_reference_prices`, and replaces `prod_cost_layers` /
  `prod_realized_results` / `prod_ledger_runs` on each rebuild. Bulk archive
  reads are one query per table for the shared-owner set. Golden tests:
  `tests/test_production_realized_costing.py` (27); loader/DB:
  `tests/test_production_realized_loader.py` (9).
  Settled interpretations:
  - Failed invention (`successful_runs == 0`) → `failed_invention_losses`
    (plan unnamed this case).
  - Missing sales-tax journal → revenue is gross + `tax_missing` warning
    (never a modeled tax rate); T0 (section 6) keeps this to the rare case.
  - No new schema file: derived tables were created in Phase 1's
    `production_ledger_schema.sql`.
  Review fixes (same day, checked against live data on the test server):
  - invention output = successes x (recipe base runs + decryptor run
    bonus); ESI's `licensed_runs` on an invention job is the input BPC's
    runs (live: 300 vs 20 per success) and is never used;
  - BPC runs are drawn only by jobs that ran on a BPC (`uses_bpc` from the
    archived blueprint, runs -1 = BPO; T2/T3 blueprints are always copies),
    so a BPO build no longer eats the copies made for invention;
  - a value without any price is booked at 0 but flags the layer, every
    layer built from it and every sale drawing from it (`price_missing`),
    and such a sale is always "estimated";
  - reference prices in two passes (costing reports the misses, the
    loader fetches exactly those): Goonmetrics (~28 days), then ESI daily
    history (~400 days), then the nearest traded day within 7 days; old
    misses are cached so a rebuild never asks again;
  - T0 per section 6 (journal start of a truncated wallet), so sales are
    not booked gross for weeks;
  - opening stock = assets shared with Production + quantities in open sell
    orders and outstanding contracts;
  - buy fills keep their fee share (5b); fills match orders without the
    price; blueprints bought on the market are BPOs (`capital_in_bpos`,
    D3);
  - the loader's SDE blueprint-category query joined a column that does
    not exist (`sde_types.category_id`); now via `sde_groups`.
- **Phase 3 - done 2026-10-17.** `report.py` aggregates the derived
  tables (no second costing pass). `do_*` in `production/actions.py`:
  rebuild, summary, products, product, sale chain, jobs, inventory,
  warnings, manual cost/revenue entry, opening-cost override, freight
  reapply. HTTP under `/api/production/realized/` (production grant,
  Pydantic models keep the flags). HTTP rebuild is
  `pipeline_runner` job `rebuild_realized`; the CLI calls
  `do_rebuild_realized` in-process
  (`eve-trader production realized rebuild|summary|entry|opening-cost|reapply-freight`,
  each inside `tenant_scope.enter_tenant`). Drill-down is stored at
  rebuild time in the same schema file. Tests:
  `tests/test_production_realized_report.py` (SQL against Postgres) and
  router tests in `tests/test_api_routers.py`.
  Deviations:
  - Section 8 names summary and product. Products list, sale chain,
    jobs, inventory and warnings are extra reads because section 9a
    needs them.
  - Manual cost/revenue rows adjust the period
    (`profit_including_manual`). They are not FIFO events. Costing
    still consumes only `opening_cost` overrides. `amount` is the
    entry's total ISK; `quantity` is stored and not multiplied in.
  - `do_reapply_freight_rate` writes the current configured rate onto
    rows that already have `freight_m3` and does not rebuild in the
    same call (`rebuild_required`).
  - Fees without a sale and fees on other trading live in
    `prod_fee_lines`, not as warning strings. A UTC day is included
    when it overlaps the period (D12 is per day).
  - Capital in BPOs and inventory at cost are balances as of the
    latest rebuild, not flows inside the period. Inventory at cost is
    remaining produced and opening item layers. Bought leftovers still
    appear on the inventory list.
  - Sales tax is a cost-structure line and is already removed from
    revenue, so it is not added to COGS. A missing journal stays null
    (`tax_missing`); it is never coerced to zero.
  - Any non-zero ESI-vs-ledger quantity is an inventory-drift warning.
    Unresolved regions are grouped by location with a count.
  - `structure_bonus_applied` is frozen on newly written job inputs.
    Older rows stay NULL and the report shows `no_structure_bonus`
    null rather than recomputing from the current settings. Jobs
    before T0 stay warnings, not job rows.
  - Costing copies `facility_id` onto jobs and records each
    non-production fee share as `OtherTradingFee` (the existing
    `fees_on_other_trading` total is unchanged).
  - Section 8's recompute-after-every-wallet/jobs-sync is not hooked
    up. A full rebuild inside the sync would hold that sync; the plan's
    own later step is an incremental rebuild. Recompute is on demand.
  - `prod_cost_layers` stays remaining-only. Consumed layers live on
    as `prod_cost_draws`. The new derived tables are listed in
    `sqlite_migration.KNOWN_NON_MIGRATED_TABLES` (rebuildable; they
    never existed in the pre-migration SQLite schema).
  Review fixes (2026-10-17):
  - D11 for derived rows: a run stores `owner_scope_hash` (the owners
    shared with Production per kind). Every report read compares it and
    returns nothing from that run when sharing changed
    (`status.sharing_changed`, `needs_rebuild` on every list) until the
    next rebuild. The sale chain is then an ActionError.
  - `status.stale`: an archive sync of a shared owner is newer than the
    last recompute. Recompute stays on demand; Phase 4 can recompute when
    the page opens and the status is stale.
  - Sale chain: a job's inputs are scaled to the share of the job's
    output the sale consumed (`share`, `cost_share` on job nodes), so
    the tree adds up to the sale's cost. `reference_price` is shown only
    for estimated layers, not for an opening-cost override.
  - Opening quantities are frozen (section 6). Drift is now real; it
    stopped being a copy of `negative_opening`.
  - Ledger tab API (section 9a.6): `GET /realized/opening` (frozen
    quantity, reference price on T0, unit cost used, override),
    `GET /realized/entries`, `DELETE /realized/entries/{id}`,
    `GET /realized/archive-status` (shared kinds, last sync, gaps,
    hidden kinds per owner), `POST /realized/freight/reapply?dry_run=true`
    (count for the confirm dialog; the update touches only rows whose
    rate differs), `POST /realized/rebuild?reset_opening=true`.
  - Still open for Phase 4/5: deleting hidden archive rows
    (count-then-confirm) and D7's corp "mine alone" switch (corp wallets
    are not readable today anyway).
- **Phase 4 - done 2026-10-17.** Production page "Realized Profit"
  (`/production/realized`, tab after Margin, QuickNav
  `production-realized`). Six sub-tabs (Overview, Products, Jobs,
  Inventory, Warnings, Ledger), remembered per viewer in
  `localStorage`. Period 7/30/90, since T0, or a custom range (default
  30 days) maps onto the summary/products query. Recompute is
  `useBackgroundJob` on `POST /realized/rebuild` (409 stays a warning).
  One auto-recompute per page open when the report is stale, or when a
  rebuild is required and an archive exists. KPI cards, the separate
  "not part of realized profit" balances, manual-entry secondary line,
  daily (or weekly, past 90 days) profit chart, and the API cost-structure
  labels. Products open a drawer with the sale list and cost chain.
  Jobs, inventory, warning groups, opening overrides, manual entries,
  freight reapply (dry run, then confirm), and archive status are the
  other tabs. Coverage colour, "estimated", and profit figures come from
  the API. `needs_rebuild` replaces an empty table with the recompute
  hint. Tests in `frontend/src/pages/production/realized/`.
  Deviations:
  - `RowDetailDrawer` takes an optional `size` (default `md`; existing
    callers unchanged) so the product chain can use `xl`.
  - Weekly bars group the API's daily `by_day` profits into Monday-UTC
    weeks for display when the selected period is longer than 90 days.
    The frontend does not compute a profit figure.
  - `no_structure_bonus` null is "unknown" on job rows and job chain
    nodes. On other rows a null is omitted, because that is the API
    default when the flag does not apply. `true` is "no", `false` is
    "applied".
  - Clearing an opening override deletes that `opening_cost` ledger row
    (`DELETE /realized/entries/{id}`) after a confirm. There is no
    dedicated clear endpoint, and `unit_cost` cannot be null.
  - A custom range is sent only after both dates are applied, so an
    incomplete range is not requested. The end date is inclusive: the
    API's `end` is exclusive, so the page sends the following day.
  - Archive owner names come from `/api/characters/owners`, read only
    when the session holds the `characters` grant; otherwise the numeric
    owner id is shown.
  - Auto-recompute is once per page visit. A module flag survives
    React StrictMode's remount in dev and clears shortly after a real
    unmount, so one open does not start two rebuilds.
  - `POST /realized/rebuild?reset_opening=true` is not part of the
    section 9a page, so the UI does not send it.
  - Deleting hidden archive rows and the D7 corp "mine alone" switch
    stay out of this phase, with no placeholders.
- **Phase 5 - done 2026-10-17.** One PR. What was built:
  - Finished item-exchange contracts (`status = 'finished'` and
    `date_completed` set) enter the pure costing snapshot. The tenant as
    acceptor books issuer-provided items as `bought` layers with
    `source = contract`, price split by reference value (D4) so the shares
    sum exactly. The tenant as issuer books Production-type items it gives
    as sales, same FIFO order as market sales, no sales tax and no broker
    fee. Internal transfers (both sides are tenant owners) are skipped.
    Both directions warn `contract_item_swap`. Forge freight is frozen on
    the contract item. Sale chain, products drawer and jobs show a
    contract badge via `source` / `contract_input`.
  - `prod_ledger_runs.costing_version` (2). A latest run whose version is
    NULL or lower forces one opening reset; the following rebuild freezes
    the opening again.
  - `ledger_job_planned_costs` freezes a cached planner cost/unit the first
    time a job is archived (no network). Products show planned cost/unit,
    the difference, and `planned_coverage`.
  - Finished courier contracts the tenant issued are archived (`volume`,
    `collateral`, `region_id`) and stay out of costing.
    `GET /api/production/realized/courier` is Forge-to-home, per month.
  - Portfolio shows last-30-day realized Production profit from the existing
    summary action when the session's tools include `production`. Not added
    to the daily snapshot.
  - Ledger tab: count then confirm-delete of hidden archive rows. Refused
    while the kind is shared with Production.
  - Deviations:
    - Channel is `source` (`market` / `contract`). Origin stays `bought`.
      Section 3's origin `contract` is not used.
    - Only the exact status `finished` counts. Broader `finished_*` values
      are ignored.
    - Issuer-received items (the issuer asks for them) are costed at the
      contract `reward`. Acceptor-given items (a buy contract the tenant
      fills, e.g. a buyback) are sales at the `reward`, split by reference
      value like any contract sale (review decision with the user).
    - A contract BPO's capital line includes frozen freight. A market BPO
      buy does not, because that path returns before freight.
    - Planned cost is never a Jita quote. Misses store NULL with source
      `unavailable`. Cached sources, in order: in-memory build plan, asset
      plan, ship-margin cache, discover cache.
    - The portfolio card calls `/api/production/realized/summary`, which is
      already production-gated. No figure was added to the portfolio route.
    - Period `tax_missing_sales` counts sales whose tax is null except
      `source = contract`, because a contract stores null tax on purpose.
  - Review fixes:
    - D7 for contracts: only a tenant *character* as issuer or acceptor
      books a contract (a corp contract counts through the member who
      issued or accepted it). A contract whose only tenant side is a corp
      is skipped, and a character-to-own-corp contract is an internal
      transfer. Before, a corp-issued or corp-accepted contract was booked
      in every tenant sharing that corp.
    - `planned_difference_per_unit` comes from the API and compares
      realized and planned cost over the same units (those with a planned
      value). The frontend used to subtract planned cost from the cost of
      all sold units.
    - The Portfolio card shows no figure until a recompute produced one.
  - Not in Phase 5: D7's corp "mine alone" switch (no corp token has the
    Accountant role, so there is no corp wallet data).
- **Test-server verification 2026-10-17** (Default tenant, real archive:
  17k wallet transactions, 12k journal entries, 2.4k jobs, 108 contracts).
  Fixed from it:
  - The tenant's characters were only the registry entry (one login
    character). Every other character read as an outsider: 2000 corp jobs
    were skipped and contracts between own characters were booked as sales
    at price 0 (-96 bn ISK in 30 days). Tenant characters are now the
    registry entry plus every character holding a token in the tenant.
  - Sales tax was looked up only at `journal_id + 1` of the
    market_transaction entry; real journals also put it at + 2, so 2 % of
    sales showed "tax missing". It is now the first transaction_tax entry
    after it with the same timestamp, before the next market_transaction.
    Remaining misses are the newest sales whose journal ESI has not served
    yet (journal cache lags transactions).
  - The first sync of an owner archives its whole job history, and every
    job got a "planned" row. Planned cost is now frozen only for jobs first
    seen within 24 h of their start (`planned.MAX_JOB_AGE`).
  Checked and correct: reference prices equal ESI's Forge daily average;
  freight is 900 ISK/m3 x packaged m3 on Forge buys only; opening stays
  frozen without a reset; D11 hides the run on revoke and shows it again
  on re-share; hidden-row delete is refused while shared; doctrine
  contracts a member issued for the corp are contract sales. Expected, not a
  bug: with T0 one week back almost every sale draws on opening stock
  (coverage ~2 %), and C-J sells 10-30 % below the Forge average, so those
  sales show a loss against the opening's market value (D5).
  Follow-up decision with the user the same day: value stock without a
  purchase like the buy list does (cheaper of buying at C-J or the hub plus
  freight, and building), not at The Forge average - section 6
  "Valuation", `COSTING_VERSION` 3 re-derives the opening once.
