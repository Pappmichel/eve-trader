import { render, screen, within } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Shortlist from './Shortlist'
import { tradingApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  tradingApi: {
    shortlistSnapshot: vi.fn(),
    shortlistTrends: vi.fn(),
    settings: vi.fn(),
    updateSettings: vi.fn(),
    recategorizeShortlist: vi.fn(),
  },
}))

const ROW = {
  item: 'Tritanium', category: 'Minerals', landed_cost: 100, net_sell: 150,
  sell_volume: 1000, own_orders_remaining: 0, profit_per_unit: 50, margin: 0.5,
  profit_per_m3: 500, decision: 'Import', active: true, item_id: 1, volume_m3: 0.01,
  jita_sell: 95, import_cost: 5, meta_level: null, days_until_deactivation: null,
  avg_daily_volume: 10000, breakeven_buy_price: 142,
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider><MemoryRouter><Shortlist /></MemoryRouter></MantineProvider>
    </QueryClientProvider>,
  )
}

// GitHub issue #221: the column shows the highest hub buy price that still
// breaks even (server-computed breakeven_buy_price), not net_sell.
describe('Shortlist breakeven price column', () => {
  it('shows breakeven_buy_price under the Breakeven Buy header, distinct from Cost and Sale', async () => {
    vi.mocked(tradingApi.shortlistSnapshot).mockResolvedValue([ROW] as any)
    vi.mocked(tradingApi.shortlistTrends).mockResolvedValue({})
    vi.mocked(tradingApi.settings).mockResolvedValue({ jita_region_id: 10000002 } as any)
    renderPage()

    // Two confirmed-real pitfalls writing this test, both about *when* the
    // real table exists, not whether this feature works:
    // 1. "Tritanium" is ambiguous once the row has both profit_per_unit and
    //    avg_daily_volume (the table cell AND the "Top Imports" chart's own
    //    axis label) - a plain/matcher-function getByText must stay scoped
    //    to a <td> specifically.
    // 2. DataTable.tsx's isLoading branch renders its own, separate <table>
    //    (a skeleton) - grabbing a `table` handle via findByRole('table')
    //    early can capture that one, which is then unmounted (not updated
    //    in place) once real data replaces it, leaving `within(table)`
    //    searching an element no longer in the document. Finding the real
    //    cell directly (one self-polling findByText) sidesteps both.
    const cell = await screen.findByText(
      (content, el) => el?.tagName === 'TD' && content === 'Tritanium',
    )
    const table = cell.closest('table') as HTMLElement
    // DataTable.tsx sets a th's title to its header label regardless of
    // whether it's sortable (role="button" then overrides the implicit
    // "columnheader" role - see DataTable.test.tsx) - title is the stable,
    // role-independent way to find a specific column header in a test.
    const breakevenHeader = within(table).getByTitle('Breakeven Buy (Jita)')
    const headerRow = breakevenHeader.closest('tr') as HTMLElement
    const headerIdx = Array.from(headerRow.children).indexOf(breakevenHeader)

    const bodyRow = cell.closest('tr') as HTMLElement
    const cells = within(bodyRow).getAllByRole('cell')
    expect(cells[headerIdx].textContent).toContain('142') // breakeven_buy_price
    expect(within(table).getByTitle('Cost (Jita)')).toBeInTheDocument()
    expect(within(table).getByTitle('Sale (Structure)')).toBeInTheDocument()
  })

  it('labels the cost column after the configured buy hub', async () => {
    vi.mocked(tradingApi.shortlistSnapshot).mockResolvedValue([ROW] as any)
    vi.mocked(tradingApi.shortlistTrends).mockResolvedValue({})
    vi.mocked(tradingApi.settings).mockResolvedValue({ jita_region_id: 10000043 } as any)
    renderPage()

    await screen.findByText((content, el) => el?.tagName === 'TD' && content === 'Tritanium')
    expect(await screen.findByTitle('Cost (Amarr)')).toBeInTheDocument()
  })
})
