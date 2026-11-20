# Realized profit ledger

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Production realized profit

Plan: `docs/PRODUCTION_REALIZED_PROFIT_PLAN.md`. The shared archive and
costing live in `eve_trader/realized/` (`ingest.py`, `inputs.py`,
`access.py`, `db.py`, `costing.py`, `loader.py`, `names.py`). Production's
report and planned-cost freeze stay in `eve_trader/production/realized/`
(`report.py`, `planned.py`). Trading
(`eve_trader/trade_realized/`), Doctrine (`eve_trader/doctrine/realized.py`)
and Station Trading (`eve_trader/station_trading/realized.py`) read the
same derived rows and keep their own channel. Portfolio shows one card per
granted tool and a total from `GET /api/portfolio/realized-cards` when at
least one of those tools has a figure. That response also carries the
period's freight, courier and configured rate, summed over those tools, and
`unreported_sales` / `unreported_revenue` / `unreported_profit`: results of a
granted channel whose owner lies outside that tool's own report scope (for
example a Trading sale from a wallet shared only with Production), counted by
`realized.db.unreported_results` and shown as one "in no tool" line under the
cards. They are null while any channel needs a recompute.
The wallet / jobs /
orders / contracts **archive** is the source of truth. Derived tables
(`prod_cost_layers`, `prod_realized_results`, `prod_cost_draws`,
`prod_job_records`, `prod_fee_lines`, `prod_job_losses`,
`prod_capital_bpos`, `prod_inventory_drift`, `prod_ledger_runs`) are
rebuilt from it and can be dropped and rebuilt. Archive reads go through
`access.py` and return the union of owners shared with any realized-profit
tool (D11 / unified plan U3a); revoking the last such share hides those
rows until one is granted again. A report filters with
`shared_owners_for`. Flags
(`estimated`, `price_missing`, `assumed_me`, `assumed_decryptor`,
coverage, `no_structure_bonus`) are stored on the derived rows and must
reach `/api/production/realized/` unchanged. Freight is the courier
rate when a shipment covers the purchase, and the configured rate
otherwise. Both are labeled that way. HTTP recompute is a `pipeline_runner`
background job; the CLI rebuild calls the same `do_rebuild_realized`
in-process. Opening `/production/realized` starts that rebuild once per page open,
without a click, when the report is `stale` (an archive sync finished after the
last recompute) or `needs_rebuild` (no run yet, or sharing changed) and an archive
exists (`cutover_t0` or `archive_since` is set); opening `/admin/sde-preview`
starts one SDE preview per page entry, unless a preview is already running. A run row is written only after the replace commits. Each run
stores a fingerprint of the shared owners (`access.owner_scope_hash`); the
report hides a run whose fingerprint no longer matches, so D11 also holds
for derived rows. Reprocessing detection also covers compressed gas (SDE group 4168): its
only SDE material is the raw gas, so a decompression is booked as a
reprocessing at `RefiningConfig.gas_decompression_yield` (Ore & Minerals
Settings, default 0.95 from production data). Raising
`realized.access.OBSERVED_TYPES_VERSION` starts a new observation series
whenever the observed types change. Detection tracks the types the series
ever held plus the reprocessable types the ledger moves, so a type gone
before the series began is still detected.
Ledger stock (opening and inventory drift) counts items waiting in
Deliveries / CorpDeliveries, unlike the planners: a contract buy is booked
on completion and the reprocessing observations count them too.
The tenant's own characters (`loader._tenant_character_ids`, read once per
rebuild) are the registry entry, every character holding a token in the
tenant, and every character with an `esi_sharing` row or an Access
capability, except one removed on the Characters page
(`esi_removed_characters`, written by `do_remove_token_character`, which
keeps the sharing rows) that holds no token; a token always wins
(confirmed with the user 2026-11-15/16). Corporation sharing rows are not
characters. A character that owns archive rows in a rebuild but is not own
gets the run warning `foreign_character_with_archive` (contracts with it
count as sales). A market fill between two own owners (ESI `client_id`,
archived on `ledger_wallet_transactions`) is a transfer, not a result: the
sale draws its layers, the buyer's archived purchase of the same fill gets
layers with that cost plus the fill's tax and broker fees (`source =
'transfer'`) and is not booked or freighted again; without that purchase
the sale stays a result warned `internal_trade_unmatched`. Rows archived
without `client_id` are booked as before. Wallet transactions get the
journal's gap rule too: a full 2,500-row page newer than the archive is an
archive gap. Opening quantities are frozen per type
(`prod_opening_frozen`, not a derived table) and only re-derived when T0 or
that fingerprint changes, on `--reset-opening`, or once when the latest run's
`costing_version` is NULL or lower than the loader's current version
(`prod_ledger_runs.costing_version`, currently 7). After that rebuild the
opening is frozen again. Re-deriving it on every rebuild would make the
inventory drift check always zero.

**Fees are negative journal entries.** Broker fee, market tax, reprocessing tax
and contract broker fee count a journal entry as cost only when its amount is
negative (cost = -amount). A positive entry (income of a structure-owning
corporation wallet) is ignored and reported once per run as
`fee_income_ignored: <ref_type> <owner> <ISK>`. Days are UTC everywhere:
`eve_trader/timeutil.py` (`utc_now`, `utc_today`, `utc_now_naive` for stored
naive ISO strings) replaces `date.today()` and `datetime.utcnow()`, and aware
timestamps are converted to UTC before they are bucketed by day.

**T0 and fee coverage are separate (confirmed with the user 2026-11-07).**
Cutover T0 is the oldest archived transaction of any shared wallet and stays
shared for jobs, opening and coverage (plan section 6). A truncated journal
no longer pushes it forward, and sharing one more busy wallet does not hide
the others' history. Each wallet (owner, division) instead has its own fee
coverage: when its journal starts after its oldest transaction (or it has no
journal at all), a market sale at or before that start keeps its place in the
results with the flag `fees_missing` (`prod_realized_results.fees_missing`,
warning `fees_missing`). Its sales tax and broker fee were never archived;
nothing is estimated (revenue is gross, fee 0, `tax_missing` too), and those
fills are left out of the daily broker-fee distribution so a partly covered
day's pool is not spread over them. `realized_totals` counts them and sums
their revenue/profit; every channel report and the Portfolio cards carry
`fees_missing_sales` / `fees_missing_revenue`. Buy fills before a journal
start also lack their fee share inside their layer (not flagged). Doctrine's
contract sales never carry the flag.

Finished item-exchange contracts (`status = 'finished'`, `date_completed`
set) enter costing through `LedgerSnapshot.contracts`. A contract whose
issuer and acceptor are both tenant owners is ignored. The tenant as
acceptor books items the issuer provides (`is_included`) as `bought` layers
dated `date_completed` (a corporation counts as acceptor only when its
wallet is shared with a realized tool - ESI names no member for a corp
acceptance, D7 revision 2026-11-14), origin stays `bought` and `source` is `contract`;
the price is split by reference value at that date (D4) and the shares sum
exactly to the price. The tenant as issuer books Production-type items it
gives as sales under the same FIFO order as market sales, with no sales tax
and no broker fee. A given blueprint line gets no result; the share of the
price split onto it (by quantity, blueprints have no reference value) is not
booked and is warned `contract_blueprint_share_unbooked` with its amount.
A contract that both gives and receives items is booked
on both sides and warned `contract_item_swap`. Freight on a contract buy
is the courier rate when a shipment covers that purchase, otherwise the
configured rate times packaged m3. The packaged m3 stays frozen on
`ledger_contract_items` at ingest. Draws and sales persist `source`
(`market` / `contract`). Courier contracts the tenant issued are archived
(`volume`, `collateral`, `region_id`) and never enter costing as buys or
sales. `GET /api/production/realized/courier` lists the shipments that
priced freight (reward, booked freight, gap), rewards whose courier is
not archived, and failed couriers. A Trading contracts share supplies
those couriers; item-exchange sales stay on Production and Doctrine shares.

**Reprocessing (confirmed with the user 2026-11-04).** ESI has no
reprocessing event, so ore that disappears counts as reprocessed. Every asset
sync of an owner in the realized-profit asset scope writes the tenant-wide
quantity of ore, ice, compressed ore and active Module Reprocessing shortlist
types (`ledger_stock_observations`, changed rows only, per
`access.assets_scope_hash`; everything counts, fitted and listed included).
Costing (`costing._detect_reprocessing`, a quantity-only pre-pass) books a
deficit against the ledger as a `reprocess` event in whole portions, placed
at the previous observation: it consumes the ore FIFO, adds that window's
`reprocessing_tax` journal entries as fees, and splits the cost over the
outputs by reference value (`origin = reprocessed`). A raw deficit next to a
compressed surplus is a compression (`origin = compressed`, ratio from SDE
materials). A surplus within 14 days undoes an earlier deficit and the other
way round (couriers, moves between owners). Yields come from Ore & Minerals /
Module Reprocessing settings (a refinery without a rig gets no security yield
factor, it is a rig attribute), so outputs are estimates; a wrong yield shows as
inventory drift. A reprocess candidate or pending surplus detected while a courier issued by an own
character was in transit counts its 14-day undo window from that courier's
completion (`CourierContract.date_completed`; warning
`reprocess_undo_window_extended`). The archive keeps only finished and failed
couriers, so a courier that is still open extends nothing until it completes
and the next rebuild runs. Detection is switched off (warning `reprocess_detection_off`)
while a wallet owner does not also share assets and market orders, or a
contracts owner its assets. Opening is re-derived once when the first
observation is used (`prod_ledger_runs.reprocess_baseline_at`). Other modules
stay drift: they also vanish through losses and gifts.

`ledger_job_planned_costs` is an archive table (not in the rebuild's replace
set). The first time a job is archived, a cached planner cost/unit is frozen
if one is already in memory (build plan, asset plan, ship-margin cache, or
discover cache); otherwise the row stores NULL. No new network call runs on
that path. The products report exposes `planned_cost_per_unit` and
`planned_coverage`. Hidden archive rows of a kind can be counted and deleted
per owner while that kind is not shared with Production.

**Observed sell fees (click to apply, never automatic).** `GET /api/production/
realized/observed-fees` and `GET /api/trading/realized/observed-fees`
(`realized/sell_fees.py`, per-channel `observed_sell_fees()` in each report)
show what the wallet actually charged: (sales tax + broker fee) / gross over the
channel's market sales of the last 30 days, in the report's own owner scope.
Production's figure is for `market_fees`; Trading's is `1 - quote` for
`structure_sell_haircut`. Two fields, two channels, never merged. No value (with a
`reason`) when there is no ledger run, the shared wallets changed since it
(`needs_rebuild`, same fingerprint check as the report), or under 20 market sales
with a known sales tax. The Settings pages show "Actual last 30 days: x % (n
sales)" and a "Use this value" button that goes through the normal settings save;
nothing changes a setting on its own, because a corporation wallet mixes other
members' sales into the figure. The broker fee per sale is costing's pro-rata
attribution (a day's real order fees spread over that day's order-based fills),
and relist fees count only as far as a sale got them, so the figure is an estimate.
A second, display-only line shows `upper_bound_quote`: the same numerator plus
the order fees costing booked to no fill (`prod_fee_lines` `without_sale`) of the
scope's own wallet owners on the window's days, each owner's fees split across
channels by that owner's market gross per channel (`db.without_sale_fees_by_owner`,
`db.market_gross_by_owner_channel`; an owner with no market sale in the window adds
nothing). It also holds buy-order and unmatched contract broker fees, so it is an
upper bound; no button, nothing stored. Trading shows it as `1 - upper_bound_quote`
(`upper_bound_value`, a lower bound for the haircut) next to the fee share.

**Fees from skills and standings (click to apply, never automatic).**
`eve_trader/fee_skills.py` (pure) holds the formulas, checked against EVE
University's "Trading" page: sales tax 7.5 % x (1 - 0.11 x Accounting), NPC
station broker fee 3 % - 0.3 % x Broker Relations - 0.03 % x faction standing -
0.02 % x corporation standing (raw standings towards the station owner; no floor
stated, clamped at 0; Advanced Broker Relations only touches relists). In a player
structure the owner sets the broker fee, so only the sales tax comes from skills.
`eve_trader/fee_skill_hints.py` is the I/O half: one character per tool, chosen
in its Settings (`TradingConfig.fee_skill_character_id`,
`StationTradingConfig.fee_skill_character_id`, `PiConfig.pi_fee_skill_character_id`,
confirmed with the user 2026-11-16). Choosing one changes no fee; without one the
reason is `no_character_selected`. Skills and standings are read only through
`fields.snapshot` (`not_shared` / `reauth_needed` / `not_synced`; the choice stays
saved). Standings are on demand: without them (or without an SDE station owner,
`sde_stations.corporation_id` + `sde_npc_corporations.faction_id`) the broker fee
is still shown, labeled an upper bound. Routes: `GET /api/trading/fee-suggestions`
(`jita_buy_broker_fee`: a standing buy order at the hub's main station, plus
`observed_buy` = attributed broker fees / price of the Trading wallets' market
buys in the hub system over 30 days, >= 20 buys, `realized/buy_fees.py`; instant
buys pay no broker fee, so it is usually lower, O3-08), `GET /api/station-trading/
fee-suggestions` (`broker_fee_rate`, `sales_tax_rate`: skills at `station_id` plus
the channel's `observed_quote` split), `GET /api/pi/fee-suggestions` (skills only,
at the price structure or the hub's main station). Field defaults stay unchanged.
