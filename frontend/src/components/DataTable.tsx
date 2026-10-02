import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type Column,
  type ColumnDef,
  type ColumnFiltersState,
  type ColumnOrderState,
  type ColumnPinningState,
  type ColumnSizingState,
  type FilterFn,
  type Row,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Table, ScrollArea, Text, Skeleton, Group, TextInput, Menu, Checkbox, Button, ActionIcon, Stack, CopyButton, HoverCard, Popover, UnstyledButton, Drawer, Badge, Tooltip } from '@mantine/core'
import { IconSearch, IconDownload, IconColumns, IconX, IconAlertTriangle, IconRefresh, IconCopy, IconCheck, IconChevronUp, IconChevronDown, IconBookmark, IconTrash, IconFilter, IconPin, IconPinFilled } from '@tabler/icons-react'
import { relativeTime } from '../format'
import { notify } from '../notify'
import {
  firstRolesOnly, hasGameList, inferRole, toCsv, toGameList, toJson, toMarkdown, toTsv, toXlsxBlob, type ExportMatrix,
} from './dataTableExport'
import {
  KEYBOARD_RESIZE_STEP, MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH,
  loadColumnOrder, loadColumnSizes, loadViews, moveColumn, moveColumnTo, saveColumnOrder, saveColumnSizes, saveViews, upsertView,
  type SavedView,
} from './dataTableViews'

// GitHub issue #52 used to force-hide columns marked `meta: { mobileHide:
// true } }` below Mantine's `sm` breakpoint, on top of whatever the user
// picked in the Columns menu - meant to spare a phone screen from a long
// horizontal scroll, but confirmed real bug: it overrode the Columns menu
// entirely (a "visible" checkbox the user could check but that never
// actually showed the column on mobile), and simply left most of a dense
// table unreachable rather than reachable-by-choice. Removed - every column
// is now reachable by horizontal scroll or the Columns menu on any screen
// size, same as desktop.
declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    // Native hover title for the cell. When omitted, DataTable stringifies
    // string/number cell values (the default "full text on hover" for
    // ellipsis-truncated cells). A column that renders custom content and
    // wants a richer title (or none) should set this instead of fighting
    // the Td's default `title`.
    cellTitle?: (row: TData, value: TValue) => string | undefined
    // Shows a copy button on hover that copies the raw cell value (not the
    // formatted text, so "1234567" rather than "1,234,567 ISK"). Opt-in per
    // column; hidden when the browser has no clipboard (non-secure context).
    copyable?: boolean
    // Extra content shown in a hover card (after a short delay) on a fine
    // pointer; never on touch, where a row click / drawer is the way in.
    hoverCard?: (row: TData) => ReactNode
    // Briefly highlights a row when this column's value changed after a data
    // refetch. Needs `getRowId` on the table; keep to small tables.
    trackChanges?: boolean
    // Shows a filter icon on hover; clicking it filters the table to rows
    // with exactly this cell value (shown as a removable chip above the table).
    filterable?: boolean
    // Set to false to leave this column out of the row detail drawer
    // (`rowDetail`). Columns without a text header (action columns) are
    // always left out.
    detail?: boolean
    // Tooltip shown on the column header (explains an abbreviation or estimate).
    headerHint?: string
    // Marks the item-name / quantity column for the "In-game list" export when the
    // header text alone is not enough to tell (otherwise inferred from the header).
    exportRole?: 'item' | 'qty'
  }
}

// Generic sortable, row-virtualized table.
//
// Three structural fixes vs. the old Streamlit implementation this replaces:
// 1. Sort correctness - columns always keep their real numeric accessor;
//    formatting (isk()/qty()/pct()) only affects the `cell` renderer, never
//    what's sorted on. The old bug baked thousands-formatted strings
//    ("1.234.567") directly into cells, which sorted as text.
// 2. Row virtualization (@tanstack/react-virtual) - Streamlit's dataframe
//    was backed by a canvas grid that only ever rendered visible rows
//    regardless of row count. A plain HTML <table> has no such limit - the
//    Candidate Universe view (45k+ rows) would otherwise render 45k <tr>
//    elements and hang the tab. Only the ~15-20 rows in the visible
//    scroll window are ever mounted here.
// 3. Stable layout (confirmed real bug via screenshots: column widths visibly
//    shifted while scrolling, row heights varied 1-3 lines depending on which
//    item names happened to be mounted) - a plain <table> with no fixed
//    widths auto-sizes columns from whatever's *currently in the DOM*, and
//    since virtualization only ever mounts the visible rows, that set (and
//    therefore every column's width) changes as you scroll. Fixed by giving
//    every column an explicit width (col.getSize(), colgroup below) and
//    forcing single-line, ellipsis-truncated cells (full text on hover via
//    `title`) so every row is exactly `rowHeight` regardless of content -
//    matching the virtualizer's fixed estimateSize instead of drifting from
//    it (which otherwise causes overlapping/gapped rows during scroll too).
//
// GitHub issue #15 ("all tables should be sortable/filterable, columns
// hideable, tables exportable") added a small toolbar (global text filter,
// column-visibility menu, CSV export) to this one shared component instead
// of each page reimplementing it - every page using DataTable gets all
// three for free, same as sorting already worked. `tableId` (optional)
// persists column visibility to localStorage per table; omit it and
// visibility still works, just doesn't survive a remount/reload.
//
// GitHub issue #76: `isError`/`onRetry` are the read-side equivalent of
// useAction's own error toast for writes - before this, a failed useQuery
// left `data` as `[]`/undefined and every page just fell through to the
// ordinary "no data yet" empty state, completely silent about the fact
// that a fetch actually failed (confirmed: only 1 of 37 pages checked
// `isError` at all, and there was nowhere to render it even if they did).
// Optional and additive - a page that doesn't pass `isError` behaves
// exactly as before.
interface DataTableProps<T> {
  data: T[]
  columns: ColumnDef<T, any>[]
  maxHeight?: number
  emptyLabel?: string
  rowHeight?: number
  isLoading?: boolean
  isError?: boolean
  errorMessage?: string
  onRetry?: () => void
  tableId?: string
  exportFilename?: string
  // GitHub issue #78: how fresh is this table's data - pass a query's own
  // `dataUpdatedAt` (react-query already tracks this per query, nothing new
  // to compute) to show a small relative-time label in the toolbar.
  // Optional/opt-in - a page that doesn't pass it renders exactly as before.
  dataUpdatedAt?: number
  // Without this, tanstack-table's default row.id is the row's *index* in
  // the current (sorted/filtered) data array - stable enough for read-only
  // display, but not for a cell that owns its own editing state (a
  // NumberInput's local useState) keyed only by React's rendering position:
  // sorting/filtering can put a *different* underlying row at the same
  // index across a re-render, and since the index-based id doesn't change,
  // React reuses the same component instance/state instead of resetting it
  // - the exact "stale value saved against the wrong item" bug class
  // StockTargets.tsx's CurrentStockInput already hit once (see its own
  // comment) at a different call site. Pass a real per-row identity
  // (usually `(row) => String(row.type_id)`) for any table with editable
  // cells.
  getRowId?: (row: T) => string
  // Opt-in row click (e.g. to open a detail drawer). Clicks on buttons, inputs
  // and links inside a row are ignored so editable/action cells keep working.
  // `activeRowId` (a `getRowId` value) highlights the currently open row.
  onRowClick?: (row: T) => void
  activeRowId?: string
  // Lets a saved view also capture/restore page-owned filter state that lives
  // outside the table (e.g. a status MultiSelect). Needs `tableId`.
  extraViewState?: { value: unknown; apply: (value: unknown) => void }
  // Clicking a row opens a side drawer listing every column of that row with
  // its formatted value - including columns hidden in the table. Zero per-page
  // code; pass `title` to override the default (the first column's value).
  // Works alongside `onRowClick` (both run).
  // On by default; pass `rowDetail={false}` where a page opens its own detail view.
  rowDetail?: boolean | { title?: (row: T) => ReactNode }
}

