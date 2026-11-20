# Tools, ESI data, Portfolio, hubs and price sources

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Two tools, one backend (plus Doctrine, Ore & Minerals, Station Trading, Sorting, Character Management, Planetary Industry, and Admin)

- **Trading**: buys in Jita, sells at a private player structure ("C-J").
- **Production**: Tech I/II/Reaction manufacturing planning for the same C-J
  structure - buy-vs-build, stock targets, invention.

These two are the original pair this section's title/history refers to.
`eve_trader/portfolio.py` and `eve_trader/scheduler.py` deliberately span
*just* them. Character-centric ESI access is a **third cross-cutting
package**, `eve_trader/esi_data/` (`docs/ESI_ACCESS_PLAN.md`): the
data-kind registry, fetchers, orchestrator (`do_sync_for_tool` /
`do_sync_all`), and fail-closed accessor (`read_esi`, `is_shared`) live here.
Characters `do_*` actions live in `esi_data/actions.py`. It names tools as strings
and imports no tool package — the same precedent as
`auth.TOOL_ROLE_PREFIXES` and `access_gate.ALL_TOOL_KEYS`. The live reaction activity id 9 -> SDE 11 conversion is one helper,
`eve_trader/industry_activity.py` (`normalize_activity_id`), imported by the
fetchers, `production/esi_sync.py` and the realized ledger; do not write
another inline `== 9` conversion. One deliberate
exception: `esi_data/fetchers.py` lazily imports `eve_trader.realized`
right after a snapshot write to fill the realized-profit
archive (`docs/REALIZED_PROFIT_UNIFIED_PLAN.md`), for an owner shared
with any realized-profit tool, and never lets an archive error fail the
snapshot sync. Do not add a second tool import here. A second, deliberate
exception: `esi_data/actions.py` lazily calls Production's
`invalidate_shared_production_owner_ids_cache()` after a sharing change, because
that cache would otherwise go stale. Do not extend this to other tools, and keep
it a function-level import (a module-level one risks an import cycle). Do not put
ESI-owner sync back into `portfolio.py` or a tool's `esi_sync.py` by
re-deriving a prefix-per-tool layout from a stale reading of this
paragraph. Tool `do_sync_esi` / `do_sync_contracts` / `do_sync_assets`
are wrappers around `do_sync_for_tool` plus tool-specific post-processing
(slots are written by the skills fetcher; Doctrine matching stays in
`doctrine/esi_sync.py`). Token selection (Phase 4) goes through
`esi_data/selector.py`'s `select_auth_role`, used by the orchestrator and the
alerts runner; a few per-tool character listings still enumerate token
prefixes directly (e.g. `actions.py`'s buyer/seller character list,
`doctrine/esi_sync.py`).

The app has since grown more tenant-facing tools that follow the exact
same `do_*`-actions/router/RLS pattern described in the rest of this
file: **Doctrine** (fitted-ship contract/stockpile tracking against EFT
fittings, `eve_trader/doctrine/`), **Ore & Minerals** (ore/ice
import-refine-sell, reprocessing quotes, mineral shopping list,
`eve_trader/refining/`, GitHub issue #90), **Station Trading**,
**Sorting**, and the **Character Management hub** (`eve_trader/
character_management/`, `docs/CHARACTER_MANAGEMENT_PLAN.md` - see its own
section below for the full breakdown) — plus the cross-tenant **Admin** tool
(`eve_trader/admin.py`, see "Tool permissions & Admin" below), which
isn't tenant-facing at all. **Portfolio** is also a tenant-facing tool
(`tool_key "portfolio"`). `portfolio_overview()` still builds the original
Trading/Production figures, including Combined Value, and
`take_portfolio_snapshot()` (calling both `portfolio_overview()` and
`total_wealth()`) is the one write path into `portfolio_snapshots`,
called by the scheduler's `portfolio_snapshot` job and a lazy fallback on
the first `GET /overview` or `GET /wealth-change` read of the day. The page
reads only `/wealth-change` (the overview's stock value took ~10 s on
production) and no longer shows
Combined Value; the column stays so older history rows remain readable
(`docs/PORTFOLIO_OVERVIEW_PLAN.md` P1).

**Total Wealth** reads only owners who share the relevant kind with
Portfolio: assets, blueprints, wallet balances, open sell orders
(`market_orders`) and outstanding contract items (`contracts`), plus
buy-order escrow from `ledger_market_orders.escrow` once an order sync
has stored it, plus **In production**: the frozen inputs
(`ledger_job_inputs`) of jobs shared via `industry_jobs` that started at or
before the owner's asset snapshot and are still running or were delivered
after it (`realized.db.in_production_input_lines`), priced like assets so a
job start leaves the total flat; a consumed blueprint copy is priced like a
copy (see the price matrix), never at its original's quote. A Portfolio-only `industry_jobs` share
is archived for this. ESI's asset list omits listed and contracted items, and
the wallet balance is already net of escrow, so those lines are added
and never subtracted from assets. An unknown escrow is omitted, not
estimated. Portfolio has no OAuth role prefix (see `auth.py`); it reuses
a token another tool already holds. `wallet_balance` is its own data
kind. A Portfolio-only share of `market_orders` or `contracts` is
archived so wealth can read it; Portfolio is not in
`realized.access.KIND_TOOLS`, so costing still ignores a share that no
realized-profit tool holds. One price function covers wealth and capital:
manual price, else home sell, else Jita sell. Unpriced items stay
excluded and are listed. The page
(`GET /api/portfolio/wealth`, `/wealth/breakdown`, `/realized-cards`,
`/realized-history`, `/capital`, `/attention`) shows the breakdown,
realized profit for every granted channel, capital at FIFO cost from the
realized-profit ledger (labeled as such), and an attention list. Capital
keeps blueprint-copy run layers in their own "Blueprint copies" bucket
(quantity in runs, market value = the per-run copy value x runs, a copy
without one left out of both sides); sell orders and contracts draw only
item layers. Like the realized reports, Capital compares the last run's
owner fingerprint (`access.owner_scope_hash`): without a run or after a
sharing change it returns `needs_rebuild` with a hint, no buckets and no
total. The frontend displays those numbers. `/wealth`, `/wealth/breakdown` and
`/attention` share one priced computation per tenant for 120 s
(`eve_trader/wealth_cache.py`; freshness lines stay live, a sync, sharing or
price-setting change, manual price, or SDE apply drops it at once, nothing is
cached while quotes are down, and the snapshot write always computes fresh).
The snapshot also stores `portfolio_snapshots.top_positions` (JSONB): the 20
largest priced types, one row per type (`type_id`, `quantity`, `unit_price`,
`value`, `component`), taken from the same priced rows as the total
(`portfolio_wealth.top_types`). `/wealth-change` returns `price_jumps` against
yesterday's snapshot: a type in both top lists whose unit price doubled or
halved and whose value moved by at least 1 % of today's total (a quantity
change alone is no jump); the page shows a short hint.

