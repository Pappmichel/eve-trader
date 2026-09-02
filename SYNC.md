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

## Candidates — port when the algorithm changes, once each exists on the local side

None of these are in `eve-trader-local` yet (only the storage/config/auth/SDE
foundation has been built so far). When one gets ported over as a real
local feature, add it to the table above and keep the following in sync
going forward. Each entry names *what part* is shareable — usually the
computation, not the surrounding storage/config wiring:

| File | Shareable logic |
|---|---|
| `eve_trader/esi_client.py` | Order-book percentile stats, price-adjustment math |
| `eve_trader/goonmetrics_client.py` | Region price/history fetch shape, the caching *pattern* (not the cache itself — see CLAUDE.md's "Caching pattern" section) |
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
