# Architecture, config and request limits

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

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

The rule above is about *mutating* actions and anything with real business
logic: those always go through a `do_*` function so the CLI and the web app
can't drift apart on what a change actually does. A **GET-only, read-a-
value-straight-out-of-storage** endpoint is a different, much more common
exception than the two below - most routers have at least one (`production.py`
alone has around eight: `/sde/counts`, `/sde/item-names`, `/stock-targets`,
`/manual-stock`, `/manual-build-buy`, `/selected-decryptors`,
`/category-locations`, plus `/plan`/`/asset-plan` reading the in-process
`_last_plan`/`_last_asset_plan` cache; `trading.py` has a similar handful for
its shortlist/candidate/history/realized-trades reads). This is fine and not
a rule violation: there is no business logic to duplicate in a bare
`storage.read_x()` call, so a `do_*` wrapper would only add a pass-through
layer with nothing of its own to keep in sync. It stops being fine the
moment a "read" needs a decision, transformation, or a cross-check beyond
"return this row" - that belongs in `do_*`, not in the router. (A `do_*`-
name-only audit of this repo - e.g. when comparing against a port - will
systematically miss this whole GET-read layer; check the routers directly,
not just `actions.py`'s function list, if you need a complete inventory of
what a repo's API surface actually exposes.)

Two narrow, deliberate exceptions call something other than a `do_*`
function directly for *mutating or cross-cutting* work specifically - not
places where the rule was missed, but don't extend either without the same
reasoning: `api/routers/portfolio.py`'s `GET /wealth` calls
`portfolio.total_wealth()` directly (real logic - pricing, sharing lookups -
not a bare passthrough), same as `GET /overview`/`GET /history` calling
`portfolio.do_get_portfolio_overview()`/`do_get_portfolio_history()` (these
two *do* carry the `do_` prefix, unlike `total_wealth()`/`portfolio_
overview()`/`take_portfolio_snapshot()` - the prefix is a naming
convenience this module reaches for when it helps the CLI/router-parity
story, not a hard rule every function here must follow) - `portfolio.py`
is the cross-cutting module that deliberately spans both tools (see "Two
tools, one backend" above), so there's no natural single-tool `do_*` home
to put any of these in. Characters `do_*` live in `eve_trader/esi_data/`
(see `docs/ESI_ACCESS_PLAN.md`), not in `portfolio.py`.
`cli.py`'s `tenant import-tokens`/`migrate-sqlite` commands call `storage`/
`sqlite_migration` directly - both are genuinely one-time, operator-run
commands with no web/API equivalent at all, so there's no router on the
other side of them to keep in sync with. `tenant create`/`add-entry`/`list`
are *not* this exception anymore - they call `storage.create_tenant`/
`add_tenant_registry_entry`/`list_tenants` too, but the Admin tool
(`eve_trader/admin.py`'s `do_*` functions, see "Tool permissions & Admin"
below) calls the exact same `storage.py` functions, so both paths stay in
sync by construction.

## Config: dataclasses + config.yaml, validated before applied, resolved per-tenant

`TradingConfig`/`ProductionConfig` (`eve_trader/config.py` /
`eve_trader/production/config.py`) are dataclasses with built-in defaults,
overridden by `config.yaml` at load time (the base values every tenant
starts from) and by Settings-page saves at runtime, persisted per-tenant to
Postgres's `tenant_settings` table (`save_tenant_config_overrides` -
`save_config_overrides`/YAML-writing was retired in the multi-tenant
migration's Phase 2). Both paths run `validate_config_overrides`
(type-checks every field against its declared type) - and Production
additionally runs `validate_production_overrides` (enum-checks
`*_structure_type`/`*_rig_tier` against `production/constants.py`) -
*before* anything is written or applied to the live config object, so a bad
value never lands half-applied. Raises `ConfigError`; `do_update_settings`
in both actions modules catches it and re-raises as `ActionError` (kept
separate from `ActionError` itself to avoid a circular import - `config.py`
is imported *by* `actions.py`, not the other way around).

`TRADING_CONFIG`/`PRODUCTION_CONFIG` (the names everything else imports and
reads) are `ConfigProxy` objects, not plain dataclass instances - they
forward every attribute read/write to whichever instance a `contextvars.
ContextVar` currently resolves to. `tenant_scope.enter_tenant(tenant_id)`
is what actually resolves and `.set()`s that per-request/per-job-tick (see
"Multi-tenant Postgres" below) - a bare dataclass instance would leak one
tenant's Settings-page save into every other tenant's live config, since
they'd all be reading/writing the exact same object.

**Settings saves are partial.** The six settings routes (Trading, Production,
Station Trading, Ore & Minerals, Module Reprocessing, Doctrine) take an update
model built by `api/partial.py` (`partial_model`: every field optional, same
types) and save `model_dump(exclude_unset=True)`: only fields the client sent
change, an explicit null on a nullable field is a value. Validation that spans
fields runs on stored values plus the update (`validate_production_merged`,
Doctrine's hangar groups via `_with_saved_hangar_fields`). The settings pages
send only the fields changed against the loaded data (`frontend/src/api/
changedFields.ts`) and each direct-save site (toggles, hub pickers) sends only
its own field. The boot load (`api/app.py` `_load_default_tenant_config`)
applies all seven scopes (trading, production, doctrine, pi, refining,
station_trading, module_reprocessing) for the default tenant.

config.yaml is flat and every tenant config class reads it by field name, so
one key can hit several classes (`hub_region_id` sets five hubs).
`config.warn_yaml_key_collisions` logs one warning per such key at boot
(class list: `config_scopes.tenant_config_classes()`, the one scope-to-class
map; behaviour unchanged). The market slug fields (`structure_market_slug`,
`home_market`, `pi_price_structure_slug`) are checked in
`validate_config_overrides` (`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, empty/None
allowed, case-sensitive) and quoted in the Goonmetrics URL; a stored invalid
slug is ignored with a warning on tenant load, never a startup failure. A
stored override key that is no longer a field is ignored and logged once per
(scope, key) by `warn_unknown_override_keys` (names only); `eve-trader admin
prune-settings` (dry run by default, `--apply`, per tenant via
`enter_tenant`) removes them.

If you add a new config field, it's automatically type-checked - no extra
work needed unless it's an enum-style string field, in which case add it to
`validate_production_overrides` (Production-specific checks stay out of the
shared `config.py` - that module is imported *by* `production/config.py`,
reaching back into `production/constants.py` from the shared module would be
a layering violation).

## Request limits

Every **request** model is bounded; response models never are (a long stored
value would turn a read into a 500). `api/schemas.py` holds the shared
`Annotated` types: `NameText` (100), `ShortText` (200), `LongText` (10,000),
`PasteText` (500,000), `IdList` (50,000 ids), `ShortTextList` (1,000 x
`ShortText`), `SkillLevels` (0-5, at most 50) and `bounded_json(max_bytes,
max_depth=12)` for free-form `Any`/dict bodies (`BoundedTemplate` 256 KiB,
`BoundedDesign`/`BoundedPlan` 64 KiB, `BoundedViewQuery` 16 KiB and 30 keys,
`BoundedPiSettings` 100 keys). Narrower existing limits stay. A new request
field needs one of these types; `tests/test_request_limits.py` walks every
route's body models and fails on an unbounded str, list, dict or `Any`.

Settings models double as response models, so they carry no limits themselves:
`api/partial.py` `partial_model()` adds them to the derived update model
(`bound_annotation`: str -> `ShortText`, id lists 50,000, text lists and
dicts bounded) and re-attaches a top-level `Annotated` constraint that
pydantic lifts into `FieldInfo.metadata`. Pass `overrides={field: annotation}`
for a field that needs a tighter shape. `api/body_limit.py` is a pure ASGI
middleware (not `BaseHTTPMiddleware`) that answers 413 `request_too_large` for
an `/api/*` body over 1 MiB, declared by `Content-Length` or counted while
streamed; it sits inside `RequestIdMiddleware` and outside
`AccessGateMiddleware`. nginx's own body limit is separate (deployment).