All tenant-facing tools share one FastAPI backend (`eve_trader/api/`),
one Postgres store (`eve_trader/storage.py`, multi-tenant - see
"Multi-tenant Postgres" below), and one React/TypeScript frontend
(`frontend/src/`).

**Production sells only at C-J, never Jita** - this was implemented once
(a Jita-comparison feature) and explicitly reverted after confirming with the
user that freighting finished Production goods to Jita isn't part of this
tool's business model. Don't reintroduce Jita as a Production sales channel
without asking first.

**Market hubs are per tool (GitHub issue #222).** `TradingConfig.jita_region_id`
is Trading's own hub only. Every other tool has its own region setting
(default Jita 10000002), read in one place per tool: `ProductionConfig.
hub_region_id`, `DoctrineConfig.hub_region_id`, `RefiningConfig.hub_region_id`,
`StationTradingConfig.hub_region_id` (set together with `station_id` by its
Settings page) and `ModuleReprocessingConfig.input_hub_region_id` (ore/mineral
inputs; modules keep `purchase_region_id`). `production/jita_price_cache.py`
is a Jita-only, cross-tenant cache (constant region, no config read), and the
Goonmetrics "jita" slug fallbacks apply only to a Jita hub - a non-Jita hub
goes straight to live ESI and missing prices stay missing.

The four non-Trading tools that have a hub setting (Production, Doctrine,
Refining, Module Reprocessing's input hub) may also pick **All hubs (best per
item)**: the setting value `0` (`hubs.ALL_HUBS`, `eve_trader/hubs.py`), where
`hubs.hub_pricing` prices each item at the hub with the lowest landed cost
(sell price + buy broker fee + that hub's freight). Freight per hub is one
tenant-wide table, `TradingConfig.hub_freight_cost_per_m3` (region id string ->
ISK/m3, edited via `/api/hubs/freight`); a hub without an entry falls back to
the calling tool's own freight value. Station Trading rejects `0` (its hub is
tied to one `station_id`), and Production only *buys* at the best hub - listing
status/valuation still uses Jita (`production/engine.py`). Goonmetrics history
covers only a few regions (The Forge, Insmother, Delve); for any other region
`goonmetrics_client.region_has_goonmetrics_history` is false and
`price_history` reads ESI daily history instead.

The hub list itself lives once, in `hubs.HUBS` (region, name, label, solar
system, main station); `hubs.TRADE_HUBS` is derived from it with the same order
and keys, `own_orders` reads the hub system from it, and the frontend gets it
from `GET /api/hubs` (`useTradeHubs()`), never from a second hard-coded list.
`GET /api/hubs/observed-freight` (`realized/freight_rates.py`) suggests a
freight rate per hub: `sum(reward) / sum(volume)` of the finished couriers to
the home structure in the last 90 days (`costing.group_shipments`, at least 3
shipments from that hub's system, owners per D11, aggregates only). Settings
pages show it under each tool's own freight field and per row of the hub
freight table with "Use this value" (normal settings save); the five freight
fields stay separate (PI freight is hub-to-planet and not part of it).

**Price sources matrix** - four different price sources answer four
different questions, deliberately, not by accident, but nowhere else are
they laid out side by side:
| Question | Source | Where |
|---|---|---|
| Is a *newly discovered* candidate historically worth importing? | Goonmetrics region-average history for `reference_region_id` (Insmother); ESI daily history for types Goonmetrics doesn't track (most blueprints, `esi_for_missing`). Untraded days (avgPrice 0) are not margin days | `history_backtest.py` |
| What can I actually buy/sell *right now* on the live Trading shortlist? | Real ESI order-book stats (5th percentile weighted by listed volume, `volume_remain`; one tiny order cannot set it) for `jita_region_id`/C-J's own structure | `esi_client.region_order_stats`/`structure_order_stats`, via `shortlist.py` |
| What's a Production build/buy decision worth? | ESI order-book quotes first (home structure, Jita through the cross-tenant Jita price cache); Goonmetrics current-price quotes (`appraise.gnf.lt`) for the configured home/Jita markets only as the fallback | `production/pricing.py` |
| What was an item without a purchase worth on that day? | What the planner would have paid: the cheaper of the buy-list purchase price (home market, or hub plus freight) and its build cost (`engine.planner_unit_values`), frozen per (type, day) in `ledger_valuation_prices`. Fallback where the planner has no value: The Forge daily average (Goonmetrics, then ESI daily history, then the nearest traded day within 7 days, cached in `ledger_reference_prices`). A day with no price at all stays `price_missing` (booked at 0 and flagged), never a guessed price. A miss is cached (`ledger_reference_prices`, source `none`) only for a type ESI, the last source, actually answered for (404 counts); an outage of either source never stores a zero price, the next rebuild asks again. Blueprint-copy runs without a cost layer are not priced from the original: they take the per-run value Total Wealth uses (`portfolio.bpc_run_values_with_status`: invention cost per run, or the copy job fee per run for a Tech I copy), frozen per (blueprint type, day) in `ledger_bpc_run_prices` only when that computation ran completely, and stay `estimated` (warning `bpc_run_estimated`); a faction/officer copy stays `price_missing` | `realized/loader.py` |
| What would a produced item sold through Doctrine have cost to buy (production saving, informational)? | The buy-list purchase price even when building was cheaper, frozen per (type, sale day) in `ledger_saving_buy_prices` by every rebuild: the planner's live quote for a sale today or yesterday, otherwise The Forge daily average plus buy broker fee and haul cost per packaged m3. Never `ledger_valuation_prices` (that holds the cheaper value, i.e. the build cost). Lines without a price are left out and the priced share is shown | `realized/saving_prices.py`, `doctrine/realized.py` |
| What did a past sale actually earn? | The shared ledger: sale revenue minus the FIFO cost of the layers that sale drew, on one channel (`production`, `trading`, `doctrine`, `station_trading`). `realized_trades` is the retired matcher and is not a live figure | `eve_trader/realized/` plus each tool's report |
| What is an owned item worth (Portfolio Total Wealth and Capital, and Production's `stock_value`)? | Manual price first; else the buy list's rule on Goonmetrics quotes: the cheaper of home sell and Jita sell plus `haul_cost_per_m3` x packaged m3, without broker fee. A lone overpriced home sell order cannot set the value. A blueprint copy without a manual price is its invention cost per run (cheapest relic grade and decryptor, no ME credit) x remaining runs; a copy that cannot be invented but whose original is on the market (Tech I) is its copy job fee per run (2% manufacturing EIV x copying rate) x runs, never its original's quote; a faction/officer copy stays unpriced | `portfolio._priced`, `portfolio._bpc_run_values` |
| What does a fee setting cost one character (Settings hints)? | The chosen character's skills and raw standings through the formulas in `fee_skills.py`, or the wallet's observed quote (sell: `sell_fees.observed_quote`; buy: attributed broker fees / price of hub purchases, `realized/buy_fees.py`). Shown only, applied by click | `fee_skill_hints.py`, `realized/buy_fees.py` |
| What freight did a hub purchase pay? | The courier shipment rate (`sum(reward) / sum(volume)`) when a finished shipment to the home structure was issued at or after the purchase from that station; otherwise the configured hub rate frozen at ingest. Packaged m3 stays the volume frozen at ingest | `realized/costing.py` |
Each is the right tool for its own job (a historical region average smooths
out noise for "is this worth tracking at all", a live order-book percentile
is what you'd actually pay/receive right now) - don't assume a number from
one is interchangeable with a same-named-sounding number from another
(see Finding 2.5's discovery/live-shortlist mismatch for a concrete case
this caused).