// Exact-match column filter (the table's default would be a substring match).
const exactFilter: FilterFn<any> = (row, columnId, value) => String(row.getValue(columnId)) === String(value)

// Header row + a little slack: tables with few rows shrink to their content
// instead of reserving `maxHeight`.
const TABLE_CHROME_HEIGHT = 64
const CHANGE_FLASH_MS = 2500
const KEY_PAGE_STEP = 10

// Elements inside a row that handle their own clicks.
const INTERACTIVE_SELECTOR = 'button, input, textarea, select, a, [role="button"], [role="checkbox"]'

function CopyCell({ value }: { value: string }) {
  return (
    <CopyButton value={value} timeout={2000}>
      {({ copied, copy }) => (
        <ActionIcon
          className="et-copy"
          size="xs"
          variant="subtle"
          color={copied ? 'accent' : 'gray'}
          aria-label={copied ? 'Copied' : 'Copy value'}
          onClick={(e) => { e.stopPropagation(); copy() }}
          style={{ marginLeft: 4, verticalAlign: 'middle' }}
        >
          {copied ? <IconCheck size={12} /> : <IconCopy size={12} />}
        </ActionIcon>
      )}
    </CopyButton>
  )
}

const SKELETON_ROWS = 8

// navigator.clipboard only exists in secure contexts (HTTPS / localhost); on a
// plain-HTTP LAN install the copy button would silently do nothing, so hide it.
// Hover cards only make sense with a real hover-capable pointer.
const canHover = typeof window !== 'undefined'
  && typeof window.matchMedia === 'function'
  && window.matchMedia('(pointer: fine)').matches

// Defaults so every table behaves the same without per-page wiring. A column's
// own `meta.copyable` / `meta.filterable` (true or false) always wins.
const COPY_HEADER = /^(item|item name|name|ship|product|blueprint|material|mineral|skill|fitting|character|corporation|location|system|station)$/i
const FILTER_HEADER = /^(category|status|decision|source|group|activity|state|recommendation|market|kind|role|type)$/i

export function columnDefaults(header: unknown, accessorKey: unknown): { copyable: boolean; filterable: boolean } {
  const label = typeof header === 'string' ? header.trim() : ''
  const key = typeof accessorKey === 'string' ? accessorKey : ''
  const nameLike = /(^|_)name$/.test(key)
  return {
    copyable: COPY_HEADER.test(label) || nameLike,
    // "Type" is a name column when it is backed by *_name (e.g. type_name).
    filterable: FILTER_HEADER.test(label) && !nameLike,
  }
}

function hashString(value: string): string {
  let h = 5381
  for (let i = 0; i < value.length; i++) h = ((h << 5) + h + value.charCodeAt(i)) | 0
  return (h >>> 0).toString(36)
}

function autoTableId<T>(columns: ColumnDef<T, any>[]): string {
  const signature = columns
    .map((c) => (typeof c.header === 'string' ? c.header : ((c as { id?: string }).id ?? (c as { accessorKey?: string }).accessorKey ?? '')))
    .join('|')
  const path = typeof window !== 'undefined' ? window.location.pathname : ''
  return `auto:${path}:${hashString(signature)}`
}

const canCopy = typeof window !== 'undefined'
  && window.isSecureContext !== false
  && typeof navigator !== 'undefined'
  && !!navigator.clipboard

function cellText(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return undefined
}

function columnLabel(header: unknown, id: string): string {
  return typeof header === 'string' ? header : id
}

function loadPersistedVisibility(tableId: string | undefined): VisibilityState {
  if (!tableId) return {}
  try {
    const raw = localStorage.getItem(`datatable:${tableId}:columns`)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {} // corrupt/unavailable storage - fall back to "everything visible", never crash the page over this
  }
}

