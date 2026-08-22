import { useEffect, useRef, useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Table, ScrollArea, Text, Skeleton, Group, TextInput, Menu, Checkbox, Button, ActionIcon, Stack } from '@mantine/core'
import { useMediaQuery } from '@mantine/hooks'
import { IconSearch, IconDownload, IconColumns, IconX, IconAlertTriangle, IconRefresh } from '@tabler/icons-react'

// A column can opt into `meta: { mobileHide: true }` (see Shortlist.tsx/
// Margin.tsx for real examples) - GitHub issue #52: below Mantine's `sm`
// breakpoint, these columns are force-hidden on top of whatever the user
// picked in the Columns menu, so a dense desktop table (10+ fixed-width
// columns, see this file's own header comment on why columns are fixed-
// width) doesn't force horizontal scrolling on a phone screen for its own
// sake. Purely a display default, not persisted - the user's own Columns
// menu choice (localStorage) always still applies on top once they're back
// above the breakpoint. Unmarked columns behave exactly as before (still
// horizontally scrollable on mobile) - this is opt-in per page, not a
// blanket behavior change.
declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    mobileHide?: boolean
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
}

const SKELETON_ROWS = 8

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
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() => loadPersistedVisibility(tableId))
  // Mantine's `sm` breakpoint (768px) - same threshold AppShell's own navbar
  // collapse already uses (see TradingLayout.tsx/ProductionLayout.tsx), so
  // "mobile" means the same viewport width everywhere in the app.
  const isMobile = useMediaQuery('(max-width: 48em)')

  // Re-derive from localStorage (or reset to "everything visible") whenever
  // this instance switches to a *different* tableId - without this, a page
  // that reuses one <DataTable> for several tabs (same component instance,
  // changing tableId as the user switches tabs) would keep showing the
  // previous tab's hidden-columns selection.
  useEffect(() => {
    setColumnVisibility(loadPersistedVisibility(tableId))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId])

  useEffect(() => {
    if (!tableId) return
    localStorage.setItem(`datatable:${tableId}:columns`, JSON.stringify(columnVisibility))
  }, [tableId, columnVisibility])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter, columnVisibility },
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
  // Mobile-hide is applied here, not folded into columnVisibility/localStorage
  // - it must never overwrite the user's own persisted Columns-menu choice,
  // and must stop applying the instant the viewport is wide enough again.
  const leafColumns = table.getVisibleLeafColumns()
    .filter((col) => !(isMobile && col.columnDef.meta?.mobileHide))
  const allColumns = table.getAllLeafColumns()

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })

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
    <div>
      <Group justify="space-between" mb="xs" gap="xs" wrap="nowrap">
        <TextInput
          size="xs"
          placeholder="Filter..."
          leftSection={<IconSearch size={14} />}
          rightSection={globalFilter ? (
            <ActionIcon size="xs" variant="subtle" color="dimmed" aria-label="Clear filter" onClick={() => setGlobalFilter('')}>
              <IconX size={12} />
            </ActionIcon>
          ) : undefined}
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.currentTarget.value)}
          style={{ flex: 1, maxWidth: 280 }}
        />
        <Group gap="xs" wrap="nowrap">
          <Menu shadow="md" closeOnItemClick={false}>
            <Menu.Target>
              <Button size="xs" variant="default" leftSection={<IconColumns size={14} />}>
                Columns
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {allColumns.map((col) => (
                <Menu.Item key={col.id} onClick={() => col.toggleVisibility()} closeMenuOnClick={false}>
                  <Checkbox
                    size="xs"
                    readOnly
                    checked={col.getIsVisible()}
                    label={columnLabel(col.columnDef.header, col.id)}
                  />
                </Menu.Item>
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
                {hg.headers.filter((h) => !(isMobile && h.column.columnDef.meta?.mobileHide)).map((h) => {
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
                    <Table.Tr key={row.id}>
                      {row.getVisibleCells()
                        .filter((cell) => !(isMobile && cell.column.columnDef.meta?.mobileHide))
                        .map((cell) => (
                          <Table.Td key={cell.id} style={cellStyle} title={cellText(cell.getValue())}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </Table.Td>
                        ))}
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
