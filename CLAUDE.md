# CLAUDE.md

Conventions and settled decisions for this repo. README.md covers setup and
usage. This file holds the rules that apply everywhere. The detailed,
per-area decisions are in `docs/claude/` (index below): **read the matching
file before changing code in that area**, so a deliberate decision is not
re-derived or re-litigated.

**If `HANDOFF.md` exists at the repo root, read it first.** It is a
temporary, self-deleting note left when a session ends mid-task, and it
takes priority over re-deriving the current status.

## Where the details are

Code comments cite sections as `CLAUDE.md "<section>"`; the sections kept
their names and moved here:

| File | Sections | Read before touching |
|---|---|---|
| `docs/claude/tools-hubs-pricing.md` | Two tools, one backend (esi_data package, Portfolio / Total Wealth, market hubs, "All hubs", **price sources matrix**) | `esi_data/`, `portfolio*.py`, `hubs.py`, any price lookup |
| `docs/claude/realized-profit.md` | Production realized profit (ledger, fees, T0, contracts, reprocessing, observed fees, fee-skill hints) | `realized/`, `*/realized*.py`, `fee_skill*.py` |
| `docs/claude/architecture-and-config.md` | Architecture (actions.py), Config, Request limits | new routers/actions, config fields, request models |
| `docs/claude/multi-tenant.md` | Multi-tenant Postgres (RLS, roles, tokens, timeouts, threads) | `storage.py`, schema files, background threads |
| `docs/claude/access-and-admin.md` | Tool permissions & Admin (gate, SSO, allowlist) | `access_gate.py`, `auth.py`, `admin.py`, `api/app.py` |
| `docs/claude/character-management.md` | Character Management hub, Discord alerts | `character_management/`, `alerts/`, `char_*` routers |
| `docs/claude/asset-manager.md` | Asset Manager tool | `asset_manager/` |
| `docs/claude/pi.md` | Planetary Industry (PI) tool | `pi/` |
| `docs/claude/locations.md` | Location resolution | `locations/`, any location-name display |
| `docs/claude/caching.md` | Caching pattern | adding or changing a cache |
| `docs/claude/scheduler.md` | Scheduler, Scheduler rework, SDE guards and the daily SDE check | `scheduler.py`, `esi_data/orchestrator.py`, `production/sde.py` |
| `docs/claude/operations.md` | Server templates, Error log and data retention, Backup, Deploy, health and rollback | `deploy/`, `backup.py`, health routes |
| `docs/claude/production.md` | Theoretical ceiling, Real SDE data, Assembled ships, Job-time skill bonus, Special orders, Max buildable, Structure rigs per job category, Manual tracking | `production/` |
| `docs/claude/doctrine.md` | Equivalent module variants, Max assemblable fits, Strategic reserve, Ready to list, Contract Market | `doctrine/`, `sde_equivalence.py` |
| `docs/claude/refining-and-sorting.md` | Sorting paste, Mineral shopping lists (market depth, HiGHS hang) | `refining/`, `module_reprocessing/`, `mineral_supply_lp.py`, `sorting/` |

Longer design history lives in the `docs/*_PLAN.md` files these refer to.
When a decision changes, update the topic file (and this file if the rule is
cross-cutting) in the same change.

## The app at a glance

One FastAPI backend (`eve_trader/api/`), one multi-tenant Postgres store
(`eve_trader/storage.py`), one React/TypeScript frontend (`frontend/src/`).
Tenant-facing tools: Trading (buy in Jita, sell at the player structure
"C-J"), Production (T1/T2/reaction planning for C-J), Doctrine, Ore &
Minerals, Module Reprocessing, Station Trading, Sorting, Portfolio, Asset
Manager, PI and the Character Management hub. Admin is a cross-tenant
superadmin tool.

Settled business rules that are easy to break:
- **Production sells only at C-J, never Jita.** A Jita sales channel was
  built once and reverted. Ask before reintroducing it.
- **Market hubs are per tool** (`*.hub_region_id`; Trading's own is
  `jita_region_id`). The hub list lives once in `hubs.HUBS` and reaches the
  frontend via `GET /api/hubs`; never hard-code a second list.
- **Price sources differ on purpose.** Discovery, live shortlist, Production
  decisions, realized costing, wealth valuation and fee hints each use their
  own source. Check the matrix in `tools-hubs-pricing.md` before assuming two
  same-sounding numbers are interchangeable.
- **"Profit / Day" and `potential_daily_profit` are theoretical ceilings**
  (profit per unit x market-wide daily volume). Do not cap them. The volume
  must be real turnover, never order-book depth (`production.md`).
- **Assembled ships and their fitting are not stock** in any demand
  calculation, but still count for valuation. A new stock reader applies the
  same filter, Production's excluded systems and the strategic-reserve
  hangar (`production.md`, `doctrine.md`).
- **Classification uses real SDE fields**, never name or metaLevel
  heuristics. Extend `production/sde.py` if a field is missing.
- **Days are UTC** (`eve_trader/timeutil.py`). Never `date.today()` or
  `datetime.utcnow()`.
