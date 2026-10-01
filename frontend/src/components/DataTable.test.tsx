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

  it('uses a column meta.cellTitle for the native hover title when provided', () => {
    const titled: ColumnDef<Row, any>[] = [
      { header: 'Item', accessorKey: 'item' },
      {
        header: 'Amount',
        accessorKey: 'amount',
        meta: { cellTitle: (row) => `${row.item}: ${row.amount} missing` },
      },
    ]
    render(
      <MantineProvider>
        <DataTable data={rows} columns={titled} />
      </MantineProvider>,
    )
    const amountCell = within(bodyRows()[0]).getAllByRole('cell')[1]
    expect(amountCell).toHaveAttribute('title', 'Zebra Ore: 5 missing')
  })

  it('renders normally when localStorage holds corrupted JSON for column visibility', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => '{not valid json',
      setItem: vi.fn(),
      removeItem: vi.fn(),
    })
    expect(() => renderTable({ tableId: 'corrupt-test' })).not.toThrow()
    const cells = bodyRows().map((r) => within(r).getAllByRole('cell')[0].textContent)
    expect(cells).toEqual(['Zebra Ore', 'Alpha Ore', 'Mid Ore'])
    vi.unstubAllGlobals()
  })

  it('renders normally when localStorage.setItem throws (quota exceeded / private browsing)', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {
        throw new DOMException('QuotaExceededError')
      },
      removeItem: vi.fn(),
    })
    expect(() => renderTable({ tableId: 'quota-test' })).not.toThrow()
    vi.unstubAllGlobals()
  })

  it('renders normally when localStorage.getItem throws (unavailable storage)', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new DOMException('SecurityError')
      },
      setItem: vi.fn(),
      removeItem: vi.fn(),
    })
    expect(() => renderTable({ tableId: 'unavailable-test' })).not.toThrow()
    vi.unstubAllGlobals()
  })
})

describe('DataTable row click', () => {
  it('does nothing and is not marked clickable when onRowClick is not set', async () => {
    const user = userEvent.setup()
    renderTable()
    await user.click(bodyRows()[0])
    expect(bodyRows()[0]).not.toHaveAttribute('data-clickable')
  })

  it('calls onRowClick with the clicked row and marks rows clickable', async () => {
    const user = userEvent.setup()
    const onRowClick = vi.fn()
    renderTable({ onRowClick })
    expect(bodyRows()[1]).toHaveAttribute('data-clickable')
    await user.click(bodyRows()[1])
    expect(onRowClick).toHaveBeenCalledWith(rows[1])
  })

  it('ignores clicks on interactive elements inside a row', async () => {
    const user = userEvent.setup()
    const onRowClick = vi.fn()
    const cols: ColumnDef<Row, any>[] = [
      { header: 'Item', accessorKey: 'item' },
      { header: 'Act', id: 'act', cell: () => <button type="button">Go</button> },
    ]
    render(
      <MantineProvider>
        <DataTable data={rows} columns={cols} onRowClick={onRowClick} />
      </MantineProvider>,
    )
    await user.click(screen.getAllByRole('button', { name: 'Go' })[0])
    expect(onRowClick).not.toHaveBeenCalled()
  })

  it('highlights the row whose getRowId matches activeRowId', () => {
    renderTable({ getRowId: (r) => r.item, activeRowId: 'Mid Ore' })
    const active = bodyRows().filter((r) => r.hasAttribute('data-active'))
    expect(active).toHaveLength(1)
    expect(within(active[0]).getAllByRole('cell')[0].textContent).toBe('Mid Ore')
  })
})

describe('DataTable copyable columns', () => {
  const copyColumns: ColumnDef<Row, any>[] = [
    { header: 'Item', accessorKey: 'item', meta: { copyable: true } },
    { header: 'Amount', accessorKey: 'amount' },
  ]

  it('renders no copy button unless a column is marked copyable', () => {
    renderTable()
    expect(screen.queryAllByRole('button', { name: /copy value/i })).toHaveLength(0)
  })

  it('renders a copy button on copyable cells when a clipboard exists', async () => {
    vi.resetModules()
    vi.stubGlobal('isSecureContext', true)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn() }, configurable: true })
    const { DataTable: Fresh } = await import('./DataTable')
    render(
      <MantineProvider>
        <Fresh data={rows} columns={copyColumns} />
      </MantineProvider>,
    )
    expect(screen.getAllByRole('button', { name: /copy value/i })).toHaveLength(rows.length)
    vi.unstubAllGlobals()
  })
})

