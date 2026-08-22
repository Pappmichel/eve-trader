import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'
import { DataTable } from './DataTable'

interface Row {
  item: string
  amount: number
}

const rows: Row[] = [
  { item: 'Zebra Ore', amount: 5 },
  { item: 'Alpha Ore', amount: 1000000 },
  { item: 'Mid Ore', amount: 50 },
]

const columns: ColumnDef<Row, any>[] = [
  { header: 'Item', accessorKey: 'item' },
  // Cell renderer formats with thousands separators (as every real page's
  // isk()/qty() columns do) - the sort must still use the raw numeric
  // accessor, not the formatted string, or "1,000,000" would sort before
  // "50" as text (this is the exact bug DataTable.tsx's own module
  // docstring says it was built to fix).
  { header: 'Amount', accessorKey: 'amount', cell: (i) => i.getValue().toLocaleString('en-US') },
]

function renderTable(props: Partial<React.ComponentProps<typeof DataTable<Row>>> = {}) {
  return render(
    <MantineProvider>
      <DataTable data={rows} columns={columns} {...props} />
    </MantineProvider>,
  )
}

function bodyRows() {
  return screen.getAllByRole('row').slice(1) // drop the header row
}

describe('DataTable', () => {
  it('renders every row initially, unsorted (source order)', () => {
    renderTable()
    const cells = bodyRows().map((r) => within(r).getAllByRole('cell')[0].textContent)
    expect(cells).toEqual(['Zebra Ore', 'Alpha Ore', 'Mid Ore'])
  })

  it('sorts a formatted numeric column by its real value, not the display string', async () => {
    const user = userEvent.setup()
    renderTable()

    // Sortable headers carry role="button" (GitHub issue #61 - keyboard
    // accessibility), not the <th> element's implicit "columnheader" role.
    await user.click(screen.getByRole('button', { name: /Amount/ }))
    let cells = bodyRows().map((r) => within(r).getAllByRole('cell')[0].textContent)
    // Descending by real numeric value (1,000,000 > 50 > 5) - if this sorted
    // the *formatted* strings instead, "1,000,000" would sort before "50"
    // as plain text too, so this alone wouldn't catch the bug; the second
    // click below (ascending: 5 < 50 < 1,000,000) is the one that would
    // actually fail under a string-based sort ("1,000,000" < "5" < "50"
    // lexicographically).
    expect(cells).toEqual(['Alpha Ore', 'Mid Ore', 'Zebra Ore'])

    // Sortable headers carry role="button" (GitHub issue #61 - keyboard
    // accessibility), not the <th> element's implicit "columnheader" role.
    await user.click(screen.getByRole('button', { name: /Amount/ }))
    cells = bodyRows().map((r) => within(r).getAllByRole('cell')[0].textContent)
    expect(cells).toEqual(['Zebra Ore', 'Mid Ore', 'Alpha Ore'])
  })

  it('filters rows via the global text filter across all columns', async () => {
    const user = userEvent.setup()
    renderTable()

    await user.type(screen.getByPlaceholderText('Filter...'), 'zebra')
    const cells = bodyRows().map((r) => within(r).getAllByRole('cell')[0].textContent)
    expect(cells).toEqual(['Zebra Ore'])
  })

  it('shows the empty-label text when data is empty (not the loading skeleton)', () => {
    renderTable({ data: [], emptyLabel: 'Nothing here.' })
    expect(screen.getByText('Nothing here.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('exports visible columns/rows as CSV, quoting every field', async () => {
    const user = userEvent.setup()
    // DataTable.tsx builds the CSV as `new Blob([content], {...})` - capture
    // the raw string via the Blob constructor itself rather than reading it
    // back out with blob.text() (jsdom's Blob doesn't implement it).
    const OriginalBlob = globalThis.Blob
    let csv: string | undefined
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    globalThis.Blob = vi.fn((parts: any[], options: any) => {
      csv = parts.join('')
      return new OriginalBlob(parts, options)
    }) as unknown as typeof Blob
    URL.createObjectURL = vi.fn(() => 'blob:mock')
    URL.revokeObjectURL = vi.fn()

    renderTable({ exportFilename: 'test-export' })
    await user.click(screen.getByRole('button', { name: 'Export' }))

    expect(csv).toBeDefined()
    expect(csv!.split('\r\n')[0]).toBe('Item,Amount')
    expect(csv).toContain('"Zebra Ore","5"')

    globalThis.Blob = OriginalBlob
  })
})
