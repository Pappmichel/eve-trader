# Portfolio rework: history, total wealth, manual pricing – implementation plan

Status: draft, not yet in the repo · 2026-09-23

Goal: the Portfolio page (`eve_trader/portfolio.py`,
`frontend/src/pages/Portfolio.tsx`) today shows a live snapshot only (four
Trading/Production figures) plus a Scheduler-status card that has nothing to
do with the page's own subject. This rework:

1. Removes the Scheduler-status card and the backup mention entirely.
2. Adds a **daily history** for the existing figures (chart, not just today's
   number).
3. Adds a **Total Wealth** figure – every asset, wallet balance and
   blueprint across every character/corp that explicitly shares data with
   Portfolio – with its own history.
4. Adds **manual item prices**, used only to value items Goonmetrics has no
   quote for in the Total Wealth calculation.

Scope stays Trading + Production for the existing Combined-Value figures (as
already decided); Total Wealth is a separate, broader figure layered on top,
not a replacement for Combined Value.

---

## 1. Remove: scheduler status and backup mention

- `Portfolio.tsx`: delete `SchedulerJobRow`, `schedulerCadence`, the
  "Background Scheduler" card, and the `HintCard` text about backups having
  moved to Admin.
- `api/routers/portfolio.py`: delete `GET /scheduler-status`.
- `portfolioApi.schedulerStatus` removed from `api/client.ts`.
- Nothing else in the app depends on this route (confirm with a grep before
  deleting – `scheduler.get_status()` itself stays, since Admin may still
  want it later; only this page's use of it goes).

---

## 2. Why Total Wealth needs an architecture decision, not just a bigger query

Today, per CLAUDE.md: *"Portfolio is also a tenant-facing tool
(`tool_key "portfolio"`); it reads derived tables, not raw ESI."* Every other
tool (Trading, Production, Doctrine, …) has its own `tool_key` in the ESI
sharing system (`eve_trader/esi_data/`, `docs/ESI_ACCESS_PLAN.md`) – a
character decides, per tool, whether to share Assets/Blueprints/Wallet with
it. "Every asset, wallet and blueprint" requires reading raw ESI-synced
data, which means Portfolio must become a real participant in that sharing
system, not bypass it.

**Decision (confirmed with the user): Portfolio gets its own sharing
capability**, consistent with the existing per-tool model. Total Wealth only
counts what a character has *additionally and explicitly* shared with
Portfolio – sharing Assets with Production does not automatically expose
them to Portfolio too. This is one more checkbox on the Characters page per
data kind, not a bypass of the sharing model.

This does **not** need a new OAuth role-prefix / token namespace. Per
`auth.py`'s own comment, Portfolio deliberately "has no ESI role_key
namespace," and that stays true: Portfolio reuses whichever token an owner
already has from another tool (producer/buyer/seller/doctrine/trader) to
make the actual ESI calls. It only adds a new `tool_key` on the **sharing**
side (`esi_sharing` table, `esi_data/access.py`'s `shared_owner_ids`/
`is_shared`) – the same "who may *read* already-synced rows" layer every
tool already goes through. No new login flow, no new scopes required from
most characters.

**Real limitation, stated in the UI, not hidden:** wallet scopes
(`esi-wallet.read_character_wallet.v1` / `esi-wallet.read_corporation_
wallets.v1`) exist today only on Trading's `buyer`/`seller` tokens. A
character added only through Production (`producer` role) has no wallet
scope at all and will show assets/blueprints but no wallet figure in Total
Wealth, until it's re-authorized with that scope via the existing Characters
page reauth flow (`/api/characters/reauth/start`) – this doesn't require
logging in through a *different* tool, just an additional scope on the same
character. The Portfolio page states this plainly next to any wallet gap
("no wallet scope shared – reauthorize this character to include its ISK
balance").

---

## 3. New ESI data kind: `wallet_balance`

Current wallet sync (`esi_data/fetchers.py:220-289`) only stores transactions
and journal entries; the current balance itself is fetched today only as a
one-off live call for Trading's Transactions tab
(`actions.do_wallet_balance`, never persisted). Total Wealth needs a stored,
per-owner balance.

- `esi_client.py`: reuse the existing `character_wallet_balance`; add
  `corporation_wallet_balance(corporation_id, division, auth_role)` (mirrors
  `character_wallet_balance`'s shape; corp wallets are per division, same as
  `fetch_corporation_wallet`'s existing per-division loop).
- `esi_data/fetchers.py`: new `fetch_character_wallet_balance` /
  `fetch_corporation_wallet_balance`, registered in the `FETCHERS` dict
  under `("wallet_balance", "character")` / `("wallet_balance",
  "corporation")` – a sibling of the existing `("wallet", ...)` entries, not
  a replacement (the existing wallet fetcher keeps owning
  transactions/journal for Trading's reconciliation; this is a separate,
  smaller fetch that only Portfolio's sharing needs to gate).
- `esi_data/registry.py` / `stale.py`: add `wallet_balance` alongside
  `wallet` in the data-kind tables list.
- Schema (`docs/phase1_schema.sql`): new tables `character_wallet_balances`
  (`character_id`, `balance`, `synced_at`) and `corp_wallet_balances`
  (`corporation_id`, `division`, `balance`, `synced_at`), RLS'd like every
  other ESI snapshot table, with `owner_character_id`/`owner_corporation_id`
  columns per the existing sharing-partition convention
  (`esi_access_schema.sql`'s own precedent).
- No new freshness-tier decision needed – `wallet_balance` reuses whichever
  tier `wallet` already uses for that owner.

---

## 4. Portfolio as a sharing participant

- `esi_data/access.py`: `"portfolio"` becomes a valid `tool_key` for
  `shared_owner_ids`/`is_shared` – no special-casing needed, the accessor is
  already generic over `tool_key`.
- Characters page (`esi_data/actions.py`'s `do_set_sharing`): Portfolio gets
  its own sharing checkboxes for `assets`, `blueprints`, and the new
  `wallet_balance` kind, next to the existing per-tool ones. No change to
  `do_set_sharing`'s own shape – it already takes `tool_key` as a plain
  string.
- `access_gate.ALL_TOOL_KEYS` already includes `"portfolio"` (the page-access
  grant exists today) – unchanged; this is a separate axis (data sharing vs.
  page access) that already coexists for every other tool.

---

## 5. Daily snapshot mechanism (backs both the existing figures' history and Total Wealth)

### 5.1 Schema

```sql
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    snapshot_date DATE NOT NULL,
    trading_realized_profit DOUBLE PRECISION NOT NULL,
    trading_average_margin DOUBLE PRECISION NOT NULL,
    trading_daily_profit_volatility DOUBLE PRECISION,   -- NULL, same semantics as today
    trading_trade_count INTEGER NOT NULL,
    production_stock_value DOUBLE PRECISION NOT NULL,
    production_stock_targets_configured BOOLEAN NOT NULL,
    combined_value DOUBLE PRECISION NOT NULL,           -- trading + production, unchanged meaning
    total_wealth DOUBLE PRECISION,                      -- NULL until any owner shares with "portfolio"
    wealth_assets_value DOUBLE PRECISION,
    wealth_wallet_balance DOUBLE PRECISION,
    -- blueprint value = total_wealth - wealth_assets_value - wealth_wallet_balance (not stored separately)
    taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, snapshot_date)
);
ALTER TABLE portfolio_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON portfolio_snapshots;
CREATE POLICY tenant_isolation ON portfolio_snapshots
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
```

One row per tenant per day. A second snapshot on the same day (scheduler
tick + lazy page-load fallback both firing) overwrites the same row via
upsert rather than creating a duplicate. No retention pruning (decision:
unbounded) – one row/day is a handful of doubles, immaterial even after
years.

`combined_value` (Trading + Production) and `total_wealth` are two separate
numbers on purpose: combined_value is "what the two tools' own dedicated
views already show," total_wealth is the broader, opt-in figure. Conflating
them would make the existing Combined Value card silently mean something
different than it does today.

### 5.2 Storage

```python
def upsert_portfolio_snapshot(snapshot_date: date, values: dict) -> None: ...
def load_portfolio_snapshots(since: Optional[date] = None) -> list[tuple]: ...
def latest_portfolio_snapshot_date() -> Optional[date]: ...
```

### 5.3 Snapshot-taking function (`eve_trader/portfolio.py`)

```python
def take_portfolio_snapshot(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Computes portfolio_overview() + total_wealth() and upserts today's
    row. Idempotent - safe to call more than once on the same day."""
    overview = portfolio_overview(cfg)
    wealth = total_wealth(cfg)
    storage.upsert_portfolio_snapshot(date.today(), {**overview, **wealth})
    return {**overview, **wealth}
```

`portfolio_overview()` itself stays unchanged and remains the plain live
read `GET /api/portfolio/overview` calls directly for "today's numbers" on
the stat cards; only the new history/wealth paths touch snapshots.

### 5.4 Two triggers, one idempotent write

**Scheduler job** (primary path when the scheduler is enabled) –
`TradingConfig.portfolio_snapshot_interval_hours: float = 24.0` plus a
`_FIELD_RANGES` entry `(0, None)`. Inside `_check_and_run_due_jobs_for_tenant`
(still gated by `cfg.scheduler_enabled`):

```python
if _hours_since_snapshot(tenant_id) >= cfg.portfolio_snapshot_interval_hours:
    _run_job(tenant_id, "portfolio_snapshot", lambda: portfolio.take_portfolio_snapshot(cfg))
```

**Lazy fallback on page load** (covers the scheduler-disabled default) –
`GET /api/portfolio/overview` changes from a bare
`_wrap(portfolio.portfolio_overview)` to:

```python
def do_get_portfolio_overview() -> dict:
    if storage.latest_portfolio_snapshot_date() != date.today():
        return portfolio.take_portfolio_snapshot()
    return portfolio.portfolio_overview()
```

Both paths call the same `take_portfolio_snapshot` – one write path, not two
implementations that could drift.

### 5.5 History endpoint

`GET /api/portfolio/history?days=<n|all>` →
`storage.load_portfolio_snapshots(since=...)`, mapped to a list of per-day
rows. `days` omitted or `"all"` returns everything (unbounded retention, so
the frontend's range buttons drive the query rather than filtering
client-side against a potentially large result).

---

## 6. Total Wealth calculation (`eve_trader/portfolio.py`)

```python
def total_wealth(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Assets + blueprints + wallet balances across every owner sharing
    with "portfolio", priced via the same Goonmetrics current-price chain
    production/pricing.py uses, manual_item_prices overriding it per
    type_id. Unpriced items are excluded from the total (not counted as 0),
    same "explicit gap over silent understatement" precedent as
    production.engine.stock_value."""
    asset_char_ids, asset_corp_ids = shared_owner_ids("assets", "portfolio", "character"), \
                                      shared_owner_ids("assets", "portfolio", "corporation")
    bp_char_ids, bp_corp_ids = shared_owner_ids("blueprints", "portfolio", "character"), \
                                shared_owner_ids("blueprints", "portfolio", "corporation")
    wallet_char_ids, wallet_corp_ids = shared_owner_ids("wallet_balance", "portfolio", "character"), \
                                        shared_owner_ids("wallet_balance", "portfolio", "corporation")

    assets = storage.load_all_assets(owner_character_ids=asset_char_ids, owner_corporation_ids=asset_corp_ids)
    blueprints = storage.load_owned_blueprints(owner_character_ids=bp_char_ids, owner_corporation_ids=bp_corp_ids)
    wallet_total = storage.sum_wallet_balances(char_ids=wallet_char_ids, corp_ids=wallet_corp_ids)

    type_ids = {row.type_id for row in assets} | {row.type_id for row in blueprints}
    prices = _priced(type_ids)  # Goonmetrics current_prices, manual_item_prices overriding per type_id

    assets_value, priced, unpriced = _value_and_gaps(assets, prices)
    blueprints_value, bp_priced, bp_unpriced = _value_and_gaps(blueprints, prices)

    return {
        "total_wealth": assets_value + blueprints_value + wallet_total,
        "wealth_assets_value": assets_value,
        "wealth_blueprints_value": blueprints_value,
        "wealth_wallet_balance": wallet_total,
        "wealth_priced_items": priced + bp_priced,
        "wealth_unpriced_items": unpriced + bp_unpriced,
        "characters_missing_wallet_scope": [...],  # for the UI hint from section 2
    }
```

**Blueprint pricing caveat, stated in the UI, not silently approximated
away:** a blueprint's ME/TE materially changes what it would actually sell
for, but Goonmetrics has one quote per `type_id`, not per ME/TE level – this
values every copy of a blueprint type at the same market quote regardless of
its own research level. This is a known approximation; the manual-price
override exists partly to let a user correct an individual high-value BPO
(e.g. a well-researched capital BPO) rather than accept the flat market
quote.

**Pricing source:** reuses `production/pricing.py`'s existing Goonmetrics
current-price plumbing (already batches a market's full price list in one
call) rather than the narrower `home_prices`/`jita_prices` (those are scoped
to explicit stock-target type_ids only, by design – Total Wealth needs
*every* type_id actually owned, which can be a much larger, unpredictable
set).

---

## 7. Manual item prices

```sql
CREATE TABLE IF NOT EXISTS manual_item_prices (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL CHECK (price >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id)
);
-- RLS + tenant_isolation policy, same pattern as every other manual_* table.
```

- Storage: `upsert_manual_item_price`, `delete_manual_item_price`,
  `load_manual_item_prices() -> dict[int, float]`.
- Actions (`eve_trader/portfolio.py` gains real mutating logic now, so per
  "actions.py is the one entry point" these are real `do_*` functions, not
  bare passthroughs): `do_list_manual_item_prices()`,
  `do_set_manual_item_price(item_name, price)` (name resolved via the
  existing `search_sde_types` exact-match pattern), `do_remove_manual_item_
  price(type_id)`.
- **Scoped to Portfolio only** (confirmed with the user) – Production's and
  Trading's own pricing chains are untouched. `_priced()` above is
  Portfolio's own function, reading Goonmetrics current prices and this new
  table only – no shared pricing module.
- Router: `GET/POST /api/portfolio/manual-prices`,
  `DELETE /api/portfolio/manual-prices/{type_id}`.

---

## 8. Frontend (`frontend/src/pages/Portfolio.tsx`)

- **Removed:** `SchedulerJobRow`, `schedulerCadence`, the "Background
  Scheduler" card, the backup `HintCard` text, `portfolioApi.
  schedulerStatus`.
- **Kept, now with history:** the four existing stat cards (Combined Value,
  Trading Realized Profit, Trading Average Margin, Trading Daily Profit
  Volatility, Production Stock Value).
- **New: history charts.** A time-range switch (7d / 30d / 90d / all,
  Mantine `SegmentedControl`) driving
  `useQuery(['portfolio', 'history', range], () => portfolioApi.history(range))`.
  Charts with **recharts** (already a dependency – `"recharts": "^3.9.2"`,
  used today in `pages/trading/PriceHistory.tsx`, `NewCandidates.tsx`,
  `RealizedTrades.tsx`, `Shortlist.tsx`; no new dependency, follow those
  pages' existing conventions):
  - **Combined Value** – one line, the headline trend.
  - **Total Wealth** – its own line, kept visually separate from Combined
    Value (different card, own chart) since it's a broader, opt-in figure.
  - **Trading Realized Profit vs. Production Stock Value** – two lines,
    same axis.
  - **Trading Average Margin** and **Daily Profit Volatility** – small
    secondary charts, since these are ratios/spread rather than ISK totals.
- **New: Total Wealth section.** Headline stat card, breakdown row
  (Assets / Blueprints / Wallet), a warning banner listing any registered
  character missing the wallet scope, linking to the Characters page's
  reauth flow.
- **New: Manual Prices section.** A table (type name, price, updated at,
  edit/remove), an add-by-name form (same resolve-by-exact-name pattern as
  other manual override tables elsewhere in the app), and a count of
  currently-unpriced owned item types, linking there directly.
- Empty state: fewer than 2 snapshot days → charts show a hint ("history
  builds up from here – check back tomorrow") instead of an empty/broken
  chart, mirroring `daily_profit_volatility`'s own existing "None below 2
  days of data" precedent.
- **Load the `dataviz` skill before writing any of the new chart code**, per
  its own trigger list – for palette, mark specs and stat-tile conventions,
  so the new charts read as one system with the rest of the app.

---

## 9. Tests

- **ESI data kind:** `wallet_balance` fetcher registration, stale-clearing,
  sharing gate (`is_shared("wallet_balance", "portfolio", ...)`), schema RLS.
- **Wealth calc:** assets/blueprints/wallet only counted for owners sharing
  with `"portfolio"` specifically (a character shared with `"production"`
  only must NOT appear); manual price overrides the Goonmetrics quote;
  unpriced items excluded, not zeroed; `characters_missing_wallet_scope`
  populated correctly.
- **Manual prices:** CRUD, name resolution, negative price rejected.
- **Snapshot storage:** upsert idempotency (same-day call twice → one row,
  latest values win); `load_portfolio_snapshots` ordering and `since`
  filtering; RLS isolation between tenants; new wealth columns present.
- **Scheduler:** `portfolio_snapshot` job respects `scheduler_enabled` and
  `portfolio_snapshot_interval_hours`; not retaken same day by the tick.
- **Router:** scheduler-status route removed (test asserts 404, catching an
  accidental re-add); `GET /overview` takes a snapshot on the first call of
  the day, not on subsequent calls; `GET /history` with `days` unset/`"all"`
  vs. a bounded value; new manual-prices routes.
- **Frontend:** scheduler/backup UI absent; wealth breakdown renders;
  wallet-scope warning banner shows only when applicable; empty-state
  rendering with 0/1 snapshot rows; range switch re-fetches; Playwright
  check per CLAUDE.md's live-verify discipline (script and screenshots
  deleted afterward).

---

## 10. Docs

- **CLAUDE.md**: Portfolio's own paragraph updated – it is no longer purely
  "reads derived tables, not raw ESI"; it now also participates in the ESI
  sharing system as a `tool_key` like any other tool, specifically for Total
  Wealth. State explicitly that this required no new token/role-prefix, only
  a new sharing axis. Note that `portfolio_overview()` stays the live,
  non-snapshotting read while `take_portfolio_snapshot()` is the one write
  path (scheduler + lazy fallback both call it).
- `docs/ESI_ACCESS_PLAN.md`: add `wallet_balance` to the data-kind table, and
  `"portfolio"` to the list of tool_keys with real sharing rows (previously
  the one tenant-facing tool without any).
- Scheduler section: `portfolio_snapshot` added to the list of per-tenant
  jobs alongside `trading_pipeline`/`esi_data_sync` – note it has no
  relationship to the Scheduler-status *display*, which no longer lives on
  this page.

---

## 11. Order / PR split

| # | Scope | Depends on |
|---|---|---|
| 1 | Remove scheduler/backup UI and route from Portfolio | – |
| 2 | Snapshot schema/storage, `take_portfolio_snapshot` (overview half only), scheduler job, config field | – |
| 3 | Lazy fallback in the overview route, history endpoint, range-switch UI, existing-figure charts | 2 |
| 4 | `wallet_balance` ESI data kind (client, fetcher, schema, registry, stale) | – |
| 5 | Portfolio as a sharing tool_key + Characters-page checkboxes | 4 |
| 6 | `manual_item_prices` table, storage, actions, routes | – |
| 7 | `total_wealth()` calculation, wired into `take_portfolio_snapshot` | 5, 6 |
| 8 | Frontend: Total Wealth section, Manual Prices section, wallet-scope banner, wealth history chart | 7 |
| 9 | Docs | 1–8 |

---

## Decision log

| Topic | Decision |
|---|---|
| History scope | All four existing figures get history, not just Production stock value |
| Snapshot cadence | Daily |
| Scheduler dependency | Scheduler job (24h) + lazy fallback on page load, so history fills in even with the scheduler off |
| Retention | Unbounded |
| Presentation | Charts (recharts, already a dependency) |
| Tool scope | Stays Trading + Production for Combined Value; no other tenant-facing tool added to that figure |
| Scheduler card | Removed entirely from Portfolio |
| Backup mention | Removed entirely from Portfolio |
| Total Wealth scope | Assets + wallet balance + blueprints (not market-order escrow) |
| Total Wealth access model | Portfolio gets its own ESI sharing capability (tool_key), consistent with the existing per-tool model – not a bypass |
| Wallet balance sync | New `wallet_balance` ESI data kind through the standard sync/fetcher/registry pipeline |
| Manual prices | One price per type_id, tenant-wide, no quantity/location tiers |
| Manual prices scope | Portfolio's wealth calculation only – not a shared pricing fallback for Production/Trading |
