import { useRef, useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Table, ScrollArea, Text, Skeleton } from '@mantine/core'

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
interface DataTableProps<T> {
  data: T[]
  columns: ColumnDef<T, any>[]
  maxHeight?: number
  emptyLabel?: string
  rowHeight?: number
  isLoading?: boolean
}

const SKELETON_ROWS = 8

function cellText(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return undefined
}

export function DataTable<T>({
  data,
  columns,
  maxHeight = 480,
  emptyLabel = 'No data.',
  rowHeight = 36,
  isLoading = false,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    defaultColumn: { size: 140, minSize: 60 },
  })

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const rows = table.getRowModel().rows
  const leafColumns = table.getVisibleLeafColumns()

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })

  // Rendered instead of the real table while the owning page's query is
  // still in flight - keeps the exact same column widths (colgroup) so
  // nothing visibly jumps once real rows replace the skeleton. Distinct from
  // the data.length===0 case below: several pages used to render their
  // "no data yet" empty state (or this component's own emptyLabel) during
  // the *initial* fetch too, since `data` is `undefined`/`[]` in both the
  // "still loading" and "genuinely empty" cases - confirmed real bug, e.g.
  // Jobs.tsx's "No active industry jobs" message flashed on every page load
  // even when jobs were about to show up a moment later.
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
                return (
                  <Table.Th
                    key={h.id}
                    onClick={h.column.getToggleSortingHandler()}
                    style={{
                      cursor: h.column.getCanSort() ? 'pointer' : undefined, userSelect: 'none',
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
          {paddingTop > 0 && (
            <tr>
              <td style={{ height: paddingTop, padding: 0, border: 0 }} colSpan={columns.length} />
            </tr>
          )}
          {virtualItems.map((vItem) => {
            const row = rows[vItem.index]
            return (
              <Table.Tr key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <Table.Td key={cell.id} style={cellStyle} title={cellText(cell.getValue())}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </Table.Td>
                ))}
              </Table.Tr>
            )
          })}
          {paddingBottom > 0 && (
            <tr>
              <td style={{ height: paddingBottom, padding: 0, border: 0 }} colSpan={columns.length} />
            </tr>
          )}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  )
}
