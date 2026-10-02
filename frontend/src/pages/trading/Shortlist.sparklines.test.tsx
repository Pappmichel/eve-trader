import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ShortlistRow } from '../../api/types'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  tradingApi: {
    shortlistSnapshot: vi.fn(),
    shortlistTrends: vi.fn(),
    sparklines: vi.fn(),
    settings: vi.fn(),
    updateSettings: vi.fn(),
    recategorizeShortlist: vi.fn(),
  },
}))

import { tradingApi } from '../../api/client'
import Shortlist from './Shortlist'

const mkRow = (id: number): ShortlistRow => ({
  item: `Item ${id}`, category: 'Ship', landed_cost: 1000, net_sell: 1300, breakeven_buy_price: 1250, sell_volume: 10,
  own_orders_remaining: 0, profit_per_unit: 300, margin: 0.3, profit_per_m3: 100, decision: 'Import', active: true,
  item_id: id, volume_m3: 5, jita_sell: 900, import_cost: 50, meta_level: 0, days_until_deactivation: null, avg_daily_volume: 20,
})

describe('Shortlist 30d sparkline column', () => {
  beforeEach(() => {
    vi.mocked(tradingApi.shortlistSnapshot).mockResolvedValue([mkRow(1), mkRow(2)])
    vi.mocked(tradingApi.shortlistTrends).mockResolvedValue({})
    vi.mocked(tradingApi.settings).mockResolvedValue(undefined as never)
    vi.mocked(tradingApi.sparklines).mockReset()
    vi.mocked(tradingApi.sparklines).mockResolvedValue({
      '1': { hub: [['2026-09-10', 5], ['2026-10-01', 6]], ref: [['2026-09-10', 10], ['2026-10-01', 12]] },
      '2': { hub: [], ref: [['2026-10-01', 12]] },
    })
  })

  it('requests only the visible rows in one batch and renders a line or a dash per row', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MantineProvider><QueryClientProvider client={client}><MemoryRouter><Shortlist /></MemoryRouter></QueryClientProvider></MantineProvider>,
    )
    await waitFor(() => expect(tradingApi.sparklines).toHaveBeenCalled())
    expect(tradingApi.sparklines).toHaveBeenCalledTimes(1)
    expect(tradingApi.sparklines).toHaveBeenCalledWith([1, 2])
    await waitFor(() => expect(screen.getAllByTestId('sparkline')).toHaveLength(1))
    expect(screen.getByTestId('sparkline').getAttribute('data-direction')).toBe('up')
    expect(screen.getByRole('columnheader', { name: /30d/ })).toBeTruthy()
  })
})
