# Caching patterns

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Caching pattern

The shared idea everywhere that caches an expensive call: a plain
`time.time()`-based TTL, no external caching library - but it's grown three
slightly different shapes as new needs came up, not one uniform pattern:
- `esi_client.py`'s `ESIClient` uses **class-level** attributes
  (`_adjusted_prices_cache`/`_adjusted_prices_cache_at`, etc, `clear_price_caches()`
  to reset for tests) - deliberately class-wide, not per-instance, since a
  fresh `ESIClient()` is constructed per call/request elsewhere in this
  codebase, so an instance-level cache would never actually hit.
- `goonmetrics_client.py`'s `current_prices` uses **module-level dicts**
  (`_prices_cache`/`_prices_cache_at`, keyed by market) plus a **per-market**
  lock (`_prices_locks`) - narrower than a single shared lock so concurrent
  requests for two different markets don't serialize behind one one lock held
  for the whole (multi-second) fetch.
- `discover_build_candidates` (`production/engine.py`) uses a module-level
  value (`_discover_cache`/`_discover_cache_at`) behind **one single lock**
  covering the whole cached computation, plus explicit invalidation
  (`invalidate_discover_cache()`) from every action that actually changes its
  result set (Settings save, stock target add/remove, decryptor change, SDE
  refresh) -   TTL alone would let a just-changed Setting serve stale results
 for the rest of the TTL window, a real correctness problem, not just a
 performance one.
- `production/engine.py`'s market-velocity estimate (`_w2_market_velocity`)
  keeps a small dict (cap 8, other days dropped on insert) behind a lock that
  guards only the dict, plus one lock per key, so a slow network call blocks
  only callers with the same key; a failed estimate is never cached.
- Per-id SDE lookups (`get_sde_type`, `get_type_materials`, and the recipe
 getters `get_blueprint_for_product` / `get_blueprint_materials` /
 `get_blueprint_time` / `find_invention_recipe_candidates_by_product_type_id`)
 are an `lru_cache` over a plain-dict **sidecar** that bulk loaders fill
 (`lru_cache` has no setter); `cache_clear` is overridden to wipe both, and
 `replace_sde_data` calls it. Tables are global (no tenant_id), so the
 sidecars are shared by every tenant. Two ways to fill the recipe sidecars,
 both in `storage.py`: `prefetch_recipes(ids)` (planner entry points: the
 structural material closure calls it once per BFS level; caches absence
 only for the ids asked for, so a row inserted later is still found) and
 `load_recipe_index()` (every recipe in four queries; sets
 `_recipe_index_complete`, after which an unknown key is answered
 "no recipe" without a query; loaded under one lock, reset by any
 `cache_clear`; readers read the flag *before* the dict). A new bulk
 loader that races an SDE apply must compare `_recipe_generation`. Only
 `warm_sde_caches()` sets the complete flag: it runs at server start in a
 daemon thread (`api/app.py` `_warm_sde_caches`, same
 `PYTEST_CURRENT_TEST` guard as the Asset Manager warm-up; failures are
 logged, startup never waits) under `DEFAULT_TENANT_ID`, and adds the type
 rows the recipes mention (~+18 MB RSS, ~0.2 s on a 53k-type SDE). Tests
 never call it, so tests that insert SDE rows by hand after an earlier
 lookup keep working.

Each shape exists for a reason specific to its own caller (see above) - when
adding a new cache, match whichever of these three actually fits your
situation (per-instance-never-hits -> class-level; multiple independent
cache keys -> per-key lock; one whole-computation cache that must never
serve stale-after-a-write results -> single lock + explicit invalidation)
rather than assuming there's one canonical "the" pattern to copy.
