# CLAUDE.md

Conventions and settled decisions for working in this repo. README.md covers
setup/usage; this file covers *how the code is organized and why*, so you
don't have to re-derive it (or accidentally re-litigate a decision that was
already made deliberately) from scratch.

**If `HANDOFF.md` exists at the repo root, read it first** - it's a
temporary, self-deleting note left when a session ends mid-task (e.g.
continuing on a different computer with no access to this machine's Claude
memory) and takes priority over re-deriving current status from scratch.

## Two tools, one backend

- **Trading**: buys in Jita, sells at a private player structure ("C-J").
- **Production**: Tech I/II/Reaction manufacturing planning for the same C-J
  structure - buy-vs-build, stock targets, invention.

Both share one FastAPI backend (`eve_trader/api/`), one SQLite store
(`data/eve_trader.db`, via `eve_trader/storage.py`), and one React/TypeScript
frontend (`frontend/src/`). `eve_trader/portfolio.py` and
`eve_trader/scheduler.py` are the only modules that deliberately span both.

**Production sells only at C-J, never Jita** - this was implemented once
(a Jita-comparison feature) and explicitly reverted after confirming with the
user that freighting finished Production goods to Jita isn't part of this
tool's business model. Don't reintroduce Jita as a Production sales channel
without asking first.

## Architecture: actions.py is the one entry point

`eve_trader/actions.py` and `eve_trader/production/actions.py` hold every
`do_*` function - UI-agnostic, no framework imports, return plain
dicts/dataclasses. Both `cli.py` and the FastAPI routers
(`api/routers/*.py`) call the *same* `do_*` functions, so the CLI and the web
app can never drift apart. If you add a feature, the real logic goes in
`engine.py`/`pricing.py`/`storage.py`; the `do_*` action is a thin
orchestration wrapper; the router is thinner still.

Routers use a `_wrap(fn, **kwargs)` helper that catches `ActionError` and
converts it to an HTTP 400 - `ActionError` is the one user-facing error type
across the whole app. A new failure mode should raise `ActionError` (or a
narrower exception the caller converts to `ActionError`), not a raw
exception that would otherwise surface as a bare 500.

## Config: dataclasses + config.yaml, validated before applied

`TradingConfig`/`ProductionConfig` (`eve_trader/config.py` /
`eve_trader/production/config.py`) are dataclasses with built-in defaults,
overridden by `config.yaml` at load time and by Settings-page saves at
runtime (`save_config_overrides`). Both paths run
`validate_config_overrides` (type-checks every field against its declared
type) - and Production additionally runs `validate_production_overrides`
(enum-checks `*_structure_type`/`*_rig_tier` against
`production/constants.py`) - *before* anything is written to disk or applied
to the live config object, so a bad value never lands half-applied. Raises
`ConfigError`; `do_update_settings` in both actions modules catches it and
re-raises as `ActionError` (kept separate from `ActionError` itself to avoid
a circular import - `config.py` is imported *by* `actions.py`, not the other
way around).

If you add a new config field, it's automatically type-checked - no extra
work needed unless it's an enum-style string field, in which case add it to
`validate_production_overrides` (Production-specific checks stay out of the
shared `config.py` - that module is imported *by* `production/config.py`,
reaching back into `production/constants.py` from the shared module would be
a layering violation).

## Testing conventions

- Router tests (`tests/test_api_routers.py`) monkeypatch the already-imported
  `actions`/`production_actions`/`portfolio`/`scheduler` **module objects**,
  not individual functions - this only works because every router does
  `from ... import actions` (module-level import), never
  `from .actions import do_thing`. If you add a router that imports
  differently, its tests need a different monkeypatch target.
- Any function with a module-level cache (see `discover_build_candidates`
  below) needs an autouse fixture resetting it between tests, or later tests
  will silently reuse an earlier test's monkeypatched result.
- Full suite: `pytest` from the repo root. Keep it green before calling
  anything done - it currently runs in a few seconds, there's no excuse to
  skip it.

## "Live-verify before declaring done" discipline

Passing unit tests is necessary but not sufficient. Before calling a
backend change done, hit the real running endpoint (`Invoke-RestMethod`/
`Invoke-WebRequest` against `localhost:8000`) and read the actual response -
not just the mocked unit-test path. Before calling a frontend change done,
load it in a real browser (Playwright via a throwaway `_verify_*.mjs`
script - screenshot it, check console/network errors, then delete the
script and screenshot afterward; don't leave verification artifacts in the
repo). This caught real bugs during development (e.g. a settings save that
looked fine in isolated unit tests but needed checking against the actual
Pydantic request-validation layer to know whether the new backend
validation was even reachable via HTTP).

## Caching pattern

`esi_client.py` established the pattern used everywhere else that caches an
expensive call: a plain `time.time()`-based TTL (`self._x_cache`,
`self._x_cache_at`), no external caching library. `discover_build_candidates`
(`production/engine.py`) follows the same pattern at module level, plus
explicit invalidation (`invalidate_discover_cache()`) from every action that
actually changes its result set (Settings save, stock target add/remove,
decryptor change, SDE refresh) - TTL alone would let a just-changed Setting
serve stale results for the rest of the TTL window, which would be a real
correctness problem, not just a performance one.

## Scheduler

