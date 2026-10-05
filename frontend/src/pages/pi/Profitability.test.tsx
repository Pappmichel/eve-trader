import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Profitability from './Profitability'

const PAYLOAD = {
  zone: 'highsec', cc_level: 5,
  assumptions: {
    zone: 'highsec', yield_per_head: 1000, effective_yield_per_head: 900, program_hours: 72, interval_hours: 24,
    tax_rate: 0.1, freight_per_m3: 0, valuation: 'sell_orders', hub_region_id: 10000002,
  },
  rows: [{
    product_type_id: 2389, product_name: 'Plasmoids', tier: 1, chain: 'P0-P1', planet_type_id: 11, planet_type: 'Barren',
    radius_km: 5000, factories: 0, heads: 10, launchpads: 1, storages: 0, output_per_day: 1200, profit_per_day: 2500000,
    revenue_per_day: 3000000, costs_per_day: { inputs: 0, export_tax: 1, import_tax: 0, freight: 0, setup: 1 },
    worth_it: false, reason: 'tax', interactions_per_week: 7, isk_per_interaction: 350000, isk_per_m3: 1,
    haul_m3_per_week: 10, market_share: 0.02, trend: { days: 28, change: 0.05, volatility: 0.1, avg_daily_volume: 100 },
    buffer_hours: 24,
  }],
}

function mockFetch(resp: Response) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(resp))
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider><MemoryRouter><Profitability /></MemoryRouter></MantineProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('PI Profitability', () => {
  it('renders the assumptions line and a verdict with a human reason', async () => {
    mockFetch(new Response(JSON.stringify(PAYLOAD), { status: 200 }))
    renderPage()
    expect(await screen.findByText(/Assumptions:/)).toBeInTheDocument()
    expect(await screen.findByText(/Not worth it: Customs tax/)).toBeInTheDocument()
  })

  it('shows the backend message when PI data is missing', async () => {
    mockFetch(new Response(JSON.stringify({ detail: 'PI data missing - run the SDE refresh in Admin first' }), { status: 400 }))
    renderPage()
    expect(await screen.findByText(/PI data missing/)).toBeInTheDocument()
  })
})
