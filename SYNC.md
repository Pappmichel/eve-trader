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
| `eve_trader/esi_client.py` | `eve_trader_local/esi_client.py` | The whole generic ESI HTTP layer: `OrderStats`/`_summarize_orders`/`_percentile`, the retry-with-backoff core (`_get_response`/`_post_response`, `_retry_after_seconds`), ESI's `X-Esi-Error-Limit-*` budget tracking, `_get_all_pages`' page-1-then-concurrent-rest pagination, the class-level price/cost-index and short-TTL order-book caches, and every endpoint wrapper (character/corp orders, assets, wallet, industry jobs, blueprints, contracts, skills, structures, name/system resolution, market history, adjusted prices, cost indices) | Ported 2026-09-02, near-verbatim. Deliberately left out on the local side: `structure_order_stats_bulk_or_goonmetrics` (its Goonmetrics fallback needs `goonmetrics_client.py`, still a candidate below — port it together with that, not before). Adapted there: this repo's `storage.with_current_tenant` wrapper around every `ThreadPoolExecutor.submit` is dropped (it exists purely because a worker thread doesn't inherit the tenant contextvar; there is no ambient tenant in eve-trader-local), and `ESIError` subclasses eve-trader-local's `ActionError` (this repo keeps them separate only to dodge a `config.py`↔`actions.py` circular import that doesn't exist there). `resolve_effective_volume`/`_bulk` needed two new SQLite-side helpers on the local side (`storage.get_type_category`, plus a `type_packaged_volume` cache table and its getter/setter) — that table is an ESI-only per-type constant, so it deliberately survives an SDE refresh there. |

## Candidates — port when the algorithm changes, once each exists on the local side

None of these are in `eve-trader-local` yet (only the storage/config/auth/SDE/
ESI-client foundation has been built so far). When one gets ported over as a
real local feature, add it to the table above and keep the following in sync
going forward. Each entry names *what part* is shareable — usually the
computation, not the surrounding storage/config wiring:

| File | Shareable logic |
|---|---|
| `eve_trader/goonmetrics_client.py` | Region price/history fetch shape, the caching *pattern* (not the cache itself — see CLAUDE.md's "Caching pattern" section). Port `esi_client.structure_order_stats_bulk_or_goonmetrics` at the same time on the local side — it was deliberately skipped in that port because it's the one method that depends on this client. |
| `eve_trader/shortlist.py` | `average_market_daily_volume`, Profit/Day computation (issue #100's fix) |
| `eve_trader/history_backtest.py` | Historical candidate-scoring logic |
| `eve_trader/candidate_discovery.py`, `station_trading/candidate_discovery.py` | Discovery heuristics |
| `eve_trader/trade_reconciliation.py` | Realized-sales matching |
| `eve_trader/own_orders.py` | Order-management logic |
| `eve_trader/production/engine.py` | `classify_activity`, `potential_daily_profit`/`daily_movement`, `ACTIVITY_MODS` |
| `eve_trader/production/pricing.py` | Buy-vs-build pricing |
| `eve_trader/production/invention.py` | Invention math |
| `eve_trader/production/constants.py` | Structure/rig-tier enums |
| `eve_trader/refining/engine.py`, `pricing.py`, `reprocessing.py`, `optimizer.py`, `paste_parser.py` | Ore/mineral/reprocessing math |
| `eve_trader/doctrine/engine.py`, `parser.py`, `validation.py` | Fitting parsing/validation logic |
| `eve_trader/*/models.py` | Plain dataclasses — portable as-is where they don't reference `storage`/`TRADING_CONFIG` directly |
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