`eve_trader/scheduler.py` is a stdlib-only (`threading`, no APScheduler)
background daemon thread, started from `api/app.py`'s FastAPI lifespan,
**off by default** (`TradingConfig.scheduler_enabled`). It runs three jobs
(Trading pipeline, Production ESI sync, DB/config backup - see
`_check_and_run_due_jobs`), each reusing an existing "when did this last
happen" source instead of separate scheduler-specific persistence:
`storage.esi_sync_state` (already written by `do_pipeline`/`do_sync_esi`) for
the first two, the newest file's own mtime under `data/backups/` (`backup.
list_backups()`) for the third. A manual run/backup from the UI correctly
counts either way and pushes back the next scheduled one. Adding a fourth
scheduled job means adding one interval field to `TradingConfig` (plus a
`_FIELD_RANGES` entry, `(0, None)`, in `config.py`) and one
`if _hours_since(...) >= cfg.x: _run_job(...)` line in
`_check_and_run_due_jobs` - no other wiring needed.

## Backup

`eve_trader/backup.py`'s `create_backup()` zips `data/eve_trader.db`,
`config.yaml`, and `data/tokens.json` (OAuth tokens - included so a restore
doesn't require re-authenticating every character) into a timestamped
`.zip` under `data/backups/`, pruning down to `MAX_BACKUPS` (14) automatically.
Uses SQLite's own online backup API (`sqlite3.Connection.backup`), not a
plain file copy - safe regardless of concurrent writers, unlike copying the
`.db` file directly. Reachable two ways: the "Backup Now" button on the
Portfolio page (always available, regardless of `scheduler_enabled`), and
the scheduler's own `backup_interval_hours`-gated job (see above, opt-in).

## "Theoretical ceiling" figures - not bugs

`potential_daily_profit` (Production's Build Candidates) and "Profit / Day"
(Trading's Shortlist) are deliberately `profit_per_unit x total daily
sell_volume` - the value of an item's *entire* day of market turnover, not a
claim about what one seller could personally capture. A tiny-volume,
huge-per-unit item (a capital hull, a faction module) can show an enormous
number - that's mathematically correct for "what's the whole market worth,"
confirmed deliberate with the user after live-testing surfaced exactly this
case. Don't cap, filter, or "fix" these values without asking first.

## Real SDE data drives classification, not heuristics

Item categorization (`classify_activity` in `production/engine.py`) uses
real SDE fields (`meta_group_id` for the "Faction"/"Officer"/"Storyline"/
"Deadspace" categories - meta_group_id 4/5/3/6 respectively, all mapped via
one `meta_group_labels` dict at the end of `classify_activity` - and
invention-recipe lookups for Tech II) rather than guessing from name
patterns or `metaLevel` alone - a past bug (Machariel/Nestor miscategorized
as Tech II) came from exactly that kind of heuristic (`metaLevel >= 2`
catching Faction ships too). All four of these meta-group labels share the
same ME0/TE0, non-researchable treatment (`constants.ACTIVITY_MODS`) - none
of them get Tech I's owned-BPO-preference/research-baseline treatment (see
`_activity_mods`). When adding another classification category, prefer an
SDE column already fetched by `production/sde.py`'s `refresh_sde()` over a
new heuristic; if the SDE doesn't already carry the field you need, extend
`refresh_sde()` to fetch it (see the `invMetaTypes.csv` merge that added
`meta_group_id` for precedent) rather than approximating.

## Environment specifics

- **Git repository, GitHub remote.** `git init` + first commit + a GitHub
  remote (`origin`, https://github.com/Pappmichel/eve-trader, currently
  private) were set up 2026-08-16 as part of preparing the project for
  publication - this repo is no longer "no VCS safety net." Workflow: commit
  locally after completed work without asking (cheap, reversible, purely
  local); never `git push` without the user explicitly asking for it in that
  turn, since that's what actually publishes to the shared remote.
- Windows 11 / PowerShell. Backend: `uvicorn eve_trader.api.main:app --port
  8000` (no `--reload` in the usual dev setup here - restart manually after
  backend changes, e.g. `Stop-Process -Id <pid> -Force` then relaunch).
  Frontend: Vite dev server on `:5173`.
- `config.yaml` is hand-maintained and not under version control - never
  overwrite it with synthetic test data; tests use their own
  `TradingConfig()`/`ProductionConfig()` instances, not the real file, and
  any live HTTP verification against `/settings` should restore/no-op the
  real values afterward.

## Deferred, not rejected

Contract-Scanner, Discord alerts, and a PI (Planetary Interaction) calculator
were explicitly discussed and deferred (not rejected) as of 2026-07-14 -
they're legitimate future scope, just not started. Don't start on these
without asking first.

## Windows packaging lives in a sibling repo

`../eve_trader_electron` (a sibling of this directory, **not** a
subdirectory of it) is a separate, already-packaged copy of this app -
PyInstaller-bundled backend (`backend_entry.py`, `eve-trader-backend.spec`,
`build_backend/`, `dist_backend/`) plus an Electron shell, last touched
2026-07-14. It is not kept in continuous sync with this repo - treat it as
its own project, not a build target of this one, unless the user says
otherwise. Do not assume packaging work is unstarted or "deferred until
feature-complete" - that framing was true earlier in this project's history
but is now stale; the packaging already happened once, over there.