describe('DataTable keyboard navigation', () => {
  it('is not focusable without onRowClick', () => {
    const { container } = renderTable()
    expect(container.firstElementChild?.hasAttribute('tabindex')).toBe(false)
  })

  it('moves a cursor with arrows, opens with Enter and focuses the filter with "/"', async () => {
    const user = userEvent.setup()
    const onRowClick = vi.fn()
    const { container } = renderTable({ onRowClick, getRowId: (r) => r.item })
    const wrapper = container.querySelector('[tabindex="0"]') as HTMLElement
    wrapper.focus()

    await user.keyboard('{ArrowDown}{ArrowDown}')
    expect(bodyRows()[1]).toHaveAttribute('data-cursor')
    expect(wrapper.getAttribute('aria-activedescendant')).toBe(bodyRows()[1].id)

    await user.keyboard('{Enter}')
    expect(onRowClick).toHaveBeenCalledWith(rows[1])

    await user.keyboard('{End}')
    expect(bodyRows()[2]).toHaveAttribute('data-cursor')
    await user.keyboard('{Home}')
    expect(bodyRows()[0]).toHaveAttribute('data-cursor')

    await user.keyboard('/')
    expect(screen.getByPlaceholderText('Filter...')).toHaveFocus()
  })
})

describe('DataTable saved views and column order', () => {
  it('saves the current sort under a name, restores it later and can delete it', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    renderTable({ tableId: 'views-test' })

    await user.click(screen.getByRole('button', { name: /Amount/ })) // sort by Amount
    await user.click(screen.getByRole('button', { name: /Views/ }))
    await user.type(await screen.findByLabelText('View name'), 'Big first')
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(JSON.parse(localStorage.getItem('datatable:views-test:views')!)).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: /Item/ })) // change sort
    await user.click(await screen.findByText('Big first'))
    const cells = bodyRows().map((r) => within(r).getAllByRole('cell')[0].textContent)
    expect(cells).toEqual(['Alpha Ore', 'Mid Ore', 'Zebra Ore'])

    await user.click(await screen.findByRole('button', { name: 'Delete view Big first' }))
    expect(JSON.parse(localStorage.getItem('datatable:views-test:views')!)).toHaveLength(0)
  })

  it('hands page-owned state to extraViewState on save and apply', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    const apply = vi.fn()
    renderTable({ tableId: 'extra-test', extraViewState: { value: { status: ['Import'] }, apply } })
    await user.click(screen.getByRole('button', { name: /Views/ }))
    await user.type(await screen.findByLabelText('View name'), 'Imports')
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    await user.click(await screen.findByText('Imports'))
    expect(apply).toHaveBeenCalledWith({ status: ['Import'] })
  })

  it('reorders columns from the Columns menu and persists the order', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    renderTable({ tableId: 'order-test' })
    await user.click(screen.getByRole('button', { name: /Columns/ }))
    await user.click(await screen.findByRole('button', { name: 'Move Item down' }))
    const headers = Array.from(document.querySelectorAll('thead th')).map((h) => h.textContent?.replace(/[▲▼]/g, '').trim())
    expect(headers).toEqual(['Amount', 'Item'])
    expect(JSON.parse(localStorage.getItem('datatable:order-test:order')!)).toEqual(['amount', 'item'])
  })
})

describe('DataTable change highlighting', () => {
  it('flashes only rows whose tracked column changed after a refetch', () => {
    const cols: ColumnDef<Row, any>[] = [
      { header: 'Item', accessorKey: 'item' },
      { header: 'Amount', accessorKey: 'amount', meta: { trackChanges: true } },
    ]
    const ui = (data: Row[]) => (
      <MantineProvider>
        <DataTable data={data} columns={cols} getRowId={(r) => r.item} />
      </MantineProvider>
    )
    const { rerender } = render(ui(rows))
    expect(bodyRows().some((r) => r.hasAttribute('data-changed'))).toBe(false) // first load: no flash

    rerender(ui(rows.map((r) => (r.item === 'Mid Ore' ? { ...r, amount: 51 } : r))))
    const flashed = bodyRows().filter((r) => r.hasAttribute('data-changed'))
    expect(flashed).toHaveLength(1)
    expect(within(flashed[0]).getAllByRole('cell')[0].textContent).toBe('Mid Ore')
  })
})

