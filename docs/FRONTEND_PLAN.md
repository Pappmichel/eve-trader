# Frontend plan: theme and interactivity

As of 2026-10-02. Merges the former `THEME_DRAFT.md` and
`docs/FRONTEND_INTERACTIVITY_PLAN.md` (both removed). Basis: `dev` after
PRs #224, #225 and #226, React 19, Mantine 9, `@tanstack/react-table` 8,
`@tanstack/react-virtual` 3, React Query 5, Recharts 3.
Effort: S = up to half a day, M = 1-2 days, L = 3+ days.

## Status

| Part | State |
|---|---|
| Theme steps 1-4 (Table, Badge, Button, Tabs, NavLink, Paper, Card, inputs, tabular numbers, `.et-panel` corners on Landing cards, thin scrollbars) | implemented, committed |
| Truncated column headers after step 1 | fixed (header font 12 px, letter spacing 0.04em) |
| `DataTable`: `onRowClick`/`activeRowId`, keyboard navigation (arrows, Home/End, PageUp/PageDown, Enter, `/`), `meta.copyable`, `meta.hoverCard`, `meta.trackChanges`, saved views (`tableId`), column reordering in the Columns menu | implemented, opt-in, covered by tests (B.1, B.4, B.5, B.6, B.8, B.9, B.13 columns) |
| Trading Shortlist: status chips (B.3), detail drawer with `?item=` (B.1), saved views incl. page filters, change flash, hover card, copyable item name | implemented |
| Price History reads `?item=<type_id>` (target of the drawer link) | implemented |
| Notification center: `notify()` wrapper, bell in tool headers and Landing (B.14) | implemented; all former `notifications.show` call sites switched |
| Job progress bar (`JobProgress`) in the Trading layout (B.5) | implemented |
| `EditableNumberCell` extracted to `components/EditableCell.tsx`; Enter saves, Escape discards; Doctrine target editor has the same keys (B.7) | implemented |
| Spotlight "Run:" commands with confirmation dialog (B.9) | implemented; first Trading only, later extended to every tool (see below) |
| `DataTable` generic extras: `rowDetail` (row click opens a drawer with every column, incl. hidden ones) and `meta.filterable` (exact-value filter icon + removable chips, part of saved views) | implemented; `rowDetail` enabled on ~25 main tables, `copyable` on item-name columns, `filterable` on category columns |
| `JobProgress` bars on Admin (structure names), SDE preview, Doctrine sync, Build Candidates | implemented |
| `DataTable` columns: drag a header onto another to reorder (native HTML5 drag and drop, no dependency), drag the right edge of a header to resize (also arrow keys on the handle, double click resets one column, "Reset order"/"Reset widths" in the Columns menu); both persist per `tableId` and are part of saved views. Touch users reorder with the arrows in the Columns menu. | implemented (B.13 columns); `tableId` added to the main tables so order, widths and Views work there |
| Same table features everywhere: `DataTable` defaults (row detail drawer on, automatic `tableId` from page path + column headers so order/widths/Views persist, copy icon on name columns, filter icon on category/status columns; each opt-out with `rowDetail={false}` / `meta.copyable=false` / `meta.filterable=false`) | implemented for all 53 DataTable tables |
| Raw Mantine tables converted to `DataTable` (SDE preview, mineral shopping lists, ore family skills, fitting deviations, station trading skills, skill plans, contacts, calendar, notifications, skills overview/groups/queue/matrix, character info lists and overview, character slots, doctrine skill check) | implemented; short tables shrink to their content, tall-cell tables use a larger `rowHeight` |
| Deliberate exceptions still on plain tables: the three sharing matrices on the Characters page (interactive access control, grouped headers) and the per-character alert toggles on the Discord Alerts page (a list of controls, not data) | open by decision |
| `DataTable` export menu (replaces the single CSV button): download CSV (international: comma, dot), Excel `.xlsx` (real numbers, bold header, widths; writer `write-excel-file` loaded on demand), JSON; copy as tab-separated table, Markdown table, and an in-game list (`Item<TAB>Qty`, only when the table has an item column and a quantity column; `meta.exportRole` overrides the header guess). Raw values, current filter/sort/visible columns; text cells that look like spreadsheet formulas are neutralised; file names default to the page path plus the date. | implemented for every `DataTable` (formats in `components/dataTableExport.ts`) |
| Merged with `dev` (column pinning, buy-hub labels, breakeven column, tap-target CSS): the Columns menu keeps `dev`'s `Menu.Item component="div"` rows and adds the move arrows; export and saved views follow the pinned order/state; dropping an unpinned column on a pinned header is ignored; drawer labels follow the configured hub | resolved |
| Partial items extended to more pages: hover summary card is now the default on every name column (`meta.hoverCard` overrides, `false` opts out); change flash is now the default for every table with `getRowId` (up to 2,000 rows; `meta.trackChanges` narrows/excludes) and `getRowId` was added to Market Status, Margin, Unlisted Stock, Build Candidates, Candidates, New Candidates; the three duplicate inline-edit components (Special Orders, Blueprints x2) now use the shared `EditableNumberCell` (Enter saves, Escape discards; `min`/`max`/`step`/`width`); Spotlight "Run:" commands now cover Production, Doctrine, Station Trading, Ore & Minerals and Module Reprocessing besides Trading; corner accents on tool overview and portfolio cards | implemented |
| Test fixes after a local run (2026-10-02): unknown `POST /api/...` returned 405 instead of 404 while `frontend/dist` was mounted (also on Linux); hover summary formatted numbers with the browser locale instead of en-US; Module Reprocessing and Station Trading tables missing from `sqlite_migration.KNOWN_NON_MIGRATED_TABLES` (the drift test depended on test order); Windows-only test issues | fixed, full backend and frontend suites green |
| Finding B3 | dropped: conflicts with the documented T3-03 decision (see B.0) |
| B1, B2, B4 | fixed (PR #225): history endpoint split by region with indexed queries, Price History draws two lines, shortlist refresh saves both regions' history, trends limited to the last 30 calendar days. Verified on the test server: before, no active shortlist item had history newer than 2026-09-21; after two refreshes 2,414 items have current Jita history and 1,850 have a trend |
| GitHub #221 Breakeven price | fixed (PR #225): `breakeven_buy_price` = highest hub buy price that still breaks even after broker fee, freight and structure sale fees (was the structure price) |
| GitHub #222 Trade hubs | fixed (PR #225): Trading's buy hub is picked on the Shortlist page and affects Trading only; every other tool has its own hub setting; Doctrine, Production, Ore & Minerals and Module Reprocessing also offer "All hubs (best per item)" with a "Best hub" column and a shared per-hub freight table (`/api/hubs/freight`); Station Trading stays on one hub |
| GitHub #223 Region names | fixed (PR #225): `sde_regions` from the SDE refresh, searchable `RegionSelect`; shows "Region <id>" until the next SDE refresh |
| Stale `index.html` after a deploy | fixed (PR #225): `Cache-Control: no-cache` on `index.html`, immutable on hashed assets; verified through nginx on the test server |
| Goonmetrics history only covers a few regions (The Forge, Insmother, Delve; not Amarr, Dodixie, Rens) | fixed (PR #226): untracked regions are read from ESI daily history, cut to the same 30-day window |
| B.2 sparklines | unblocked |
| B.10 charts | implemented: Price History has a 7/30/90d/All range picker (ranges beyond the stored history are disabled), a zoom Brush, the reference region's real name, and compare mode (up to 5 items, one line each, indexed to 100 at the first common date, buy hub or reference region); Portfolio already had the same range picker, unchanged |
| B.11 dashboard tiles | open, needs a new Production `/kpis` endpoint |
| B.12 auto refresh, B.13 priority drag and drop | deferred (low benefit / needs schema); re-checked 2026-10-02, still right |

Clicked through with a logged-in session on the test server (2026-10-03,
`dev` at 359131f, Playwright with a session cookie minted on that server):
all 64 pages load without console errors or failed API calls, except
Production's Invention and Logistics tabs right after a backend restart -
their build list lives in memory only, so they answer 400 "No build list
computed yet"; Logistics already showed a hint, Invention now does too.
Slow pages there (Stock Targets' stock value, Station Trading shortlist,
Doctrine shopping list, shortlist trends: 7-14 s) are mostly the test VM
(1 vCPU, 1 GB): on production the trends take 1.5 s and the stock value
1 s warm, about 10 s on the first call while prices are fetched. Still not
checked: the Excel export in real Excel.

## Guidelines

- **Mantine stays.** No switch to Tailwind/shadcn (see decision below).
  Customize through `theme.ts` (component overrides) and `index.css`.
- **`DataTable` is the lever.** `frontend/src/components/DataTable.tsx` is used
  by every table page; each extension benefits all pages.
- **Opt-in props.** New props are optional; a page that does not set them
  behaves as it does today (like `isError`, `dataUpdatedAt`).
- **Virtualization:** only ~20 rows are mounted. Reference rows via
  `getRowId`, not DOM nodes; scroll with `virtualizer.scrollToIndex`.
- **Fixed row height** (`rowHeight`, ellipsis): cell content must fit in 36 px.
- **Stable row id** (`getRowId`) is mandatory for per-row state (drawer,
  highlighting, inline edit); otherwise the state jumps to another row on sort.
- **Checks per step:** `npm run lint`, `npx tsc -b`, `npm test` in
  `frontend/`; `pytest` for backend parts; live check with a throwaway
  Playwright script and before/after screenshots, check the console, delete the
  script afterwards (rule from `CLAUDE.md`).
- **Commits carry no Claude attribution** (rule from `CLAUDE.md`).
- **Everything that goes into the repo is written in English** (code,
  comments, docs, commit messages).
- **Toasts go through `notify()`** (`frontend/src/notify.ts`), never
  `notifications.show` directly, so they also land in the bell history.

## Decision: keep Mantine

The Tailwind/shadcn templates from the research (TailAdmin, Shadcn Admin,
Windmill, Admin One) would introduce a second styling system next to Mantine
and touch every page. The generic Mantine look can be fixed with theme
overrides. MUI (Material look is hard to adapt, parts of the DataGrid are
paid), Chakra, Ant Design and Radix Themes offer no advantage that justifies
the migration. Optionally borrow individual ideas from `CSS-sci-fi-ui` for
panel borders. Do not use: ARWES (no longer maintained), SCIFICN/UI (requires
shadcn/Tailwind).

---

# Part A: Theme

Goal: make the existing "trade terminal" theme more cohesive without changing
the palette (`COLORS`), fonts or dark-only mode.

## A.1 Step 1 (implemented) and open remainder

Added in `frontend/src/theme.ts` and `frontend/src/index.css`: table headers
as Rajdhani uppercase with letter spacing and divider lines, squarer badges,
bold Rajdhani button text, equal-width table digits
(`font-variant-numeric: tabular-nums`).

**Open:** uppercase is wider. With the fixed column widths (`DataTable`,
`colgroup`) headers are truncated more, e.g. "DAYS UNTIL AU…" and "TREND (3D
VS 3…". Fix, one of two:
1. Header font size from 13 to 12 px and `letterSpacing` to 0.04em.
2. Widen the affected columns in the pages (Shortlist: "Days Until Auto-…",
   "Trend").
Recommendation: 1 first, then check in a real browser with the real Rajdhani
font, which is narrower than the fallback font in the screenshots.

## A.2 Step 2: Tabs and NavLink

```ts
Tabs: { styles: {
  tab: { fontFamily: 'Rajdhani, sans-serif', letterSpacing: '0.04em', color: COLORS.textDim,
         '&[data-active]': { color: COLORS.accent, borderColor: COLORS.accent } },
  list: { borderColor: COLORS.border } } },
NavLink: { styles: {
  root: { borderRadius: 4, '&[data-active]': { background: 'rgba(53, 208, 186, 0.10)', color: COLORS.accent } },
  label: { fontFamily: 'Rajdhani, sans-serif', letterSpacing: '0.03em' } } },
```

## A.3 Step 3: cards, paper, inputs

```ts
Paper: { defaultProps: { radius: 'sm', withBorder: true },
  styles: { root: { background: COLORS.surface, borderColor: COLORS.border } } },
Card: { defaultProps: { radius: 'sm', withBorder: true, padding: 'md' },
  styles: { root: {
    background: `linear-gradient(180deg, ${COLORS.surface2} 0%, ${COLORS.surface} 100%)`,
    borderColor: COLORS.border, transition: 'border-color 120ms ease',
    '&:hover': { borderColor: COLORS.accent } } } },
TextInput:   { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
NumberInput: { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
Select:      { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
```

## A.4 Step 4: panel decoration and scrollbars (`index.css`)

```css
.et-panel { position: relative; border: 1px solid #24313F; border-radius: 4px;
  background: linear-gradient(180deg, #182230 0%, #121922 100%); }
.et-panel::before, .et-panel::after { content: ''; position: absolute; width: 10px; height: 10px;
  border: 1px solid #35D0BA; opacity: .7; pointer-events: none; }
.et-panel::before { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
.et-panel::after { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
* { scrollbar-width: thin; scrollbar-color: #24313F #0B0F14; }
```
Apply `.et-panel` only to Landing cards and page headers.

## A.5 Notes

- Mantine's `styles` API applies plain inline styles, so nested selectors
  (`'&:hover'`, `'&[data-active]'`) cannot work there. Static values live in
  `theme.ts`; hover and active states live in `index.css` via
  `.mantine-Card-root:hover`, `.mantine-Tabs-tab[data-active]` and
  `.mantine-NavLink-root[data-active]`. Tab letter spacing was dropped
  because it made the Trading tab row wrap at 1500 px.
- No new fonts, no palette change, no animation beyond the 120 ms border
  transition.
- Order: A.1 remainder → A.2 → A.3 → A.4. Before/after screenshots of Landing
  and Shortlist for each step.

---

# Part B: Interactivity

## B.0 Clarify findings first (phase 0)

B1-B3 concern `GET /api/trading/history/{type_id}` in
`eve_trader/api/routers/trading.py`; B4 concerns where its data comes from.
Sparklines, drawer and charts build on both. Checked against the code on
2026-10-02. All four are resolved (B1, B2, B4 fixed in PR #225, B3 dropped);
the text below is kept as the record of what was found.

**B1. Two regions are mixed (confirmed bug).** The endpoint filters only by
`type_id`. `goonmetrics_history` holds rows for `jita_region_id` and
`reference_region_id` (both written by `history_backtest.py`'s candidate
search). The response is sorted by date, and `PriceHistory.tsx` draws a single
`avg_price` line from it, so it zigzags between two price levels.
Fix: separate by region, two lines in the frontend.

**B2. Reads the whole table (confirmed).** `storage.read_table("goonmetrics_history")`,
filtered only afterwards in pandas. Fix together with B1 as one query per
region, `WHERE region_id = ? AND type_id = ?`: that uses the primary key
`(region_id, type_id, date)`, the table's only index.
`read_goonmetrics_history_for_types` (`WHERE type_id IN (...)`) cannot use it,
because `type_id` is not the leading column.

**B3. Not scoped to the tenant: dropped.** Leaving `/history/{type_id}`
reachable for any known id is a documented decision (T3-03, docstring of
`storage.goonmetrics_history_type_ids_for_tenant`): the price data is public
market data, only the item *listing* was tenant-private. Revisit only if that
decision changes.

**B4. Shortlist price history is never refreshed (confirmed bug, new).** The
only writer of `goonmetrics_history` is the candidate search
(`do_find_new_candidates` → `history_backtest.find_new_import_candidates`,
`history_sink=storage.save_goonmetrics_history`), and it skips every item that
is already active on the shortlist (`history_backtest.py`, `existing_item_ids`).
The shortlist refresh fetches reference-region history for the same items
(`actions.py`, `gm.price_history_chunked(cfg.reference_region_id, ...)` for
Profit/Day) but does not save it. So once an item is on the shortlist its
stored history stops at the day it was added. Affected today: Price History
and the Shortlist "Trend" column (`do_shortlist_trends` uses the last stored
days, not the last calendar days). Planned B.2/B.10 would show the same stale
data. Fix: in the shortlist refresh, fetch both regions and pass the points to
`storage.save_goonmetrics_history` (one extra Goonmetrics request per batch for
Jita). The table is shared across tenants, so every tenant benefits.
Confirmed against real data on 2026-10-02 (test server, database copy of the
old production server, 2,457 active shortlist items): in both regions no
active shortlist item has stored history newer than 2026-09-21, while 282
other items in the reference region have rows up to the same day (newest
2026-10-02). Most shortlist items stop between 2026-08-03 and 2026-09-17;
253 (Jita) and 276 (reference region) have no stored history at all.

## B.1 Row detail on click (drawer) · M

Feasibility good, first version needs no backend. Today `Table.Tr` has no
click handler and there is no drawer yet (modals exist).
- `DataTable`: `onRowClick?: (row) => void`, `activeRowId?: string`
  (`data-active`, `cursor: pointer` only when set). Ignore clicks on buttons,
  inputs and links (`closest('button, input, a, [role=button]')`).
- New `components/RowDetailDrawer.tsx` (Mantine `Drawer`, right side ~420 px,
  full width on mobile). Keep the open row in the URL (`?item=<type_id>`).
- First page: Trading Shortlist: Jita price, import cost, landed cost, net
  sell, profit per unit, margin, volume, average daily volume, mini history
  (after B.0). Actions only through existing endpoints; there is no watchlist.
  Instead a link "Open in Price History" (needs `PriceHistory.tsx` to read the
  selection from the URL, S).
- Then Build Candidates, Market Status, Stock Targets (S each).

## B.2 Sparklines · M

Feasibility medium. 50 rows with one request each would be too much, and each
request currently reads the whole table (B2).
- Backend: `GET /api/trading/history/sparklines`, returns for this tenant's
  shortlist items the last ~28 days per region
  (`{type_id: {"jita": [...], "ref": [...]}}`), via
  `read_goonmetrics_history_for_types`. Pure read, may live in the router; if
  aggregation is added (e.g. margin), put it in a `do_*`.
- Needs B4 first. Select by calendar date (last 28 days before today), not
  the last 28 stored rows, so stale data shows as missing instead of as a
  current trend.
- Register the route before `/history/{type_id}` (like `/history/type-ids`),
  otherwise the dynamic route swallows it.
- Frontend: `components/Sparkline.tsx` as inline SVG (`<polyline>`, ~80×24 px),
  not Recharts per row. Color by direction, tooltip via `meta.cellTitle`. One
  query per page (`['trading','sparklines']`).

## B.3 Filter chips · S to M

- Shortlist (S): clicking a status badge calls `setSelDecisions([decision])`,
  a second click resets; badge as `UnstyledButton` with `aria-pressed`.
- Generic (M): tanstack-table `columnFilters`, columns via
  `meta: { filterValue }`; chip bar with "x" and "Reset all".
  `getFilteredRowModel` is already wired up.
- Badge handler with `stopPropagation()` so it does not open the drawer (B.1).

## B.4 Copyable cells · S

- `useClipboard` from `@mantine/hooks`. Columns with `meta: { copyable: true }`
  show a copy icon on hover (not the whole cell clickable).
- Raw value (`cell.getValue()`), short "Copied" state in the icon instead of a
  toast.
- `navigator.clipboard` needs HTTPS or `localhost`; hide the icon when
  `!window.isSecureContext`.

## B.5 Loading and success states · M

- `components/JobProgress.tsx` (Mantine `Progress`) fed from the
  `useBackgroundJob` status (`batch/total_batches`, otherwise indeterminate),
  placed in the tool layout under the header.
- Briefly highlight changed rows: `DataTable` remembers previous values per
  `getRowId` via `useRef`, only for columns with `meta.trackChanges`, opt-in
  and only on pages with under ~2,000 rows (Candidate Universe has 45k+).

## B.6 Saved views · M (local), M-L (server)

- Stage 1 (recommended, local): a view = `{name, sorting, columnVisibility,
  columnOrder, columnFilters, globalFilter}` under `datatable:<id>:views`,
  "Views" menu next to "Columns". Page filters outside `DataTable` (Shortlist
  `MultiSelect`s) are either moved into `columnFilters` (B.3 generic) or wired
  through `extraViewState`. `try/catch` as today.
- Stage 2 (only if needed): server storage needs a new per-tenant table with
  RLS. Per `CLAUDE.md`, a new schema file must be added to `deploy/deploy.sh`,
  `deploy/README.md`, `README.md` and `.cursor/start.sh`. The new table also
  goes into `sqlite_migration.KNOWN_NON_MIGRATED_TABLES`, and its schema
  fixture into `tests/test_sqlite_migration_table_drift.py`, otherwise the
  drift test fails.

## B.7 Inline editing · S

`EditableNumberCell` in `StockTargets.tsx` (issue #16) already has the pattern
(local draft, check mark, `PATCH /api/production/stock-targets/{type_id}`).
Lift it to `components/EditableCell.tsx`, switch DoctrineDetail
(`TargetEditor`) to it. Enter saves, Escape discards, **no** automatic save on
blur (deliberate, see the Shortlist cap comment). Further candidates only with
a PATCH endpoint (manual stock, listed quantities, logistics categories).
Settings pages stay forms (`validate_config_overrides` checks the whole
object).

## B.8 Hover cards · S (low priority)

`HoverCard` on the name column, ~400 ms delay, content only from row and
sparkline data (no request per hover), off on touch (`(pointer: coarse)`).
Overlaps with B.1; afterwards only worthwhile for very dense tables.

## B.9 Keyboard and Spotlight actions · M + M

- Table: focusable container, `activeIndex`, arrows/Enter/Home/End,
  `virtualizer.scrollToIndex`, `aria-activedescendant` (row ids from
  `getRowId`). `/` focuses the filter via `useHotkeys` (with `tagsToIgnore`).
- Spotlight (`QuickNav.tsx`, navigation only so far): second group "Actions"
  ("Refresh Shortlist", "Reconcile Trades", "Sync ESI") with the same grant
  filtering (`TOOL_KEYS`). Actions go through the same hooks as the buttons
  (`useAction`, `useTradingPipelineJob`) so toasts, invalidation and job lock
  stay identical. Actions of tier `live` (ESI/Goonmetrics) get a confirmation
  dialog.

## B.10 Interactive charts · M

Only after B1, B2 and B4. `PriceHistory.tsx`: Recharts `<Brush>`, quick range 7/30/90
days (`SegmentedControl`), two lines for Jita and the reference region,
multi-select (`useQueries`, normalized to index 100, at most ~5 items).
"Click a data point to jump to the table" is dropped (the page has no table);
instead a link from Shortlist/drawer (B.1). The portfolio history
(`/api/portfolio/history` accepts `days`) gets the same range picker.

## B.11 Dashboard tiles · M to L

Landing cards get an optional KPI line, only when the grant is present
(`gateStatus.tools`). Trading: existing `/kpis`. Portfolio: total wealth from
`/overview` (triggers a snapshot on the first call of the day, do not hang it
on polling). Production: `/market-status` and `/plan` are too expensive, so a
new lean `GET /api/production/kpis` (counts only). Each tile loads
independently, skeleton instead of spinner. With the access gate off,
`/api/gate/status` has no `tools`; show every tile then.

## B.12 Automatic refresh · S, low benefit

The scheduler is deliberately off (`CLAUDE.md`), data practically only changes
through the user's own actions, which already invalidate their queries
(`useAction`). No global `refetchInterval`. Instead check that
`useBackgroundJob` invalidates everywhere after a job ends, and enable
`refetchOnWindowFocus` only for selected queries. A "New data available" bar
only makes sense once the scheduler runs again.

## B.13 Drag and drop · M (columns), L (priorities)

Columns: tanstack-table `columnOrder`, dragging via `@dnd-kit/*` (~15 kB gz,
the only possible new dependency) or without a dependency using up/down arrows
in the "Columns" menu. Persist like B.6. Priorities (stock targets):
`stock_targets` has no order column; it would need `sort_order`, a migration, a
PATCH field and changes to all readers. Defer until it is clear what the order
should control.

## B.14 Notification center · M

42 direct `notifications.show` calls. A central wrapper `src/notify.ts`
(forwards to Mantine, writes into a small store via `useSyncExternalStore`),
switch the calls once, oxlint `no-restricted-imports` against regressions. A
bell in the header with an unread counter and the last ~50 entries, storage in
`sessionStorage`. Errors also stay visible as a toast. Whether successes only
land in the center is open.

---

# Order (overall)

Phases 0-5 and the B.0 findings are done (see Status). Next, as of
2026-10-02:
1. B.12 as a "new data available" notice: the scheduler runs again since
   2026-10-02, so data now changes without the user's own actions.
2. B.2 sparklines (load only the visible rows' history) and B.10 charts
   (offer only ranges the stored history covers).
3. B.11 dashboard tiles (Production needs a lean `/kpis`; read Portfolio
   from the stored snapshot, not `/overview`).

Original order:

| Phase | Content | Reason |
|---|---|---|
| 0 | A.1 remainder (column headers) · verify and fix B1-B3 | Bugs before new features; charts and drawer build on them |
| 1 | Theme A.2 and A.3 (tabs, NavLink, cards, inputs) · `DataTable`: `onRowClick`/`activeRowId`, keyboard, copy icon | Foundation for B.1, B.4, B.9 |
| 2 | Shortlist drawer (B.1) · Shortlist status chips (B.3) · Spotlight actions (B.9) | Most visible effect, no schema |
| 3 | Sparkline endpoint and component (B.2) · charts (B.10) · theme A.4 | Needs phase 0 |
| 4 | Saved views, local (B.6) · generic column filters · column order in the menu | Builds on phase 1 |
| 5 | Notification center (B.14) · job progress and change highlighting (B.5) | Independent |
| 6 | Dashboard tiles with production `/kpis` (B.11) | New endpoint |
| later | Generalize inline edit (B.7), hover cards (B.8), auto refresh (B.12), drag and drop (B.13) | Low added value or schema needed |

New dependencies: none required (optionally `@dnd-kit/*` for B.13).
