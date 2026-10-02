import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PriceHistory as PriceHistoryData } from '../../api/types'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  tradingApi: { historyTypeIds: vi.fn(), history: vi.fn() },
  sdeApi: { regions: vi.fn() },
}))

import { sdeApi, tradingApi } from '../../api/client'
import PriceHistory from './PriceHistory'
import { normalizeCompare } from './priceHistoryCompare'

function daysAgo(n: number): string {
  return new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10)
}

function history(spanDays: number, base: number): PriceHistoryData {
  const pts = Array.from({ length: spanDays + 1 }, (_, i) => ({
    date: daysAgo(spanDays - i), min_price: base, max_price: base, avg_price: base + i, movement: 1, num_orders: 1,
  }))
  return { hub_region_id: 10000002, reference_region_id: 10000044, hub: pts, reference: pts }
}

function renderPage(url = '/trading/history') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[url]}><PriceHistory /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

beforeEach(() => {
  vi.mocked(tradingApi.historyTypeIds).mockReset().mockResolvedValue([
    { type_id: 1, type_name: 'Alpha' }, { type_id: 2, type_name: 'Beta' },
  ] as never)
  vi.mocked(tradingApi.history).mockReset().mockImplementation(async (id: number) => history(40, id * 100))
  vi.mocked(sdeApi.regions).mockReset().mockResolvedValue([{ region_id: 10000044, region_name: 'Solitude' }])
})

describe('PriceHistory', () => {
  it('disables ranges the data does not cover and keeps All', async () => {
    renderPage()
    await waitFor(() => expect(tradingApi.history).toHaveBeenCalledWith(1))
    // 40 days of data: 7/30 ok, 90 not (> 40 + 7)
    await waitFor(() => expect(screen.getByRole('radio', { name: '90d' })).toBeDisabled())
    expect(screen.getByRole('radio', { name: '30d' })).toBeEnabled()
    expect(screen.getByRole('radio', { name: 'All' })).toBeEnabled()
    expect(screen.getByRole('radio', { name: '30d' })).toBeChecked()
  })

  it('preselects the item from ?item= and shows the region name', async () => {
    renderPage('/trading/history?item=2')
    await waitFor(() => expect(tradingApi.history).toHaveBeenCalledWith(2))
    expect(tradingApi.history).not.toHaveBeenCalledWith(1)
    expect(await screen.findByText(/Solitude \(sell side\)/)).toBeInTheDocument()
  })

  it('fetches one history per compared item', async () => {
    renderPage()
    await waitFor(() => expect(tradingApi.history).toHaveBeenCalledWith(1))
    await userEvent.click(screen.getByPlaceholderText('Add items'))
    await userEvent.click(await screen.findByRole('option', { name: 'Beta' }))
    await waitFor(() => expect(tradingApi.history).toHaveBeenCalledWith(2))
    expect(await screen.findByRole('radio', { name: 'Buy hub' })).toBeInTheDocument()
  })
})

describe('normalizeCompare', () => {
  it('indexes every item to 100 at the first common date', () => {
    const rows = normalizeCompare([
      { id: '1', points: [{ date: '2026-01-01', avg_price: 5 }, { date: '2026-01-02', avg_price: 10 }, { date: '2026-01-03', avg_price: 20 }] },
      { id: '2', points: [{ date: '2026-01-02', avg_price: 1000 }, { date: '2026-01-03', avg_price: 500 }] },
    ])
    expect(rows.map((r) => r.date)).toEqual(['2026-01-02', '2026-01-03'])
    expect(rows[0].i1).toBe(100)
    expect(rows[0].i2).toBe(100)
    expect(rows[1].i1).toBe(200)
    expect(rows[1].i2).toBe(50)
    expect(rows[1].p2).toBe(500)
  })
})
