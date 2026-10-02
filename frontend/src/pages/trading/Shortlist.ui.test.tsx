import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ShortlistRow } from '../../api/types'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  tradingApi: {
    shortlistSnapshot: vi.fn(),
    shortlistTrends: vi.fn(),
    settings: vi.fn(),
    updateSettings: vi.fn(),
    recategorizeShortlist: vi.fn(),
  },
}))

import { tradingApi } from '../../api/client'
import Shortlist from './Shortlist'

function row(over: Partial<ShortlistRow>): ShortlistRow {
  return {
    item: 'Item', category: 'Ship', landed_cost: 1_000_000, net_sell: 1_300_000, sell_volume: 10,
    own_orders_remaining: 0, profit_per_unit: 300_000, margin: 0.3, profit_per_m3: 100,
    decision: 'Import', active: true, item_id: 1, volume_m3: 5, jita_sell: 900_000, import_cost: 50_000,
    meta_level: 0, days_until_deactivation: null, avg_daily_volume: 20, ...over,
  }
}

const rows: ShortlistRow[] = [
  row({ item: 'Hobgoblin II', item_id: 1, decision: 'Import' }),
  row({ item: 'Nanite Repair Paste', item_id: 2, decision: 'Skip' }),
  row({ item: 'Warp Scrambler II', item_id: 3, decision: 'Import' }),
]

function renderShortlist(initialUrl = '/trading/shortlist') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialUrl]}>
          <Shortlist />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

const bodyItemNames = () =>
  screen.getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0].textContent)

describe('Trading Shortlist interactivity', () => {
  beforeEach(() => {
    vi.mocked(tradingApi.shortlistSnapshot).mockResolvedValue(rows)
    vi.mocked(tradingApi.shortlistTrends).mockResolvedValue({})
    vi.mocked(tradingApi.settings).mockResolvedValue(undefined as never)
  })

  it('filters to one status when its badge is clicked and resets on a second click', async () => {
    const user = userEvent.setup()
    renderShortlist()
    await waitFor(() => expect(bodyItemNames()).toHaveLength(3))

    await user.click(screen.getAllByRole('button', { name: 'Filter by status Skip' })[0])
    await waitFor(() => expect(bodyItemNames()).toEqual(['Nanite Repair Paste']))

    await user.click(screen.getAllByRole('button', { name: 'Filter by status Skip' })[0])
    await waitFor(() => expect(bodyItemNames()).toHaveLength(3))
  })

  it('opens the detail drawer on row click and links to Price History', async () => {
    const user = userEvent.setup()
    renderShortlist()
    await user.click(await screen.findByText('Nanite Repair Paste'))

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Pricing')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open in Price History' })).toHaveAttribute('href', '/trading/history?item=2')
  })

  it('opens the drawer straight from ?item= in the URL', async () => {
    renderShortlist('/trading/shortlist?item=3')
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(within(screen.getByRole('dialog')).getByText('Warp Scrambler II')).toBeInTheDocument()
  })
})
