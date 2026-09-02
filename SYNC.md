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

| File here | File in eve-trader-local | What's shared | Status |
|---|---|---|---|
| `eve_trader/auth.py` | `eve_trader_local/auth.py` | EVE SSO authorization-code+PKCE flow: state/PKCE generation, loopback callback handling, refresh logic | Ported 2026-09-02. Storage calls adapted (no tenant scoping), locking removed (single-process) — port algorithm changes only, not the storage glue. |
| `eve_trader/production/sde.py` | `eve_trader_local/sde.py` | Fuzzwork CSV fetch/parse: the file list, `_fetch_csv`'s retry-with-backoff, `_dump_etag`'s ETag freshness check, every row-shaping rule (the `_RELEVANT_ACTIVITIES` filter, the slot-defining dogma effect IDs, `metaGroupID`/`portionSize` handling) | Ported 2026-09-02, near-verbatim. **Data layer only.** Deliberately left out: the `production/constants.py` import (the four activity IDs are inlined on the local side), `ProductionConfig.fuzzwork_csv_base` (a module constant there — no Production config exists in eve-trader-local yet), and most of this repo's `storage.py` SDE *read* helpers (`load_sde_*`, `get_type_category`, `get_blueprint_*`, `find_invention_recipe_candidates_*`, `get_type_slot`, `get_type_materials`, …) — those exist to serve Production/Doctrine/Refining business logic that isn't ported to eve-trader-local yet; port each one when the feature that needs it arrives there. Only `sde_row_counts`, `get_sde_type` and `search_sde_types` came over, to make its `refresh-sde`/`sde-status` CLI verifiable by a human. This repo's `lru_cache` memoisation on those reads was dropped on the local side (nothing there calls them in a hot loop yet, and a cache would need invalidation on every refresh). |
| `eve_trader/esi_client.py` | `eve_trader_local/esi_client.py` | The whole generic ESI HTTP layer: `OrderStats`/`_summarize_orders`/`_percentile`, the retry-with-backoff core (`_get_response`/`_post_response`, `_retry_after_seconds`), ESI's `X-Esi-Error-Limit-*` budget tracking, `_get_all_pages`' page-1-then-concurrent-rest pagination, the class-level price/cost-index and short-TTL order-book caches, and every endpoint wrapper (character/corp orders, assets, wallet, industry jobs, blueprints, contracts, skills, structures, name/system resolution, market history, adjusted prices, cost indices) | Ported 2026-09-02, near-verbatim. `structure_order_stats_bulk_or_goonmetrics` followed 2026-09-02 with the `goonmetrics_client.py` port (see the row below) — it was held back only because it's the one method depending on that client. Adapted: this repo's `storage.with_current_tenant` wrapper around every `ThreadPoolExecutor.submit` is dropped on the local side (it exists purely because a worker thread doesn't inherit the tenant contextvar; there is no ambient tenant in eve-trader-local), and `ESIError` subclasses eve-trader-local's `ActionError` (this repo keeps them separate only to dodge a `config.py`↔`actions.py` circular import that doesn't exist there). `resolve_effective_volume`/`_bulk` needed two new SQLite-side helpers on the local side (`storage.get_type_category`, plus a `type_packaged_volume` cache table and its getter/setter) — that table is an ESI-only per-type constant, so it deliberately survives an SDE refresh there. |
| `eve_trader/goonmetrics_client.py` | `eve_trader_local/goonmetrics_client.py` | The current-price fetch shape (`appraise.gnf.lt/market/{slug}/prices.json`, its price parsing and retry-with-backoff) and the caching *pattern*: module-level cache dicts keyed by market, with a per-market lock rather than one shared lock, so a multi-second Jita fetch never blocks a concurrent home-market fetch that shares no cache entry with it. Plus `ESIClient.structure_order_stats_bulk_or_goonmetrics` (in `esi_client.py`, same as here) — the failsafe that falls back to a Goonmetrics snapshot when no seller is logged in or the real structure order book fails, returning `(stats, used_fallback)` with volumes zeroed. Plus the region price-history half: `price_history`/`price_history_chunked`/`HistoryPoint`/`_parse_history_xml`, its silent per-type ESI history fallback for when that no-SLA API is down (where `movement` passes ESI's `volume`, a unit count, straight through, never multiplied by average price), and the chunked fetch's per-chunk isolation so one bad chunk can't lose a whole multi-minute search. | Ported 2026-09-02 (current prices), completed 2026-09-02 with the price-history half. Both endpoints are now on the local side, and its module docstring spells out which question each answers — a region history average ("was this historically worth importing", asked against `reference_region_id`) is not interchangeable with a live current quote. Added there: `TradingConfig.goonmetrics_appraise_base` and `goonmetrics_history_base` (this repo hardcodes the former as a module constant `APPRAISE_BASE`; eve-trader-local already has an `esi_base` config precedent, so both became fields there) plus `chunk_size` (with a `_FIELD_RANGES` entry). |
| `eve_trader/candidate_discovery.py`, `eve_trader/models.py` | `eve_trader_local/candidate_discovery.py`, `eve_trader_local/models.py` | The whole discovery pass: `is_wanted_market_path`'s exclusion-prefix-only filtering (no keyword allowlist, no per-item m3 cap — an item is only ever dropped for failing on real profitability/volume later), `guess_category`'s real-SDE-category classification (with the string/volume heuristic kept strictly as the live-ESI-walk fallback, and the `IMPLANT_CATEGORY_ID`/`BOOSTER_GROUP_ID` split that keeps drugs from being labelled implants), `_market_group_path`, the SDE-cache-first/live-ESI-walk-fallback `build_candidate_universe`, and `build_focused_candidate_universe` (a deliberate pass-through). Plus the `Candidate` dataclass. | Ported 2026-09-02, near-verbatim on the local side — including issue #73 (capital-sized modules need their *packaged* volume, not the SDE flight volume) and issue #96 (resolve it in one bulk concurrent pass, never per-type inside the loop) context, which are real bugs, not stylistic notes. Added there to support it: `TradingConfig.excluded_path_prefixes`, four SDE read helpers (part of the set the `production/sde.py` row deliberately deferred), and `candidate_universe`/`focused_candidates` tables — persistence stays the *caller's* job there too (`actions.do_build_universe`), so the module itself stays free of storage writes. eve-trader-local's `load_candidate_universe` returns `Candidate` objects where this repo's `read_table` returns a pandas DataFrame (no pandas on the local side, and every caller there wants the dataclass). Only `Candidate` came from `models.py` so far; its other dataclasses arrive with `shortlist.py`/`history_backtest.py`/`trade_reconciliation.py`. |

## Candidates — port when the algorithm changes, once each exists on the local side

None of these are in `eve-trader-local` yet. When one gets ported over as a
real local feature, add it to the table above and keep the following in
sync going forward. Each entry names *what part* is shareable — usually the
computation, not the surrounding storage/config wiring:

| File | Shareable logic |
|---|---|
| `eve_trader/shortlist.py` | `average_market_daily_volume`, Profit/Day computation (issue #100's fix) |
| `eve_trader/history_backtest.py` | Historical candidate-scoring logic |
| `eve_trader/station_trading/candidate_discovery.py` | Station-trading discovery heuristics (the Jita→structure import `candidate_discovery.py` is ported, see above) |
| `eve_trader/trade_reconciliation.py` | Realized-sales matching |
| `eve_trader/own_orders.py` | Order-management logic |
| `eve_trader/production/engine.py` | `classify_activity`, `potential_daily_profit`/`daily_movement`, `ACTIVITY_MODS` |
| `eve_trader/production/pricing.py` | Buy-vs-build pricing |
| `eve_trader/production/invention.py` | Invention math |
| `eve_trader/production/constants.py` | Structure/rig-tier enums |
| `eve_trader/refining/engine.py`, `pricing.py`, `reprocessing.py`, `optimizer.py`, `paste_parser.py` | Ore/mineral/reprocessing math |
| `eve_trader/doctrine/engine.py`, `parser.py`, `validation.py` | Fitting parsing/validation logic |
| `eve_trader/models.py` (the rest), `eve_trader/*/models.py` | Plain dataclasses — portable as-is where they don't reference `storage`/`TRADING_CONFIG` directly. `Candidate` is ported; `ShortlistItem`/`ShortlistRow`/`NewCandidateResult`/`RealizedTrade`/… belong to modules still listed here, so each arrives with its own |
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