// Confirmed real request (user feedback on Trading's Shortlist, 2026-10-01):
// scrolling right to see Market Volume loses sight of the Item column - the
// user wants to pin a column so it stays put regardless of horizontal scroll.
// Left-pinning only (no right-pin UI) - that is the actual use case (keep an
// identifying column, usually the leftmost one, in view), and it keeps the
// sticky-offset math to one side. Same persistence shape/pattern as
// columnVisibility above, own localStorage key so the two never collide.
function loadPersistedPinning(tableId: string | undefined): ColumnPinningState {
  if (!tableId) return {}
  try {
    const raw = localStorage.getItem(`datatable:${tableId}:pinning`)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {} // corrupt/unavailable storage - fall back to "nothing pinned", never crash the page over this
  }
}

// Cumulative left offset (sum of the pinned widths before it) for every
// column in `pinnedColumns`, in their pinned order - tanstack-table v8.21
// doesn't ship a getStart()/getAfter() helper (added in a later version), so
// this is the same sum any caller of one would need to do.
function pinnedLeftOffsets(pinnedColumns: Column<any, any>[]): Map<string, number> {
  const offsets = new Map<string, number>()
  let running = 0
  for (const col of pinnedColumns) {
    offsets.set(col.id, running)
    running += col.getSize()
  }
  return offsets
}

