# SYNC.md — keeping eve-trader (online) and eve-trader-local (offline) aligned

`eve-trader` (this repo) and `eve-trader-local`
(https://github.com/Pappmichel/eve-trader-local) are two separate codebases,
not a fork/branch pair — they deliberately diverge at the storage/config
layer (Postgres + RLS + `ConfigProxy` here vs. SQLite + a plain dataclass
there, see that repo's own `SYNC.md`/README). There is no tooling that
syncs them automatically. This file is the manually-maintained contract for
*what* needs porting by hand when it changes, and *what never should be*.

**In scope for syncing:** general business-logic/behavior — pricing
formulas, ESI/Goonmetrics parsing and math, classification rules, the OAuth
flow itself. **Out of scope, by design:** anything tenant-specific,
user-specific settings/data, or access-control/multi-tenant machinery — see
"Never sync" below. When in doubt: would a single local user's own settings
or a specific tenant's data ever appear in the diff? If yes, it's not a sync
candidate.

## Already ported

### Trading (plus the shared SDE/ESI/config foundation)

| File here | File in eve-trader-local | What's shared | Status |
|---|---|---|---|
| `eve_trader/auth.py` | `eve_trader_local/auth.py` | EVE SSO authorization-code+PKCE flow: state/PKCE generation, loopback callback handling, refresh logic | Ported 2026-09-02. Storage calls adapted (no tenant scoping), locking removed (single-process) — port algorithm changes only, not the storage glue. |
| `eve_trader/production/sde.py` | `eve_trader_local/sde.py` | Fuzzwork CSV fetch/parse: the file list, `_fetch_csv`'s retry-with-backoff, `_dump_etag`'s ETag freshness check, every row-shaping rule (the `_RELEVANT_ACTIVITIES` filter, the slot-defining dogma effect IDs, `metaGroupID`/`portionSize` handling) | Ported 2026-09-02, near-verbatim. **Data layer only.** Deliberately left out: `ProductionConfig.fuzzwork_csv_base` (a module constant there — no Production config exists in eve-trader-local yet), and most of this repo's `storage.py` SDE *read* helpers (`load_sde_*`, `get_type_category`, `get_blueprint_*`, `find_invention_recipe_candidates_*`, `get_type_slot`, `get_type_materials`, …) — those exist to serve Production/Doctrine/Refining business logic that isn't ported to eve-trader-local yet; port each one when the feature that needs it arrives there. Only `sde_row_counts`, `get_sde_type` and `search_sde_types` came over, to make its `refresh-sde`/`sde-status` CLI verifiable by a human. This repo's `lru_cache` memoisation on those reads was dropped on the local side (nothing there calls them in a hot loop yet, and a cache would need invalidation on every refresh). **Amended 2026-09-02**: the four SDE activity IDs are no longer inlined on the local side — `production/constants.py` is ported there now and owns them, same import direction as here (see the Production row below). |
| `eve_trader/esi_client.py` | `eve_trader_local/esi_client.py` | The whole generic ESI HTTP layer: `OrderStats`/`_summarize_orders`/`_percentile`, the retry-with-backoff core (`_get_response`/`_post_response`, `_retry_after_seconds`), ESI's `X-Esi-Error-Limit-*` budget tracking, `_get_all_pages`' page-1-then-concurrent-rest pagination, the class-level price/cost-index and short-TTL order-book caches, and every endpoint wrapper (character/corp orders, assets, wallet, industry jobs, blueprints, contracts, skills, structures, name/system resolution, market history, adjusted prices, cost indices) | Ported 2026-09-02, near-verbatim. `structure_order_stats_bulk_or_goonmetrics` followed 2026-09-02 with the `goonmetrics_client.py` port (see the row below) — it was held back only because it's the one method depending on that client. Adapted: this repo's `storage.with_current_tenant` wrapper around every `ThreadPoolExecutor.submit` is dropped on the local side (it exists purely because a worker thread doesn't inherit the tenant contextvar; there is no ambient tenant in eve-trader-local), and `ESIError` subclasses eve-trader-local's `ActionError` (this repo keeps them separate only to dodge a `config.py`↔`actions.py` circular import that doesn't exist there). `resolve_effective_volume`/`_bulk` needed two new SQLite-side helpers on the local side (`storage.get_type_category`, plus a `type_packaged_volume` cache table and its getter/setter) — that table is an ESI-only per-type constant, so it deliberately survives an SDE refresh there. |
| `eve_trader/goonmetrics_client.py` | `eve_trader_local/goonmetrics_client.py` | The current-price fetch shape (`appraise.gnf.lt/market/{slug}/prices.json`, its price parsing and retry-with-backoff) and the caching *pattern*: module-level cache dicts keyed by market, with a per-market lock rather than one shared lock, so a multi-second Jita fetch never blocks a concurrent home-market fetch that shares no cache entry with it. Plus `ESIClient.structure_order_stats_bulk_or_goonmetrics` (in `esi_client.py`, same as here) — the failsafe that falls back to a Goonmetrics snapshot when no seller is logged in or the real structure order book fails, returning `(stats, used_fallback)` with volumes zeroed. Plus the region price-history half: `price_history`/`price_history_chunked`/`HistoryPoint`/`_parse_history_xml`, its silent per-type ESI history fallback for when that no-SLA API is down (where `movement` passes ESI's `volume`, a unit count, straight through, never multiplied by average price), and the chunked fetch's per-chunk isolation so one bad chunk can't lose a whole multi-minute search. | Ported 2026-09-02 (current prices), completed 2026-09-02 with the price-history half. Both endpoints are now on the local side, and its module docstring spells out which question each answers — a region history average ("was this historically worth importing", asked against `reference_region_id`) is not interchangeable with a live current quote. Added there: `TradingConfig.goonmetrics_appraise_base` and `goonmetrics_history_base` (this repo hardcodes the former as a module constant `APPRAISE_BASE`; eve-trader-local already has an `esi_base` config precedent, so both became fields there) plus `chunk_size` (with a `_FIELD_RANGES` entry). |
| `eve_trader/candidate_discovery.py`, `eve_trader/models.py` | `eve_trader_local/candidate_discovery.py`, `eve_trader_local/models.py` | The whole discovery pass: `is_wanted_market_path`'s exclusion-prefix-only filtering (no keyword allowlist, no per-item m3 cap — an item is only ever dropped for failing on real profitability/volume later), `guess_category`'s real-SDE-category classification (with the string/volume heuristic kept strictly as the live-ESI-walk fallback, and the `IMPLANT_CATEGORY_ID`/`BOOSTER_GROUP_ID` split that keeps drugs from being labelled implants), `_market_group_path`, the SDE-cache-first/live-ESI-walk-fallback `build_candidate_universe`, and `build_focused_candidate_universe` (a deliberate pass-through). Plus the `Candidate` dataclass. | Ported 2026-09-02, near-verbatim on the local side — including issue #73 (capital-sized modules need their *packaged* volume, not the SDE flight volume) and issue #96 (resolve it in one bulk concurrent pass, never per-type inside the loop) context, which are real bugs, not stylistic notes. Added there to support it: `TradingConfig.excluded_path_prefixes`, four SDE read helpers (part of the set the `production/sde.py` row deliberately deferred), and `candidate_universe`/`focused_candidates` tables — persistence stays the *caller's* job there too (`actions.do_build_universe`), so the module itself stays free of storage writes. eve-trader-local's `load_candidate_universe` returns `Candidate` objects where this repo's `read_table` returns a pandas DataFrame (no pandas on the local side, and every caller there wants the dataclass). Only `Candidate` came from `models.py` in this row; its other dataclasses arrived with `history_backtest.py` (`NewCandidateResult`, see the row below) or still await `shortlist.py`/`trade_reconciliation.py`. |
| `eve_trader/history_backtest.py`, `eve_trader/models.py` | `eve_trader_local/history_backtest.py`, `eve_trader_local/models.py` | The whole backtest/scoring pass: `_index_history`, `_latest_margin`, `_score_candidate` (the landed/net_sell/margin formula, `score = avg_profit_m3 x log(1+avg_move) x hit_rate`, and the five-part `add` gate), `compute_margin_trends` (3-day vs 30-day rolling-margin momentum, with `MIN_TREND_HISTORY_DAYS` and the `MIN_BASELINE_MARGIN_MAGNITUDE` near-breakeven guard), `select_candidate_window`'s rotating coverage, and `find_new_import_candidates`/`_safe` with their per-batch isolation and incremental sinks. Plus the `NewCandidateResult` dataclass. | Ported 2026-09-02, near-verbatim apart from `compute_margin_trends`. **Pandas deliberately not added on the local side**: this repo's version takes a DataFrame, but everything it does with it is an inner join on `(type_id, date)`, a per-type grouping and two tail-window means — no vectorised math, no rolling/resampling, and at this size (a few thousand rows per candidate search) plain dicts/lists are just as fast. eve-trader-local's version takes an `Iterable[HistoryPoint]` instead, which is also the exact shape `find_new_import_candidates`' `history_sink` already emits there, so no DataFrame-shaped storage read has to exist for it. Same dependency-light call the `candidate_discovery.py` row already made. Added there to support it: `TradingConfig.safe_mode_max_ids`/`min_margin_threshold`/`min_hit_rate`/`min_avg_movement` (each with a `_FIELD_RANGES` entry). Deliberately left out on the local side: any storage coupling at all — persistence (`storage.save_goonmetrics_history`/`save_new_candidates`/`candidate_search_cursor` here) is the caller's job there too, same precedent as `candidate_discovery.py`, and the callbacks/offset that let a caller do it are ported; those three SQLite helpers arrive with eve-trader-local's future `do_find_new_candidates` equivalent, which doesn't have an actions layer yet. |
| `eve_trader/shortlist.py`, `eve_trader/models.py` | `eve_trader_local/shortlist.py`, `eve_trader_local/models.py` | The whole per-row evaluation: `_decision`'s precedence ladder (Inactive → Missing ID → No market data → Skip → Already ordered/Import, with "No market data" and "Skip" deliberately kept as two labels), `evaluate_shortlist_item`'s landed-cost/net-sell/margin/profit-per-m3 formula (priced off the *ask*, not the bid, with `jita_buy_broker_fee` still applied — deliberately conservative, not an oversight) including its "an inactive item is still fully priced" rule, `evaluate_shortlist`'s one-pass-over-pre-fetched-stats shape, `average_market_daily_volume`, `summary_counts`, `top_imports_by_daily_profit` and `audit_shortlist`. Plus the `ShortlistItem`/`ShortlistRow` dataclasses. | Ported 2026-09-02, near-verbatim on the local side. The two historical bugs this file's shape exists for (see this file's own "Theoretical ceiling" section) came over intact, in the code: **Profit / Day is `profit_per_unit x avg_daily_volume`** (real market-wide traded quantity from Goonmetrics region history for `reference_region_id`) — never `sell_volume`/order-book depth (issue #51) and never the trader's own realized sales alone (issue #100). An item Goonmetrics has no history for is excluded from Top Imports rather than estimated. `sell_volume` is still carried as "Listed Qty" and used only as the "is anything listed at all" gate in `_decision`. **Storage-ownership decision (local side): shortlist membership is persisted, but the persistence lives in `storage.py`, not `shortlist.py`** — this repo's `shortlist.py` imports no storage at all (every `storage.*` call is in `actions.py`), but eve-trader-local already keeps every table read/write in one module, so its `shortlist.py` port stays a pure, network-free, storage-free computation over values handed to it, same as its `candidate_discovery.py`/`history_backtest.py`. Added there to support it: `shortlist`/`shortlist_snapshot` tables with matching upsert/load/(de)activate/snapshot helpers (the snapshot read returns `ShortlistRow` objects where this repo's `latest_snapshot` returns a DataFrame — no pandas there), and `TradingConfig.min_profit_threshold` (with a `_FIELD_RANGES` entry bounded at 0 only, never at 1 — a target margin above 100% is legitimate, not a typo). Deliberately left out there: `shortlist_skip_streak`/`skip_grace_period_days`/`max_active_shortlist_items` — those belong to `do_refresh_and_prune_candidates`'s pruning policy in `actions.py`, not ported yet; they arrive with it. |

| `eve_trader/trade_reconciliation.py`, `eve_trader/models.py` | `eve_trader_local/trade_reconciliation.py`, `eve_trader_local/models.py` | The whole realized-P&L pass: `fetch_recent_transactions`' `from_id` cursor paging (a single un-paginated call only ever sees ESI's most recent 2500 transactions, which silently dropped older-but-still-in-window trades), `fetch_recent_journal_entries`' best-effort `market_transaction` lookup, `reconcile_realized_trades`' per-type FIFO match (pooled across every buyer/seller character, not paired 1:1) with both location filters and the `buy_date <= sell_date` rule, the landed-cost/net-sell formula including the wallet-journal real-tax path and its `_ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT` back-out, the on-demand ESI type backfill, `summarize_realized` and `average_daily_sold_by_type`. Plus the `RealizedTrade` dataclass. | Ported 2026-09-02, near-verbatim on the local side. The two matching bugs this file's shape exists for came over in the code, not just in this table: **a buy dated after its sell is never a cost basis** (FIFO alone only orders buys chronologically; in this repo 20% of matched rows, and 21.8% of reported profit, had `buy_date > sell_date` — the matcher breaks and drops the sell's unmatched remainder rather than substituting a later buy), and **the buy side is region-filtered too** (it had no location filter at all, so any wallet transaction the buyer made anywhere could be paired against an unrelated structure sale). `_BUY_LOOKBACK_MULTIPLIER` (buys fetched over 3x the sell window) came with them. `average_daily_sold_by_type` is deliberately kept on the local side even though its shortlist no longer uses it: it answers the narrower "how much have *I* sold of this" question, not the market-wide Profit/Day figure that `shortlist.average_market_daily_volume` now answers there (issue #100 superseded issue #51's realized-sales version on the shortlist side, it did not delete this). **Storage-ownership decision: this module calls storage itself on the local side too, unlike `candidate_discovery.py`/`history_backtest.py`/`shortlist.py`** — this file itself imports `storage` and calls it directly in two places (`get_station_ids_in_region` for the buyer-side filter, and a `realized_trades` read in `average_daily_sold_by_type`), where `shortlist.py` imports no storage at all. So the local port follows this file's actual structure rather than the shortlist precedent: the calls live in `trade_reconciliation.py` there too, the SQL lives in eve-trader-local's one persistence module, and *writing* a run's results stays the caller's job exactly as here (`actions.do_reconcile_trades`). Added there to support it: a `realized_trades` table (replaced wholesale per run, not appended — a naked INSERT had bloated this repo's to ~19.8k rows over 13 runs) with matching save/load helpers (the load returns `RealizedTrade` objects where this repo's `read_table` returns a DataFrame — no pandas there), and `storage.get_station_ids_in_region` without this repo's `lru_cache` (nothing calls it in a hot loop there, and a cache would need invalidating on every SDE refresh). No config fields were needed on the local side: every field this module reads already existed. |

| `eve_trader/own_orders.py`, `eve_trader/models.py` | `eve_trader_local/own_orders.py`, `eve_trader_local/models.py` | All four live order-management checks: `fetch_own_sell_orders` (one pass over the character's orders producing `{type_id: volume_remain}` for open sells at `structure_id`, instead of one filtered query per shortlist row), `check_undercut`/`_pooled` (which of the seller's own listings a competitor has beaten on price), `fetch_seller_stock_without_order`/`_pooled` (shortlist stock physically at the structure with no sell order on it at all), and `fetch_buyer_already_covered` (items already on a buy order or already in inventory at Jita/the structure, so they needn't be imported again). Plus the `UnlistedStockRow`/`UndercutRow` dataclasses. | Ported 2026-09-02, near-verbatim on the local side. Two real constraints came over in the code, not just in this table: **"which orders are mine" can only be decided by `order_id`** — ESI's structure order book carries no owning-character field at all, so type_id/price matching would false-positive on a coincidentally identical competitor price; and **the own-order exclusion is pooled across every registered seller character** (issue #46 — several sellers share one structure's order slots, and a cheaper order from another of your own characters must never read as "undercut"). Same pooling applies to unlisted stock. This file's deliberate scope limits are kept as-is on the local side: only shortlist items are considered, only a *complete* absence of a sell order is flagged, and only assets sitting directly in the structure's own `location_id` are seen (anything nested in a container or parked ship carries that container's item_id). **Storage-ownership decision: this module calls storage itself on the local side too, following `trade_reconciliation.py`'s precedent rather than `shortlist.py`'s** — because this file itself imports `storage` and calls it directly (`get_station_ids_in_system`, for the buyer-side "is this asset in Jita" check), so the local port mirrors this file's actual structure; the SQL lives in eve-trader-local's one persistence module, and nothing in the ported module writes anything. Added there to support it: `storage.get_station_ids_in_system` (without this repo's `lru_cache`, same call already made for its other SDE reads) and `esi_client.NON_STOCK_LOCATION_FLAGS` — that constant lives in `storage.py` here because this repo's persisted asset tables filter on it; **on the local side it sits in `esi_client.py` instead**, since it describes ESI's asset payload and eve-trader-local has no persisted asset table for it to sit beside. Deliberately left out there: item-name resolution, the shortlist-item-id set, the margin/`sell_volume` enrichment and the "log in again for esi-assets.read_assets.v1" error message — all of those live in this repo's `actions.do_check_seller_unlisted_stock`/`do_check_undercut`, not in `own_orders.py`, and arrive with eve-trader-local's future action layer. |

### Production

A separate tool from Trading here too — the two share only the SDE/ESI/
config foundation. Only classification is ported so far on the local side;
everything needing a `ProductionConfig` or Goonmetrics pricing (buy-vs-
build, invention math, build-candidate discovery, `potential_daily_profit`/
`daily_movement`) is still in "Candidates" below.

| File here | File in eve-trader-local | What's shared | Status |
|---|---|---|---|
| `eve_trader/production/constants.py` | `eve_trader_local/production/constants.py` | The whole file: SDE activity IDs, `ACTIVITY_MODS`, `STRUCTURE_TYPES`/`RIG_TIERS` and `structure_rig_multiplier`/`rig_security_multiplier` (including `_rounded_security`'s CCP 1-decimal rule), `DECRYPTORS`, `SCC_SURCHARGE_RATE`, the meta-group IDs, `COMPONENT_GROUP_IDS`/job-category/ship-size group sets, and the job-slot skill IDs with `job_slots_from_skills`. All of it is fixed real-EVE game data with no config or storage dependency, so it is pure sync material — when CCP changes a bonus, both repos change identically. | Ported 2026-09-02, near-verbatim on the local side (in full, not just the parts `classify_activity` needs — the file is one flat table of constants and splitting it would only make the next Production port re-derive the split). **Activity-ID duplication decision:** eve-trader-local's `sde.py` used to inline `ACTIVITY_MANUFACTURING`/`REACTION`/`COPYING`/`INVENTION` with a comment saying it did so only because no Production module existed there to own them. That condition is now gone, so they have exactly one definition in `production/constants.py` there and `sde.py` imports them — the same direction as here, where `production/sde.py` imports them from `production/constants.py`. No cycle and no layering inversion on the local side either: this module imports nothing at all (not even `storage`), so it is a leaf the data layer can depend on. |
| `eve_trader/production/engine.py` (`classify_activity` only) | `eve_trader_local/production/engine.py` | `classify_activity` only: the Reaction/Tech II/meta-group/Tech I decision ladder and the `meta_group_labels` mapping (metaGroupID 4/5/3/6 → Faction/Officer/Storyline/Deadspace). | Ported 2026-09-02, verbatim on the local side. **This is the one file in the Production port whose reasoning must never be simplified away:** "Tech II" is decided *solely* by a real SDE invention-recipe lookup, never by `metaLevel` — this file's own `metaLevel >= 2` heuristic once mislabelled 854 of 4208 scanned manufacturable items as Tech II (every Faction/Pirate hull, Officer/Deadspace module and faction booster — Machariel/Nestor are metaLevel 8), pricing each off the flat Tech II ME/TE baseline instead of the Tech I one, while also *missing* genuinely invented Tech III subsystems (a Loki Core is metaLevel 1). Both halves are covered by explicit regression tests on the local side. No `ProductionConfig` was created there: classification is a pure function of the SDE cache with zero config dependency — the config class arrives with the first pricing/buy-vs-build port that actually needs a field. Added there to support it: `storage.get_blueprint_for_product` and `storage.find_invention_recipe_candidates_by_product_type_id`, ported from this repo's `storage.py` (both read tables `sde.py` already populates there; this repo's `lru_cache` dropped, same call already made for every other SDE read on the local side). The `published = 1` filter on the blueprint lookup came with it — a leftover unpublished CCP test blueprint would otherwise make an unbuildable item look buildable — and has its own test there. |

## The orchestration layer (`actions.py`) — an integration, not a port

`eve_trader_local/actions.py` (2026-09-02) is a different kind of entry from
every row above: the rows are file-for-file ports of business logic, this is
the *integration* that finally wires them together on the local side. It
follows this repo's own `actions.py` structurally — same `do_*` names, same
"thin orchestration only, real logic stays in the other modules" rule, same
`ActionError` boundary the CLI catches — but it is a synthesis against
eve-trader-local's own modules, so a future diff against this file will
never apply cleanly line-for-line. **Sync its *behavior*, not its text**: a
change to this file's step ordering, pruning policy, failure isolation or a
`do_*`'s documented result shape is a sync candidate; its tenant/config/
DataFrame plumbing is not.

What's covered on the local side: `do_list_buyer_characters`/
`do_list_seller_characters`/`do_remove_trading_character`,
`do_wallet_balance`/`do_wallet_transactions`, `do_update_settings`,
`do_build_universe`/`do_build_focused`, `do_find_new_candidates`,
`do_add_to_shortlist`, `do_refresh_shortlist` (plus the shared
`_refresh_shortlist_rows`/`_backfill_meta_levels`), `do_shortlist_trends`,
`do_check_seller_unlisted_stock`/`do_check_undercut`,
`do_refresh_and_prune_candidates` (with `_items_past_skip_grace_period`/
`_items_beyond_rank`/`_items_to_reactivate` and
`shortlist_skip_deactivation_days`), `do_reconcile_trades` and
`do_pipeline`. This file's two real behavioral lessons came over in the
code there: `do_pipeline` calls `do_refresh_and_prune_candidates`, never a
separate find+refresh pair (this repo's "Run Complete Pipeline" once added
and pruned nothing at all because of that), and each of its steps is
isolated in its own try/except so a missing login or a network hiccup in
one can't stop the others.

Deliberately excluded on the local side: `do_create_backup`/
`do_list_backups` (this repo's `backup.py` shells out to
`docker exec pg_dump` — see "Never sync"), and anything tenant/admin-shaped.
`do_auth` is excluded too: eve-trader-local's `cli.py` `auth` command calls
`TokenManager.login` directly, and its TokenManager has only ever stored
characters under `f"{prefix}:{character_id}"`, so this repo's legacy-
single-key reconciliation in `_list_role_characters` has nothing to
reconcile there — it is one `tm.list_records(prefix)` call.
`do_recategorize_shortlist` and `do_list_transaction_characters` are left
out there as web-UI-specific one-offs (a one-time fix-up for a bug the
local shortlist never had, and a character picker for a tab that doesn't
exist); port them if a GUI needs them. `do_wallet_transactions` returns raw
`location_id`s on the local side where this repo resolves display names —
that needs this repo's persisted `structure_names` table, which
eve-trader-local has no equivalent of yet.

Added on the local side specifically to support this layer:
- `storage.py`: the `new_candidates`, `goonmetrics_history`,
  `candidate_search_cursor` and `shortlist_skip_streak` tables (the
  persistence `candidate_discovery.py`/`history_backtest.py` were
  deliberately ported without, since their caller owns it there) with
  matching save/load helpers, plus `sde_type_names`. Reads there return
  `NewCandidateResult`/`HistoryPoint` objects where this repo returns
  DataFrames (no pandas on the local side) — the history read imports
  `HistoryPoint` *inside* the function, since a module-level import would
  close a `storage -> goonmetrics_client -> config -> storage` cycle there.
  `new_candidates` is append-only there (a safe-mode run only ever covers a
  rotating window); reads filter to `MAX(run_ts)` themselves.
- `config.py`: `TradingConfig.skip_grace_period_days`,
  `enforce_shortlist_cap` and `max_active_shortlist_items` on the local
  side — the pruning policy fields the `shortlist.py` row deferred to
  exactly this layer, with `_FIELD_RANGES` entries for the two numeric ones.
- `cli.py`: `build-universe`, `find-candidates` (`--safe/--full`, matching
  this repo's own flag pair), `add-to-shortlist`, `refresh-shortlist`,
  `check-unlisted-stock`, `check-undercut`, `reconcile-trades` and
  `pipeline` (`--rebuild-universe`) on the local side. One command per
  action, printing only — no logic, so a future GUI calls the same `do_*`
  functions.

One deliberate behavioral difference from this repo, not an oversight:
eve-trader-local's `do_add_to_shortlist` raises only when *no* candidate
search has ever run, and returns `{"added": 0}` when the latest run simply
recommended nothing. This repo raises on an empty table only, which amounts
to the same thing here (its table accumulates across tenants' runs); there
the distinction has to be explicit so a normal "nothing worth adding this
run" outcome doesn't abort `do_refresh_and_prune_candidates` before it
prunes.

## Candidates — port when the algorithm changes, once each exists on the local side

None of these are in `eve-trader-local` yet. When one gets ported over as a
real local feature, add it to the table above and keep the following in
sync going forward. Each entry names *what part* is shareable — usually the
computation, not the surrounding storage/config wiring:

| File | Shareable logic |
|---|---|
| `eve_trader/station_trading/candidate_discovery.py` | Station-trading discovery heuristics (the Jita→structure import `candidate_discovery.py` is ported, see above) |
| `eve_trader/production/engine.py` (the rest) | `potential_daily_profit`/`daily_movement`, buy-vs-build expansion, `job_category`/`_structure_profile`, the ME/TE stacking helpers — everything past `classify_activity`, which is ported (see the Production section above). Most of it needs a `ProductionConfig` and/or Goonmetrics pricing first |
| `eve_trader/production/pricing.py` | Buy-vs-build pricing |
| `eve_trader/production/invention.py` | Invention math |
| `eve_trader/refining/engine.py`, `pricing.py`, `reprocessing.py`, `optimizer.py`, `paste_parser.py` | Ore/mineral/reprocessing math |
| `eve_trader/doctrine/engine.py`, `parser.py`, `validation.py` | Fitting parsing/validation logic |
| `eve_trader/models.py` (the rest), `eve_trader/*/models.py` | Plain dataclasses — portable as-is where they don't reference `storage`/`TRADING_CONFIG` directly. `Candidate`, `NewCandidateResult`, `ShortlistItem`, `ShortlistRow`, `RealizedTrade`, `UnlistedStockRow` and `UndercutRow` are ported; the rest belong to modules still listed here, so each arrives with its own |
| `TradingConfig`/`ProductionConfig` **field definitions** (`config.py`, `production/config.py`) | The behavioral fields (region ids, thresholds, economics) — not the `ConfigProxy`/contextvars machinery around them |

## Never sync (intentionally divergent, or not applicable)

- `storage.py`, `tenant_scope.py` — Postgres/RLS is the whole reason
  eve-trader-local exists as a separate repo, not a fork.
- `access_gate.py`, `admin.py` — multi-tenant/cross-tenant concepts with no
  local-single-user equivalent.
- `api/*` — FastAPI web layer; eve-trader-local's target UI is a native GUI,
  not a web frontend (`frontend/*` doesn't apply either).
- `scheduler.py` — per-tenant background-job iteration; a local app's
  "scheduler" (if it ever needs one) would be a single-user timer, not
  worth sharing code with this.
- `backup.py` — shells out to `docker exec pg_dump`; meaningless without
  Postgres.
- `sqlite_migration.py` — a one-time Postgres *cutover* tool; conceptually
  the opposite direction from eve-trader-local's SQLite-native storage.
- Any tenant's or local user's own data/settings — this file tracks code,
  never data.

## Maintenance

Update this table (both copies — see the mirrored `SYNC.md` in
eve-trader-local) whenever:
- A "candidate" module actually gets ported to eve-trader-local → move its
  row to "Already ported".
- A genuinely new, storage/tenant-agnostic business-logic module is added
  here → add it to "Candidates".
- A module you'd expect to share turns out to be too entangled with
  storage/config to port cleanly → note it under "Never sync" with why,
  so nobody re-attempts the same porting effort later.