describe('DataTable hover card', () => {
  it('renders no hover content on a coarse pointer (no matchMedia match)', () => {
    const cols: ColumnDef<Row, any>[] = [
      { header: 'Item', accessorKey: 'item', meta: { hoverCard: (r: Row) => <div>detail {r.item}</div> } },
    ]
    render(<MantineProvider><DataTable data={rows} columns={cols} /></MantineProvider>)
    expect(screen.queryByText(/detail Zebra Ore/)).not.toBeInTheDocument()
    expect(bodyRows()[0].querySelector('td')?.getAttribute('title')).toBe('Zebra Ore')
  })
})

describe('DataTable row detail drawer', () => {
  it('opens a drawer with every column, including hidden ones, and closes again', async () => {
    const user = userEvent.setup()
    const cols: ColumnDef<Row, any>[] = [
      { header: 'Item', accessorKey: 'item' },
      { header: 'Amount', accessorKey: 'amount', cell: (i) => i.getValue().toLocaleString('en-US') },
      { header: '', id: 'actions', cell: () => <span>actions-cell</span> },
    ]
    render(
      <MantineProvider>
        <DataTable data={rows} columns={cols} rowDetail getRowId={(r) => r.item} tableId="detail-test" />
      </MantineProvider>,
    )
    // Hide the Amount column in the table first - the drawer must still show it.
    await user.click(screen.getByRole('button', { name: /Columns/ }))
    await user.click(await screen.findByLabelText('Amount'))
    await user.keyboard('{Escape}')

    await user.click(screen.getByText('Alpha Ore'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Alpha Ore' })).toBeInTheDocument() // default title: first column
    expect(within(dialog).getByText('Amount')).toBeInTheDocument()
    expect(within(dialog).getByText('1,000,000')).toBeInTheDocument()
    expect(within(dialog).queryByText('actions-cell')).not.toBeInTheDocument() // header-less column skipped
  })

  it('uses a custom title and also runs onRowClick', async () => {
    const user = userEvent.setup()
    const onRowClick = vi.fn()
    renderTable({ rowDetail: { title: (r: Row) => `Details: ${r.item}` }, onRowClick })
    await user.click(screen.getByText('Mid Ore'))
    expect(await screen.findByText('Details: Mid Ore')).toBeInTheDocument()
    expect(onRowClick).toHaveBeenCalledWith(rows[2])
  })
})

describe('DataTable column filter chips', () => {
  const filterCols: ColumnDef<Row, any>[] = [
    { header: 'Item', accessorKey: 'item', meta: { filterable: true } },
    { header: 'Amount', accessorKey: 'amount' },
  ]
  const renderFilterable = () => render(
    <MantineProvider><DataTable data={rows} columns={filterCols} /></MantineProvider>,
  )

  it('filters to the exact value, shows a chip, and resets', async () => {
    const user = userEvent.setup()
    renderFilterable()
    await user.click(screen.getByRole('button', { name: 'Filter Item = Mid Ore' }))
    expect(bodyRows()).toHaveLength(1)
    expect(screen.getByText('Item: Mid Ore')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Reset filters' }))
    expect(bodyRows()).toHaveLength(3)
  })

  it('toggles the same filter off from the cell icon and removes via the chip', async () => {
    const user = userEvent.setup()
    renderFilterable()
    await user.click(screen.getByRole('button', { name: 'Filter Item = Zebra Ore' }))
    await user.click(screen.getByRole('button', { name: 'Filter Item = Zebra Ore' }))
    expect(bodyRows()).toHaveLength(3)

    await user.click(screen.getByRole('button', { name: 'Filter Item = Zebra Ore' }))
    await user.click(screen.getByRole('button', { name: 'Remove filter Item' }))
    expect(bodyRows()).toHaveLength(3)
  })
})
