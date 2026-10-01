import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type ColumnDef,
  type ColumnOrderState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Table, ScrollArea, Text, Skeleton, Group, TextInput, Menu, Checkbox, Button, ActionIcon, Stack, CopyButton, HoverCard, Popover, UnstyledButton } from '@mantine/core'
import { IconSearch, IconDownload, IconColumns, IconX, IconAlertTriangle, IconRefresh, IconCopy, IconCheck, IconChevronUp, IconChevronDown, IconBookmark, IconTrash } from '@tabler/icons-react'
import { relativeTime } from '../format'
import {
  loadColumnOrder, loadViews, moveColumn, saveColumnOrder, saveViews, upsertView, type SavedView,
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
}

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

// RFC4126-ish CSV quoting - always quotes (simplest correct approach: never
// have to special-case which fields *need* it) and doubles internal quotes.
function csvField(value: unknown): string {
  const text = cellText(value) ?? ''
  return `"${text.replace(/"/g, '""')}"`
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
  tableId,
  exportFilename = 'export',
  getRowId,
  dataUpdatedAt,
  onRowClick,
  activeRowId,
  extraViewState,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() => loadPersistedVisibility(tableId))
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(() => loadColumnOrder(tableId))
  const [views, setViews] = useState<SavedView[]>(() => loadViews(tableId))
  const [viewName, setViewName] = useState('')
  const [cursorId, setCursorId] = useState<string | null>(null)
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set())
  const uid = useId()
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const filterRef = useRef<HTMLInputElement | null>(null)

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
    setViews(loadViews(tableId))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId])

  useEffect(() => {
    saveColumnOrder(tableId, columnOrder)
  }, [tableId, columnOrder])

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

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter, columnVisibility, columnOrder },
    onColumnOrderChange: setColumnOrder,
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getRowId: getRowId ? (row) => getRowId(row) : undefined,
    defaultColumn: { size: 140, minSize: 60 },
  })

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const rows = table.getRowModel().rows
  const leafColumns = table.getVisibleLeafColumns()
  const allColumns = table.getAllLeafColumns()

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })

  const cursorIndex = cursorId === null ? -1 : rows.findIndex((r) => r.id === cursorId)

  // Keyboard navigation (only with onRowClick): the wrapper itself is focused,
  // arrows move a cursor row, Enter opens it, "/" jumps to the filter box.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || !onRowClick || rows.length === 0) return
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
          onRowClick(rows[cursorIndex].original)
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
    if (view.extra !== undefined) extraViewState?.apply(view.extra)
  }

  const saveCurrentView = () => {
    const name = viewName.trim()
    if (!name) return
    const next = upsertView(views, {
      name, sorting, columnVisibility, columnOrder, globalFilter,
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

  const moveColumnBy = (id: string, delta: -1 | 1) => {
    setColumnOrder(moveColumn(table.getAllLeafColumns().map((c) => c.id), id, delta))
  }

  const exportCsv = () => {
    // Deliberately table.getVisibleLeafColumns() (the user's real Columns-menu
    // choice), not the mobile-filtered `leafColumns` below - exporting data
    // shouldn't silently drop columns just because the viewport is narrow
    // right now.
    const exportColumns = table.getVisibleLeafColumns()
    const header = exportColumns.map((col) => columnLabel(col.columnDef.header, col.id)).join(',')
    const body = rows
      .map((row) => row.getVisibleCells().map((cell) => csvField(cell.getValue())).join(','))
      .join('\r\n')
    const blob = new Blob([`${header}\r\n${body}`], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${exportFilename}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

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
      className={onRowClick ? 'et-table-nav' : undefined}
      tabIndex={onRowClick ? 0 : undefined}
      onKeyDown={onRowClick ? handleKeyDown : undefined}
      aria-activedescendant={onRowClick && cursorIndex >= 0 ? `${uid}-r${cursorIndex}` : undefined}
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
              {allColumns.map((col, i) => (
                <Group key={col.id} justify="space-between" wrap="nowrap" gap="xs" px="xs" py={4}>
                  <Checkbox
                    size="xs"
                    checked={col.getIsVisible()}
                    onChange={() => col.toggleVisibility()}
                    label={columnLabel(col.columnDef.header, col.id)}
                  />
                  <Group gap={2} wrap="nowrap">
                    <ActionIcon size="xs" variant="subtle" color="gray" disabled={i === 0}
                      aria-label={`Move ${columnLabel(col.columnDef.header, col.id)} up`}
                      onClick={() => moveColumnBy(col.id, -1)}>
                      <IconChevronUp size={12} />
                    </ActionIcon>
                    <ActionIcon size="xs" variant="subtle" color="gray" disabled={i === allColumns.length - 1}
                      aria-label={`Move ${columnLabel(col.columnDef.header, col.id)} down`}
                      onClick={() => moveColumnBy(col.id, 1)}>
                      <IconChevronDown size={12} />
                    </ActionIcon>
                  </Group>
                </Group>
              ))}
            </Menu.Dropdown>
          </Menu>
          <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} onClick={exportCsv}>
            Export
          </Button>
        </Group>
      </Group>

      <ScrollArea h={maxHeight} type="auto" viewportRef={scrollRef}>
        <Table stickyHeader striped style={{ tableLayout: 'fixed', width: '100%' }}>
          <colgroup>
            {leafColumns.map((col) => (
              <col key={col.id} style={{ width: col.getSize() }} />
            ))}
          </colgroup>
          <Table.Thead>
            {table.getHeaderGroups().map((hg) => (
              <Table.Tr key={hg.id}>
                {hg.headers.map((h) => {
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
                      }}
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {sorted === 'asc' ? ' ▲' : sorted === 'desc' ? ' ▼' : ''}
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
                      id={onRowClick ? `${uid}-r${vItem.index}` : undefined}
                      data-clickable={onRowClick ? '' : undefined}
                      data-active={activeRowId !== undefined && row.id === activeRowId ? '' : undefined}
                      data-cursor={onRowClick && row.id === cursorId ? '' : undefined}
                      data-changed={changedIds.has(row.id) ? '' : undefined}
                      onClick={onRowClick ? (e) => {
                        if ((e.target as HTMLElement).closest(INTERACTIVE_SELECTOR)) return
                        setCursorId(row.id)
                        onRowClick(row.original)
                      } : undefined}
                    >
                      {row.getVisibleCells()
                        .map((cell) => {
                          const copyText = cell.column.columnDef.meta?.copyable && canCopy
                            ? cellText(cell.getValue())
                            : undefined
                          const hoverContent = canHover ? cell.column.columnDef.meta?.hoverCard : undefined
                          const content = (
                            <>
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                              {copyText !== undefined && <CopyCell value={copyText} />}
                            </>
                          )
                          return (
                            <Table.Td
                              key={cell.id}
                              style={cellStyle}
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
    </div>
  )
}
