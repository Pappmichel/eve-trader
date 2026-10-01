# Frontend plan: theme and interactivity

As of 2026-10-01. Merges the former `THEME_DRAFT.md` and
`docs/FRONTEND_INTERACTIVITY_PLAN.md` (both removed). Basis: branch
`ccr-e0ccb567-s04qgg`, React 19, Mantine 9, `@tanstack/react-table` 8,
`@tanstack/react-virtual` 3, React Query 5, Recharts 3.
Effort: S = up to half a day, M = 1-2 days, L = 3+ days.

## Status

| Part | State |
|---|---|
| Theme steps 1-3 (Table, Badge, Button, Tabs, NavLink, Paper, Card, inputs, tabular numbers) | implemented, committed |
| Truncated column headers after step 1 | fixed (header font 12 px, letter spacing 0.04em) |
| Theme step 4 (`.et-panel`, scrollbars) | draft, deliberately not applied yet |
| `DataTable` foundation: `onRowClick`, `activeRowId`, `meta.copyable` | implemented, opt-in, covered by tests; only the Shortlist item column uses `copyable` so far |
| Findings B1-B3 (price history endpoint) | open, unverified (no backend in the cloud session) |
| Remaining interactivity items | plan only |

None of this has been checked against a real backend. The theme screenshots
were taken with a mocked API. Rajdhani does not load in the cloud environment
(Google Fonts is blocked), so the font impression cannot be judged there.

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

All three concern `GET /api/trading/history/{type_id}` in
`eve_trader/api/routers/trading.py`. Sparklines, drawer and charts build on
it. All unverified, since there is no backend here.

**B1. Two regions are mixed (likely bug).** The endpoint filters only by
`type_id`. `goonmetrics_history` holds rows for `jita_region_id` and
`reference_region_id`. The response is sorted by date, and `PriceHistory.tsx`
draws a single line from it. Expected: a zigzag between two price levels.
Fix: separate by region, two lines in the frontend.

**B2. Reads the whole table.** `storage.read_table("goonmetrics_history")`,
filtered only afterwards in pandas. `storage.read_goonmetrics_history_for_types`
filters in SQL and already exists (for `do_shortlist_trends`).

**B3. Not scoped to the tenant.** The selection list `/history/type-ids` has
been limited to own items since T3-03, but `/history/{type_id}` returns data
from the shared cache for any guessed id. Fix: check against
`goonmetrics_history_type_ids_for_tenant()`, otherwise 404.

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
  `deploy/README.md`, `README.md` and `.cursor/start.sh`.

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

Only after B1. `PriceHistory.tsx`: Recharts `<Brush>`, quick range 7/30/90
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
independently, skeleton instead of spinner.

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
