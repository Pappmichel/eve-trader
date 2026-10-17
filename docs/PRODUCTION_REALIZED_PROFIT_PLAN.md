# Production realized profit - plan

Status: **planned, not started** (2026-10-17). Decisions D1-D11 (section 10)
are confirmed with the user. Revised the same day after a critical review
against the code (section 12 lists what changed and why).

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
| Wallet transactions | `esi_wallet_transactions` (snapshot, DELETE-then-INSERT per sync) | ESI only returns about 30 days; older rows disappear on the next sync. Consumed only by `trading`. No `order_id`. |
| Wallet journal | `esi_wallet_journal` (snapshot) | Same 30-day window. No `context_id`/`context_id_type`, so a journal entry cannot be linked to a job, transaction or contract (`trade_reconciliation._journal_from_snapshot` documents this gap). |
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
| Job cost | `start_date` | adds to the job's output | ESI job `cost`, plus the journal entry linked by `context_id = job_id` only if Phase 0 V1 shows it is not already included |
| Job output | `end_date` | + layer of the product, T1 BPC runs or T2 BPC runs, origin `produced` | consumed input cost + job cost, divided by output quantity |
| Cancelled job | cancel date | per Phase 0 V7 (materials returned or lost) | install cost stays a cost of the period |
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
  (`blueprint_me`, `material_multiplier`, `decryptor_type_id`, `source` =
  `esi_blueprint` / `manual_blueprint` / `assumed_me` / `assumed_decryptor`).
  Freezing matters because the blueprint (and its ME) may be gone by the next
  sync, and config can change later.
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
   was used:
   - first, derive it from the T2 BPCs that appear in the blueprints snapshot
     at the job's output location after delivery (runs and ME identify the
     decryptor);
   - otherwise the decryptor selected in Production for that product
     (`selected_decryptors`), flagged `assumed_decryptor`.
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

Broker fees are charged per order (and again on every price change), not per
fill, and wallet transactions carry no `order_id`. Attribution:

- Fills are matched to orders from `ledger_market_orders` (type, location,
  price, time). The order's broker fees (journal `brokers_fee`, linked per
  Phase 0 V2) are spread over its fills by quantity.
- A fill that cannot be matched gets the modeled rate instead
  (`jita_buy_broker_fee` for buys, `structure_broker_fee` for sells), flagged.
  Instant buys from sell orders carry no broker fee.
- Fees of orders that expire or are cancelled unfilled, and the unfilled share
  of relist fees, are not unit costs; the report shows them as a separate
  "fees without sale" line in the period.

## 6. Cutover, opening inventory and coverage

**Cutover T0.** ESI windows differ (wallet about 30 days, completed jobs about
90 days, order history 90 days). Costing starts at one date T0 = the start of
the *shortest* complete window when the archive went live (practically: the
oldest wallet transaction ESI still returned). Events before T0 are ignored:
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

**Unknown origin (D5).** When a consumption or sale finds no layer (or not
enough), it draws from an `unknown` layer valued at the reference price on the
day of use. There is no separate "own" origin (mined/refined/PI items produce
no ESI event; decision D5): they are `opening` or `unknown` like any other
item without a purchase.

**Reference price** (used for D4, D5, D8 only): The Forge daily average from
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

**Phase 0 - live verification (read-only, one real character with industry
activity).** Confirm before building on it:
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
Numbers are English-only like the rest of the UI. Live verification per
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
  inputs are valued at the reference price (section 6), flagged, with a
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
  T0, oldest layer, reference price on T0 (section 6).
- **D9 freight:** every unit bought in The Forge gets freight to the home
  structure at the rate the planner uses (`haul_cost_per_m3`, or the hub table
  entry in "All hubs" mode) x packaged m3, frozen per purchase at ingest.
  Courier rewards actually paid are not used as the cost (section 5a).
- **D10 retention:** archive-relevant kinds keep syncing for inactive tenants
  holding `production` (section 8). Confirmed with the user 2026-10-17.
- **D11 revoked share:** archive rows are hidden, not deleted; deletion only
  by explicit user action.

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