export function DataTable<T>({
  data,
  columns,
  maxHeight = 480,
  emptyLabel = 'No data.',
  rowHeight = 36,
  isLoading = false,
  isError = false,
  errorMessage,
  onRetry,
  tableId: tableIdProp,
  exportFilename: exportFilenameProp,
  getRowId,
  dataUpdatedAt,
  onRowClick,
  activeRowId,
  extraViewState,
  rowDetail = true,
}: DataTableProps<T>) {
  // Every table remembers its column layout and saved views. Without an
  // explicit `tableId` the key is derived from the page path plus the column
  // headers, so two different tables never share state by accident.
  const tableId = tableIdProp ?? autoTableId(columns)
  // Download name: the page's own name (or the page path), plus today's date.
  const exportFilename = `${exportFilenameProp
    ?? (typeof window !== 'undefined' ? window.location.pathname.replace(/^\/+|\/+$/g, '').replace(/\//g, '-') : '')
    ?? ''}`.replace(/[^\w.-]+/g, '-') || 'export'
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() => loadPersistedVisibility(tableId))
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(() => loadColumnOrder(tableId))
  const [views, setViews] = useState<SavedView[]>(() => loadViews(tableId))
  const [viewName, setViewName] = useState('')
  const [cursorId, setCursorId] = useState<string | null>(null)
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>(() => loadColumnSizes(tableId))
  const [dragColumnId, setDragColumnId] = useState<string | null>(null)
  const [dropColumnId, setDropColumnId] = useState<string | null>(null)
  const resizingRef = useRef(false)
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  // The clicked Row object itself, not just its id: without a `getRowId` the id is
  // the row's index, which a re-sort or refetch would point at a different row.
  const [detailRow, setDetailRow] = useState<Row<T> | null>(null)
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set())
  const uid = useId()
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const filterRef = useRef<HTMLInputElement | null>(null)
  const [columnPinning, setColumnPinning] = useState<ColumnPinningState>(() => loadPersistedPinning(tableId))

  // Ticks every 30s so the relative-time label below ("2m ago" -> "3m ago")
  // stays live without a full data refetch - cheap (one re-render, no
  // network) and only runs at all when a page actually opts in.
  const [, forceTick] = useState(0)
  useEffect(() => {
    if (!dataUpdatedAt) return
    const id = setInterval(() => forceTick((n) => n + 1), 30_000)
    return () => clearInterval(id)
  }, [dataUpdatedAt])

  // Re-derive from localStorage (or reset to "everything visible") whenever
  // this instance switches to a *different* tableId - without this, a page
  // that reuses one <DataTable> for several tabs (same component instance,
  // changing tableId as the user switches tabs) would keep showing the
  // previous tab's hidden-columns selection.
  useEffect(() => {
    setColumnVisibility(loadPersistedVisibility(tableId))
    setColumnOrder(loadColumnOrder(tableId))
    setColumnSizing(loadColumnSizes(tableId))
    setViews(loadViews(tableId))
    setColumnPinning(loadPersistedPinning(tableId))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId])

  useEffect(() => {
    saveColumnOrder(tableId, columnOrder)
  }, [tableId, columnOrder])

  useEffect(() => {
    saveColumnSizes(tableId, columnSizing)
  }, [tableId, columnSizing])

  // Flash rows whose tracked columns changed since the previous `data`. The
  // first load (no previous snapshot) flashes nothing.
  const prevSignatures = useRef<Map<string, string> | null>(null)
  useEffect(() => {
    const tracked = columns.filter((c) => c.meta?.trackChanges)
    if (!getRowId || tracked.length === 0) return
    const valueOf = (c: ColumnDef<T, any>, row: T): unknown => {
      if ('accessorFn' in c && c.accessorFn) return c.accessorFn(row, 0)
      if ('accessorKey' in c) return (row as Record<string, unknown>)[c.accessorKey as string]
      return undefined
    }
    const next = new Map<string, string>()
    for (const row of data) next.set(getRowId(row), tracked.map((c) => String(valueOf(c, row))).join('|'))
    const prev = prevSignatures.current
    prevSignatures.current = next
    if (!prev) return
    const changed = new Set<string>()
    for (const [id, sig] of next) if (prev.has(id) && prev.get(id) !== sig) changed.add(id)
    if (changed.size === 0) return
    setChangedIds(changed)
    const t = setTimeout(() => setChangedIds(new Set()), CHANGE_FLASH_MS)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  useEffect(() => {
    if (!tableId) return
    try {
      localStorage.setItem(`datatable:${tableId}:columns`, JSON.stringify(columnVisibility))
    } catch {
      // quota exceeded / unavailable (private browsing) - column visibility
      // just won't persist this session, never crash the page over it
    }
  }, [tableId, columnVisibility])

  useEffect(() => {
    if (!tableId) return
    try {
      localStorage.setItem(`datatable:${tableId}:pinning`, JSON.stringify(columnPinning))
    } catch {
      // see the columnVisibility effect above - same reasoning
    }
  }, [tableId, columnPinning])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter, columnVisibility, columnOrder, columnFilters, columnSizing, columnPinning },
    enableColumnResizing: true,
    columnResizeMode: 'onChange',
    onColumnSizingChange: setColumnSizing,
    onColumnOrderChange: setColumnOrder,
    onColumnFiltersChange: setColumnFilters,
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnPinningChange: setColumnPinning,
    enableColumnPinning: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getRowId: getRowId ? (row) => getRowId(row) : undefined,
    defaultColumn: { size: 140, minSize: MIN_COLUMN_WIDTH, maxSize: MAX_COLUMN_WIDTH, filterFn: exactFilter },
  })

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const rows = table.getRowModel().rows
  // Pinned-left columns first, then the rest - tanstack's own
  // getVisibleLeafColumns() keeps the user's column *order* (unaffected by
  // pinning), but this component needs the actual left-to-right render
  // order once a column is pinned. getLeftLeafColumns()/getCenterLeafColumns()
  // are unfiltered by visibility (unlike the row-level getLeftVisibleCells()),
  // so filter them here.
  const pinnedLeftColumns = table.getLeftLeafColumns().filter((c) => c.getIsVisible())
  const leafColumns = [...pinnedLeftColumns, ...table.getCenterLeafColumns().filter((c) => c.getIsVisible())]
  const allColumns = table.getAllLeafColumns()
  const pinnedOffsets = pinnedLeftOffsets(pinnedLeftColumns)
  const lastPinnedId = pinnedLeftColumns.length > 0 ? pinnedLeftColumns[pinnedLeftColumns.length - 1].id : null

  // Sticky styling for a pinned-left header/cell: opaque background (a
  // scrolling row's own cells would otherwise show through underneath as the
  // table scrolls horizontally - Mantine's `striped` alternation is per-row
  // CSS, not reproduced here, so a pinned column loses the stripe, a small
  // trade-off for staying put) plus a right-edge shadow on the last pinned
  // column marking where the pinned area ends.
  const pinnedCellStyle = (columnId: string, opts?: { header?: boolean }): React.CSSProperties | undefined => {
    const offset = pinnedOffsets.get(columnId)
    if (offset === undefined) return undefined
    return {
      position: 'sticky', left: offset,
      // Mantine's own stickyHeader puts sticky-top header cells at z-index 3
      // (styles.css); a header cell that is both sticky-top AND pinned-left
      // needs to sit above that, a plain body cell just above unpinned ones.
      zIndex: opts?.header ? 4 : 2,
      backgroundColor: 'var(--mantine-color-body)',
      boxShadow: columnId === lastPinnedId ? '4px 0 4px -2px rgba(0, 0, 0, 0.35)' : undefined,
    }
  }

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })

  const cursorIndex = cursorId === null ? -1 : rows.findIndex((r) => r.id === cursorId)
  const rowActivatable = !!onRowClick || !!rowDetail
  const activateRow = (row: Row<T>) => {
    onRowClick?.(row.original)
    if (rowDetail) setDetailRow(row)
  }
  const highlightedRowId = activeRowId ?? (rowDetail ? detailRow?.id : undefined)

  const detailTitle = (row: Row<T>): ReactNode => {
    if (typeof rowDetail === 'object' && rowDetail.title) return rowDetail.title(row.original)
    const first = row.getAllCells().find((c) => c.column.columnDef.meta?.detail !== false)
    return first ? (cellText(first.getValue()) ?? '') : ''
  }

  const toggleColumnFilter = (columnId: string, value: unknown) => {
    setColumnFilters((prev) => {
      const same = prev.some((f) => f.id === columnId && String(f.value) === String(value))
      const without = prev.filter((f) => f.id !== columnId)
      return same ? without : [...without, { id: columnId, value }]
    })
  }

  // Keyboard navigation (only with onRowClick): the wrapper itself is focused,
  // arrows move a cursor row, Enter opens it, "/" jumps to the filter box.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || !rowActivatable || rows.length === 0) return
    let next = cursorIndex
    switch (e.key) {
      case 'ArrowDown': next = Math.min(cursorIndex + 1, rows.length - 1); break
      case 'ArrowUp': next = Math.max(cursorIndex - 1, 0); break
      case 'PageDown': next = Math.min(Math.max(cursorIndex, 0) + KEY_PAGE_STEP, rows.length - 1); break
      case 'PageUp': next = Math.max(Math.max(cursorIndex, 0) - KEY_PAGE_STEP, 0); break
      case 'Home': next = 0; break
      case 'End': next = rows.length - 1; break
      case 'Enter':
      case ' ':
        if (cursorIndex >= 0) {
          e.preventDefault()
          activateRow(rows[cursorIndex])
        }
        return
      case '/':
        e.preventDefault()
        filterRef.current?.focus()
        return
      default:
        return
    }
    e.preventDefault()
    setCursorId(rows[next].id)
    virtualizer.scrollToIndex(next)
  }

  const applyView = (view: SavedView) => {
    setSorting(view.sorting)
    setColumnVisibility(view.columnVisibility)
    setColumnOrder(view.columnOrder)
    setGlobalFilter(view.globalFilter)
    setColumnFilters(view.columnFilters ?? [])
    setColumnSizing(view.columnSizing ?? {})
    setColumnPinning(view.columnPinning ?? {})
    if (view.extra !== undefined) extraViewState?.apply(view.extra)
  }

  const saveCurrentView = () => {
    const name = viewName.trim()
    if (!name) return
    const next = upsertView(views, {
      name, sorting, columnVisibility, columnOrder, globalFilter, columnFilters, columnSizing, columnPinning,
      extra: extraViewState?.value,
    })
    setViews(next)
    saveViews(tableId, next)
    setViewName('')
  }

  const deleteView = (name: string) => {
    const next = views.filter((v) => v.name !== name)
    setViews(next)
    saveViews(tableId, next)
  }

  // Drag a header onto another header to move the dragged column to that
  // position. Native HTML5 drag and drop: no dependency, mouse only (touch
  // users reorder with the arrows in the Columns menu).
  const allLeafIds = () => table.getAllLeafColumns().map((c) => c.id)
  const endDrag = () => { setDragColumnId(null); setDropColumnId(null) }
  const dropOn = (targetId: string) => {
    // Pinned columns always render first, so dropping an unpinned column onto one would not
    // move it where the user points; ignore that drop (unpin the column first).
    const intoPinned = !!table.getColumn(targetId)?.getIsPinned() && !table.getColumn(dragColumnId ?? '')?.getIsPinned()
    if (dragColumnId && dragColumnId !== targetId && !intoPinned) {
      setColumnOrder(moveColumnTo(allLeafIds(), dragColumnId, targetId))
    }
    endDrag()
  }
  const resizeBy = (id: string, delta: number) => {
    const col = table.getColumn(id)
    if (!col) return
    const next = Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, col.getSize() + delta))
    setColumnSizing((prev) => ({ ...prev, [id]: next }))
  }
  const isResized = Object.keys(columnSizing).length > 0

  const moveColumnBy = (id: string, delta: -1 | 1) => {
    setColumnOrder(moveColumn(table.getAllLeafColumns().map((c) => c.id), id, delta))
  }

  // Export matrix: the visible columns in their current order, over the rows as
  // currently filtered and sorted. Raw values (not the formatted display text), so
  // "1234567" rather than "1,234,567 ISK". Header-less (action) columns are left out.
  const buildExportMatrix = (): ExportMatrix & { visibleCount: number } => {
    // `leafColumns` (not getVisibleLeafColumns) so the export follows what the table shows: pinned columns first.
    const exportColumns = leafColumns.filter((col) => col.columnDef.header !== '')
    // An explicit `meta.exportRole` wins over the header-based guess for that role.
    const allColumns = table.getAllLeafColumns()
    const explicit = new Set(allColumns.map((col) => col.columnDef.meta?.exportRole).filter(Boolean))
    const roles = firstRolesOnly(allColumns.map((col) => {
      const declared = col.columnDef.meta?.exportRole
      const guessed = inferRole(col.columnDef.header, (col.columnDef as { accessorKey?: unknown }).accessorKey)
      return { id: col.id, role: declared ?? (guessed && !explicit.has(guessed) ? guessed : undefined) }
    }))
    const roleById = new Map(roles.map((r) => [r.id, r.role]))
    const gameColumns = table.getAllLeafColumns().filter((col) => roleById.get(col.id))
    // The in-game list needs its item/quantity columns even when they are hidden in the table.
    const columns = [...exportColumns]
    for (const col of gameColumns) if (!columns.includes(col)) columns.push(col)
    return {
      columns: columns.map((col) => ({
        id: col.id, label: columnLabel(col.columnDef.header, col.id), role: roleById.get(col.id),
      })),
      rows: rows.map((row) => columns.map((col) => row.getValue(col.id))),
      visibleCount: exportColumns.length,
    }
  }

  const download = (content: BlobPart, type: string, extension: string) => {
    const blob = new Blob([content], { type })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${exportFilename}-${new Date().toISOString().slice(0, 10)}.${extension}`
    a.click()
    URL.revokeObjectURL(url)
  }
  // Same matrix but without the hidden game-list-only columns, for every format except the game list.
  const exportMatrix = (): ExportMatrix => {
    const m = buildExportMatrix()
    const keep = m.columns.slice(0, m.visibleCount)
    return { columns: keep, rows: m.rows.map((r) => r.slice(0, m.visibleCount)) }
  }
  const copyText = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text)
      notify({ title: 'Copied', message: `${rows.length} rows copied as ${what}.`, color: 'accent' })
    } catch {
      notify({ title: 'Copy failed', message: 'The browser did not allow access to the clipboard.', color: 'danger' })
    }
  }
  const exportAs = async (format: 'csv' | 'xlsx' | 'json' | 'tsv' | 'markdown' | 'game') => {
    if (format === 'game') {
      const list = toGameList(buildExportMatrix())
      if (list) await copyText(list, 'an in-game item list')
      return
    }
    const m = exportMatrix()
    switch (format) {
      case 'csv': return download(toCsv(m), 'text/csv;charset=utf-8;', 'csv')
      case 'json': return download(toJson(m), 'application/json', 'json')
      case 'xlsx':
        return download(
          await toXlsxBlob(m, exportFilename),
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx',
        )
      case 'tsv': return copyText(toTsv(m), 'a tab-separated table')
      case 'markdown': return copyText(toMarkdown(m), 'a Markdown table')
    }
  }
  const gameListAvailable = rows.length > 0 && hasGameList(buildExportMatrix())

  // Rendered instead of the real table while the owning page's query is
  // still in flight - keeps the exact same column widths (colgroup) so
  // nothing visibly jumps once real rows replace the skeleton. Distinct from
  // the data.length===0 case below: several pages used to render their
  // "no data yet" empty state (or this component's own emptyLabel) during
  // the *initial* fetch too, since `data` is `undefined`/`[]` in both the
  // "still loading" and "genuinely empty" cases - confirmed real bug, e.g.
  // Jobs.tsx's "No active industry jobs" message flashed on every page load
  // even when jobs were about to show up a moment later.
  // Checked before isLoading - react-query settles a failed query to
  // isLoading=false/isError=true, so this never fights the skeleton state
  // below; it can still be true during a background refetch of already-
  // loaded data, which is fine, that's an even stronger "something's wrong"
  // signal than the initial-load case.
  if (isError) {
    return (
      <Stack align="center" gap="xs" py="xl">
        <IconAlertTriangle size={28} color="var(--mantine-color-danger-5)" />
        <Text size="sm" c="dimmed">{errorMessage ?? 'Failed to load data.'}</Text>
        {onRetry && (
          <Button size="xs" variant="default" leftSection={<IconRefresh size={14} />} onClick={onRetry}>
            Retry
          </Button>
        )}
      </Stack>
    )
  }

  if (isLoading) {
    return (
      <ScrollArea h={maxHeight} type="auto">
        <Table style={{ tableLayout: 'fixed', width: '100%' }}>
          <colgroup>
            {leafColumns.map((col) => (
              <col key={col.id} style={{ width: col.getSize() }} />
            ))}
          </colgroup>
          <Table.Thead>
            <Table.Tr>
              {leafColumns.map((col) => (
                <Table.Th key={col.id}>
                  <Skeleton height={12} width="60%" />
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {Array.from({ length: SKELETON_ROWS }).map((_, r) => (
              <Table.Tr key={r}>
                {leafColumns.map((col) => (
                  <Table.Td key={col.id} style={{ height: rowHeight }}>
                    <Skeleton height={12} width={`${40 + ((r * 13 + col.getSize()) % 40)}%`} />
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
    )
  }

  if (data.length === 0) {
    return <Text c="dimmed" size="sm">{emptyLabel}</Text>
  }

  const virtualItems = virtualizer.getVirtualItems()
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0
  const paddingBottom = virtualItems.length > 0 ? virtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end : 0
  const cellStyle: React.CSSProperties = {
    height: rowHeight, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  }

  return (
    <div
      ref={wrapperRef}
      className={rowActivatable ? 'et-table-nav' : undefined}
      tabIndex={rowActivatable ? 0 : undefined}
      onKeyDown={rowActivatable ? handleKeyDown : undefined}
      aria-activedescendant={rowActivatable && cursorIndex >= 0 ? `${uid}-r${cursorIndex}` : undefined}
    >
      {/* Wraps below the search box on a phone: one line squeezed the box to a few pixels. */}
      <Group justify="space-between" mb="xs" gap="xs">
        <TextInput
          size="xs"
          placeholder="Filter..."
          leftSection={<IconSearch size={14} />}
          rightSection={globalFilter ? (
            <ActionIcon size="xs" variant="subtle" color="dimmed" aria-label="Clear filter" onClick={() => setGlobalFilter('')}>
              <IconX size={12} />
            </ActionIcon>
          ) : undefined}
          ref={filterRef}
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.currentTarget.value)}
          style={{ flex: '1 1 160px', maxWidth: 280 }}
        />
        <Group gap="xs" wrap="nowrap">
          {dataUpdatedAt && (
            <Text size="xs" c="dimmed" visibleFrom="xs" title={new Date(dataUpdatedAt).toLocaleString()}>
              Updated {relativeTime(dataUpdatedAt)}
            </Text>
          )}
          {tableId && (
            <Popover width={260} position="bottom-end" shadow="md" withArrow>
              <Popover.Target>
                <Button size="xs" variant="default" leftSection={<IconBookmark size={14} />}>
                  Views{views.length > 0 ? ` (${views.length})` : ''}
                </Button>
              </Popover.Target>
              <Popover.Dropdown p="xs">
                {views.length === 0 && <Text size="xs" c="dimmed" mb="xs">No saved views yet.</Text>}
                {views.map((v) => (
                  <Group key={v.name} justify="space-between" wrap="nowrap" gap={4} mb={4}>
                    <UnstyledButton onClick={() => applyView(v)} style={{ flex: 1, minWidth: 0 }}>
                      <Text size="sm" truncate>{v.name}</Text>
                    </UnstyledButton>
                    <ActionIcon size="xs" variant="subtle" color="danger" aria-label={`Delete view ${v.name}`}
                      onClick={() => deleteView(v.name)}>
                      <IconTrash size={12} />
                    </ActionIcon>
                  </Group>
                ))}
                <Group gap={4} wrap="nowrap" mt="xs">
                  <TextInput
                    size="xs" placeholder="Name for current view" aria-label="View name" style={{ flex: 1 }}
                    value={viewName} onChange={(e) => setViewName(e.currentTarget.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') saveCurrentView() }}
                  />
                  <Button size="xs" onClick={saveCurrentView} disabled={!viewName.trim()}>Save</Button>
                </Group>
              </Popover.Dropdown>
            </Popover>
          )}
          <Menu shadow="md" closeOnItemClick={false}>
            <Menu.Target>
              <Button size="xs" variant="default" leftSection={<IconColumns size={14} />}>
                Columns
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {allColumns.map((col, i) => {
                const pinned = col.getIsPinned() === 'left'
                const label = columnLabel(col.columnDef.header, col.id)
                return (
                  // component="div": Menu.Item renders a <button> by default, and a <button> may
                  // not nest other buttons (the pin and move icons below are real buttons).
                  <Menu.Item component="div" key={col.id} onClick={() => col.toggleVisibility()} closeMenuOnClick={false}>
                    <Group justify="space-between" gap="xs" wrap="nowrap">
                      <Checkbox size="xs" readOnly checked={col.getIsVisible()} label={label} />
                      <Group gap={2} wrap="nowrap">
                        {col.getCanPin() && (
                          <Tooltip label={pinned ? 'Unpin column' : 'Pin column (stays visible while scrolling right)'}>
                            <ActionIcon
                              size="xs"
                              variant={pinned ? 'filled' : 'subtle'}
                              color={pinned ? 'accent' : 'dimmed'}
                              aria-label={pinned ? `Unpin ${label}` : `Pin ${label}`}
                              onClick={(e) => {
                                e.stopPropagation()
                                col.pin(pinned ? false : 'left')
                              }}
                            >
                              {pinned ? <IconPinFilled size={12} /> : <IconPin size={12} />}
                            </ActionIcon>
                          </Tooltip>
                        )}
                        <ActionIcon size="xs" variant="subtle" color="gray" disabled={i === 0}
                          aria-label={`Move ${label} up`}
                          onClick={(e) => { e.stopPropagation(); moveColumnBy(col.id, -1) }}>
                          <IconChevronUp size={12} />
                        </ActionIcon>
                        <ActionIcon size="xs" variant="subtle" color="gray" disabled={i === allColumns.length - 1}
                          aria-label={`Move ${label} down`}
                          onClick={(e) => { e.stopPropagation(); moveColumnBy(col.id, 1) }}>
                          <IconChevronDown size={12} />
                        </ActionIcon>
                      </Group>
                    </Group>
                  </Menu.Item>
                )
              })}
              <Menu.Divider />
              <Group gap={4} px="xs" py={4}>
                <Button size="compact-xs" variant="subtle" onClick={() => setColumnOrder([])}>Reset order</Button>
                <Button size="compact-xs" variant="subtle" onClick={() => setColumnSizing({})}>Reset widths</Button>
              </Group>
            </Menu.Dropdown>
          </Menu>
          <Menu shadow="md" position="bottom-end">
            <Menu.Target>
              <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} aria-label="Export table">
                Export
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>Download</Menu.Label>
              <Menu.Item onClick={() => void exportAs('csv')}>CSV</Menu.Item>
              <Menu.Item onClick={() => void exportAs('xlsx')}>Excel (.xlsx)</Menu.Item>
              <Menu.Item onClick={() => void exportAs('json')}>JSON</Menu.Item>
              {canCopy && (
                <>
                  <Menu.Divider />
                  <Menu.Label>Copy to clipboard</Menu.Label>
                  <Menu.Item onClick={() => void exportAs('tsv')}>Table (paste into Excel / Sheets)</Menu.Item>
                  <Menu.Item onClick={() => void exportAs('markdown')}>Markdown table</Menu.Item>
                  {gameListAvailable && (
                    <Menu.Item onClick={() => void exportAs('game')}>In-game list (Item and quantity)</Menu.Item>
                  )}
                </>
              )}
            </Menu.Dropdown>
          </Menu>
        </Group>
      </Group>

      {columnFilters.length > 0 && (
        <Group gap={6} mb="xs" wrap="wrap">
          {columnFilters.map((f) => {
            const col = table.getColumn(f.id)
            const label = col ? columnLabel(col.columnDef.header, col.id) : f.id
            return (
              <Badge
                key={f.id} variant="light" color="accent" tt="none" size="lg"
                rightSection={(
                  <ActionIcon size="xs" variant="transparent" color="accent" aria-label={`Remove filter ${label}`}
                    onClick={() => toggleColumnFilter(f.id, f.value)}>
                    <IconX size={10} />
                  </ActionIcon>
                )}
              >
                {label}: {String(f.value)}
              </Badge>
            )
          })}
          <Button size="compact-xs" variant="subtle" onClick={() => setColumnFilters([])}>Reset filters</Button>
        </Group>
      )}

      <ScrollArea h={Math.min(maxHeight, rows.length * rowHeight + TABLE_CHROME_HEIGHT)} type="auto" viewportRef={scrollRef}>
        {/* Until a column is resized the table fills its container (columns scale
            proportionally, as before). Once one is resized, widths are exact px so
            the dragged edge stays under the pointer; the area then scrolls sideways. */}
        <Table stickyHeader striped style={{ tableLayout: 'fixed', width: isResized ? table.getTotalSize() : '100%' }}>
          <colgroup>
            {leafColumns.map((col) => (
              <col key={col.id} style={{ width: col.getSize() }} />
            ))}
          </colgroup>
          <Table.Thead>
            {/* Pinned-left headers first, then center - same order as `leafColumns`/
                colgroup above. getLeftHeaderGroups()/getCenterHeaderGroups() (not a
                flat getHeaderGroups()) so a pinned column also reorders correctly
                for a page with grouped/nested headers, not just this app's usual
                flat ones. */}
            {table.getLeftHeaderGroups().map((hg, i) => (
              <Table.Tr key={hg.id}>
                {[...hg.headers, ...(table.getCenterHeaderGroups()[i]?.headers ?? [])].map((h) => {
                  const sorted = h.column.getIsSorted()
                  const canSort = h.column.getCanSort()
                  const toggleSort = h.column.getToggleSortingHandler()
                  // GitHub issue #61 (found in a full-codebase audit
                  // 2026-08-21): sortable headers were mouse-only - no
                  // tabIndex/onKeyDown/aria-sort - unreachable for a
                  // keyboard-only user, app-wide (every page uses this one
                  // shared component). tabIndex/onKeyDown only apply to
                  // actually-sortable columns, matching the existing
                  // cursor:pointer-only-when-sortable convention below.
                  return (
                    <Table.Th
                      key={h.id}
                      draggable
                      onDragStart={(e) => {
                        if (resizingRef.current) { e.preventDefault(); return }
                        setDragColumnId(h.column.id)
                        e.dataTransfer?.setData('text/plain', h.column.id)
                        if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
                      }}
                      onDragOver={(e) => {
                        if (!dragColumnId) return
                        e.preventDefault()
                        if (dropColumnId !== h.column.id) setDropColumnId(h.column.id)
                      }}
                      onDrop={(e) => { e.preventDefault(); dropOn(h.column.id) }}
                      onDragEnd={endDrag}
                      onClick={toggleSort}
                      tabIndex={canSort ? 0 : undefined}
                      role={canSort ? 'button' : undefined}
                      aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : canSort ? 'none' : undefined}
                      onKeyDown={canSort ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          toggleSort?.(e)
                        }
                      } : undefined}
                      title={columnLabel(h.column.columnDef.header, h.column.id)}
                      style={{
                        cursor: canSort ? 'pointer' : undefined, userSelect: 'none',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                        position: 'relative', // anchors the resize handle
                        opacity: dragColumnId === h.column.id ? 0.4 : undefined,
                        boxShadow: dropColumnId === h.column.id && dragColumnId !== h.column.id ? 'inset 2px 0 0 #35D0BA' : undefined,
                        ...pinnedCellStyle(h.column.id, { header: true }),
                      }}
                    >
                      {h.column.columnDef.meta?.headerHint ? (
                        <Tooltip label={h.column.columnDef.meta.headerHint} multiline w={280}>
                          <span>{flexRender(h.column.columnDef.header, h.getContext())}</span>
                        </Tooltip>
                      ) : flexRender(h.column.columnDef.header, h.getContext())}
                      {sorted === 'asc' ? ' ▲' : sorted === 'desc' ? ' ▼' : ''}
                      {h.column.getCanResize() && (
                        <div
                          role="separator"
                          aria-orientation="vertical"
                          aria-label={`Resize column ${columnLabel(h.column.columnDef.header, h.column.id)}`}
                          aria-valuenow={h.column.getSize()}
                          aria-valuemin={MIN_COLUMN_WIDTH}
                          aria-valuemax={MAX_COLUMN_WIDTH}
                          tabIndex={0}
                          className="et-resizer"
                          data-resizing={h.column.getIsResizing() ? '' : undefined}
                          draggable={false}
                          onClick={(e) => e.stopPropagation()}
                          onDoubleClick={(e) => {
                            e.stopPropagation()
                            setColumnSizing((prev) => { const { [h.column.id]: _removed, ...rest } = prev; return rest })
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                              e.preventDefault()
                              e.stopPropagation()
                              resizeBy(h.column.id, e.key === 'ArrowLeft' ? -KEYBOARD_RESIZE_STEP : KEYBOARD_RESIZE_STEP)
                            } else if (e.key === 'Enter' || e.key === ' ') {
                              e.stopPropagation() // do not trigger the header's sort toggle
                            }
                          }}
                          onMouseDown={(e) => {
                            resizingRef.current = true
                            window.addEventListener('mouseup', () => { resizingRef.current = false }, { once: true })
                            h.getResizeHandler()(e)
                          }}
                          onTouchStart={(e) => {
                            resizingRef.current = true
                            window.addEventListener('touchend', () => { resizingRef.current = false }, { once: true })
                            h.getResizeHandler()(e)
                          }}
                        />
                      )}
                    </Table.Th>
                  )
                })}
              </Table.Tr>
            ))}
          </Table.Thead>
          <Table.Tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={leafColumns.length} style={{ padding: 'var(--mantine-spacing-sm)' }}>
                  <Text c="dimmed" size="sm">No rows match your filter.</Text>
                </td>
              </tr>
            ) : (
              <>
                {paddingTop > 0 && (
                  <tr>
                    <td style={{ height: paddingTop, padding: 0, border: 0 }} colSpan={leafColumns.length} />
                  </tr>
                )}
                {virtualItems.map((vItem) => {
                  const row = rows[vItem.index]
                  return (
                    <Table.Tr
                      key={row.id}
                      id={rowActivatable ? `${uid}-r${vItem.index}` : undefined}
                      data-clickable={rowActivatable ? '' : undefined}
                      data-active={highlightedRowId !== undefined && row.id === highlightedRowId ? '' : undefined}
                      data-cursor={rowActivatable && row.id === cursorId ? '' : undefined}
                      data-changed={changedIds.has(row.id) ? '' : undefined}
                      onClick={rowActivatable ? (e) => {
                        if ((e.target as HTMLElement).closest(INTERACTIVE_SELECTOR)) return
                        setCursorId(row.id)
                        activateRow(row)
                      } : undefined}
                    >
                      {[...row.getLeftVisibleCells(), ...row.getCenterVisibleCells()]
                        .map((cell) => {
                          const colMeta = cell.column.columnDef.meta
                          const defaults = columnDefaults(
                            cell.column.columnDef.header, (cell.column.columnDef as { accessorKey?: unknown }).accessorKey,
                          )
                          const cellValue = cell.getValue()
                          const copyText = (colMeta?.copyable ?? defaults.copyable) && canCopy && typeof cellValue === 'string'
                            ? cellText(cellValue)
                            : undefined
                          const hoverContent = canHover ? cell.column.columnDef.meta?.hoverCard : undefined
                          const content = (
                            <>
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                              {copyText !== undefined && <CopyCell value={copyText} />}
                              {(colMeta?.filterable ?? defaults.filterable) && typeof cellValue === 'string' && cellValue !== '' && (
                                <ActionIcon
                                  className="et-copy" size="xs" variant="subtle" color="gray"
                                  aria-label={`Filter ${columnLabel(cell.column.columnDef.header, cell.column.id)} = ${String(cell.getValue())}`}
                                  onClick={(e) => { e.stopPropagation(); toggleColumnFilter(cell.column.id, cell.getValue()) }}
                                  style={{ marginLeft: 4, verticalAlign: 'middle' }}
                                >
                                  <IconFilter size={12} />
                                </ActionIcon>
                              )}
                            </>
                          )
                          return (
                            <Table.Td
                              key={cell.id}
                              style={{ ...cellStyle, ...pinnedCellStyle(cell.column.id) }}
                              title={hoverContent ? undefined : (cell.column.columnDef.meta?.cellTitle?.(cell.row.original, cell.getValue()) ?? cellText(cell.getValue()))}
                            >
                              {hoverContent ? (
                                <HoverCard openDelay={400} withArrow shadow="md" position="bottom-start" withinPortal>
                                  <HoverCard.Target><span>{content}</span></HoverCard.Target>
                                  <HoverCard.Dropdown>{hoverContent(cell.row.original)}</HoverCard.Dropdown>
                                </HoverCard>
                              ) : content}
                            </Table.Td>
                          )
                        })}
                    </Table.Tr>
                  )
                })}
                {paddingBottom > 0 && (
                  <tr>
                    <td style={{ height: paddingBottom, padding: 0, border: 0 }} colSpan={leafColumns.length} />
                  </tr>
                )}
              </>
            )}
          </Table.Tbody>
        </Table>
      </ScrollArea>

      {rowDetail && (
        <Drawer
          opened={!!detailRow} onClose={() => setDetailRow(null)} position="right" size="md" padding="md"
          title={detailRow ? detailTitle(detailRow) : ''}
        >
          <Stack gap={6}>
            {detailRow?.getAllCells()
              .filter((c) => c.column.columnDef.meta?.detail !== false && typeof c.column.columnDef.header === 'string' && c.column.columnDef.header !== '')
              .map((c) => (
                <Group key={c.id} justify="space-between" wrap="nowrap" gap="md" align="flex-start">
                  <Text size="sm" c="dimmed">{columnLabel(c.column.columnDef.header, c.column.id)}</Text>
                  <div style={{ textAlign: 'right', minWidth: 0 }}>{flexRender(c.column.columnDef.cell, c.getContext())}</div>
                </Group>
              ))}
          </Stack>
        </Drawer>
      )}
    </div>
  )
}