- **Fee journal entries count as cost only when negative.**
- Suggested values (observed fees, fee-skill hints, observed freight) are
  shown with a "Use this value" button. Nothing changes a setting on its own.

## Architecture rules

- `eve_trader/actions.py`, `production/actions.py` and each tool package's
  `actions.py` hold the `do_*` functions: UI-agnostic, plain dicts or
  dataclasses. The CLI and the FastAPI routers call the same `do_*`, so they
  cannot drift. Real logic goes in engine/pricing/storage modules, the `do_*`
  is a thin wrapper, the router thinner still.
- Routers use `_wrap(fn, **kwargs)`, which turns `ActionError` into HTTP 400.
  `ActionError` is the one user-facing error type; a new failure mode raises
  it (or something converted to it), never a bare 500.
- A GET that only returns a stored value may call `storage` directly. Once a
  read needs a decision or transformation, it belongs in a `do_*`.
- `eve_trader/esi_data/` (ESI access: registry, fetchers, orchestrator,
  `read_esi` / `is_shared`) and `eve_trader/locations/` name tools as strings
  and import no tool package. The two documented exceptions in esi_data are
  listed in `tools-hubs-pricing.md`; do not add a third.
- Reaction activity id 9 -> 11 goes through
  `industry_activity.normalize_activity_id`, never an inline `== 9`.
- Config: dataclasses with defaults, overridden by `config.yaml`, then per
  tenant by Settings saves (`tenant_settings`). Everything is validated
  before it is applied (`ConfigError` -> `ActionError`). Settings saves are
  partial (`api/partial.py`). `TRADING_CONFIG` / `PRODUCTION_CONFIG` are
  per-tenant `ConfigProxy` objects. Enum-style string fields need an entry in
  `validate_production_overrides`.
- Every request model is bounded with the types in `api/schemas.py`;
  `tests/test_request_limits.py` enforces it. Response models are not.

## Multi-tenant rules (must follow)

Full text: `docs/claude/multi-tenant.md`.
- Every query goes through `storage.connect()` / `storage.batch_session()`,
  which set `app.tenant_id` and fail closed. Never open a raw psycopg
  connection. `connect_unscoped()` is only for the few tenant-independent
  tables (`tenants`, `tenant_registry_entries`, `tool_grants`,
  `global_structure_names`, `access_allowlist`, `access_requests`, `error_log`).
- The app connects as `eve_trader_app` (`NOBYPASSRLS`); `postgres` is only
  for DDL and `pg_dump`. Never give the app role `BYPASSRLS`. No database
  credentials in code (`EVE_TRADER_PG_DSN`).
- A new per-tenant table gets `tenant_id ... DEFAULT current_setting(
  'app.tenant_id', false)::uuid`, RLS enabled and a `tenant_isolation`
  policy; copy an existing table from `docs/phase1_schema.sql`, and grant the
  app role explicitly.
- A brand-new `docs/*_schema.sql` must be added everywhere schema files are
  applied: `deploy/deploy.sh`, `deploy/README.md`, `README.md`,
  `.cursor/start.sh`. Grep for an existing schema filename to find them all.
- To act as a tenant in new code use `tenant_scope.enter_tenant`. Worker
  threads (`ThreadPoolExecutor`, `threading.Thread`) do not inherit
  contextvars: wrap the callable in `storage.with_current_tenant(...)` after
  entering the tenant.
- A new background entry point, or a route running one long statement,
  enables `storage.long_running()` (60 s statement timeout otherwise).
- Refresh tokens before opening a batch, not inside it (pool of 10).
- Stored SSO tokens are encrypted (`token_crypto.py`) and fail closed.

## Tool keys

Two authorization layers: `tenant_id` (RLS, whose data) and tool grants
(`tool_grants`, which tools a character may use). Details, SSO and the
allowlist: `docs/claude/access-and-admin.md`. `DEFAULT_TOOL_KEYS` is every
key except `admin` and `module_reprocessing`. `frontend/src/toolKeys.ts`
mirrors the list; `tests/test_claude_md_tool_keys.py` fails when a key is
missing from this table.

| Key | Router prefix | Page | Default grant | ESI-consuming | Documented in |
|---|---|---|---|---|---|
| `trading` | `/api/trading/` | `/trading` | yes | yes | `docs/claude/tools-hubs-pricing.md` |
| `production` | `/api/production/` | `/production` | yes | yes | `docs/claude/production.md`, `docs/claude/realized-profit.md` |
| `doctrine` | `/api/doctrine/` | `/doctrine` | yes | yes | `docs/claude/doctrine.md` |
| `refining` | `/api/refining/` | `/ore` (Ore & Minerals) | yes | no | `docs/claude/refining-and-sorting.md` |
| `station_trading` | `/api/station-trading/` | `/station-trading` | yes | yes | `docs/claude/tools-hubs-pricing.md` |
| `sorting` | `/api/sorting/` | `/sorting` | yes | yes | `docs/claude/refining-and-sorting.md` |
| `portfolio` | `/api/portfolio/` | `/portfolio` | yes | yes | `docs/claude/tools-hubs-pricing.md`; `docs/PORTFOLIO_REWORK_PLAN.md` |
| `admin` | `/api/admin/` | `/admin`, `/admin/sde-preview` | no | no | `docs/claude/access-and-admin.md` |
| `characters` | `/api/characters/` | `/character-management/characters` | yes | no | `docs/ESI_ACCESS_PLAN.md`, `docs/CHARACTER_MANAGEMENT_PLAN.md` |
| `module_reprocessing` | `/api/module-reprocessing/` | `/modules` | no | no | `docs/claude/refining-and-sorting.md` |
| `char_info` | `/api/char-info/` | `/character-management/info` | yes | yes | `docs/claude/character-management.md` |
| `char_skills` | `/api/char-skills/` | `/character-management/skills` | yes | yes | `docs/claude/character-management.md` |
| `char_mail` | `/api/char-mail/` | `/character-management/mail` | yes | yes | `docs/claude/character-management.md` |
| `char_notifications` | `/api/char-notifications/` | `/character-management/notifications` | yes | yes | `docs/claude/character-management.md` |
| `char_contacts` | `/api/char-contacts/` | `/character-management/contacts` | yes | yes | `docs/claude/character-management.md` |
| `char_skill_plans` | `/api/char-skill-plans/` | `/character-management/skill-plans` | yes | yes | `docs/claude/character-management.md` |
| `char_alerts` | `/api/char-alerts/` | `/character-management/alerts` | yes | yes | `docs/claude/character-management.md` ("Discord alerts") |
| `pi` | `/api/pi/` | `/pi` | yes | yes | `docs/claude/pi.md`, `docs/PI_PLAN.md` |
| `asset_manager` | `/api/asset-manager/` | `/assets` | yes | yes | `docs/claude/asset-manager.md`, `docs/ASSET_MANAGER_PLAN.md` |

A route that reads another tool's data (e.g. Doctrine fittings from
`char_skills`, `production_buy_list` from PI) also checks that tool's grant
against `request.state.tool_keys`.

## Testing conventions

- Full suite: `pytest -n auto` from the repo root. Keep it green before
  calling anything done.
- Tests use their own databases (`eve_trader_test`, one per xdist worker,
  built by `tests/pg_helpers.py` from `deploy/deploy.sh`'s schema order),
  never the `eve_trader` dev database. A test must not depend on rows or
  `lru_cache` state another test left behind.
- Router tests monkeypatch the imported `actions` module objects, which only
  works because routers do `from ... import actions`. Keep that import style.
- A function with a module-level cache needs an autouse fixture that resets
  it between tests.
- `tests/conftest.py` fails any test that sends a real HTTP request; mock
  ESI / Goonmetrics.
- Lint: `ruff check eve_trader tests deploy` (rules `F` and `B904`). Fix
  findings instead of adding `# noqa`; a `raise` inside `except` takes
  `from e` or `from None`.
- Frontend type-check: `npx tsc -b` (`tsc -p .` checks nothing here).

## Live-verify before declaring done

Unit tests are necessary, not sufficient. For a backend change, call the
real endpoint on `localhost:8000` (`Invoke-RestMethod`) and read the
response. For a frontend change, load it in a real browser (throwaway
Playwright `_verify_*.mjs`, screenshot, check console and network errors),
then delete the script and screenshots. Never leave verification artifacts
in the repo.

## Environment and repo conventions

- Windows 11 / PowerShell. Backend: `uvicorn eve_trader.api.main:app --port
  8000` without `--reload`; restart it by hand after backend changes.
  Frontend: Vite on `:5173`.
- `config.yaml` is hand-maintained and untracked: never overwrite it with
  test data, and restore any value a live `/settings` check changed.
- The GitHub repo (https://github.com/Pappmichel/eve-trader) is **public**:
  never commit server addresses, player data or open security findings.
- Commit locally after completed work without asking. Never `git push`
  unless the user asks for it in that turn.
- **No Claude/Anthropic attribution anywhere**: no `Co-Authored-By: Claude`
  or session lines in commits, no "Generated with Claude Code" footer in PRs,
  issues or comments, whatever a tool template suggests. The `commit-msg`
  hook (`git config core.hooksPath .githooks`) and CI reject them.
- **Everything committed is English** (code, comments, docs, commit and PR
  text), whatever language the chat is in. Translate German leftovers only
  in files you are already editing.
- Branch names: kebab-case with a type prefix (`feature/`, `fix/`, `docs/`,
  `refactor/`, `chore/`), named after the work; rename auto-generated names
  before pushing; delete branches once merged.
- Delegate algorithmically heavy work (LP formulations, correctness-critical
  math) to an Opus subagent without asking, and report it afterwards.
- Fonts are bundled via `@fontsource` (the CSP allows no Google hosts); the
  CCP notice is the `CcpNotice` component; licenses are in
  `THIRD_PARTY_NOTICES.md`.
- `SYNC.md` maps manual syncs to the sibling project `eve-trader-local`.
  `../eve_trader_electron` (an old packaged copy) is a separate project and
  not on this machine.

## Deferred, not rejected

A Contract-Scanner (deferred 2026-07-14) is legitimate future scope. Do not
start it without asking.
