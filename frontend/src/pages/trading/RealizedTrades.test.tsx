import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { tradingApi } from '../../api/client'
import type {
  TradingRealizedItemDetail, TradingRealizedItemList, TradingRealizedSaleDetail, TradingRealizedSummary,
} from '../../api/types'
import RealizedTrades from './RealizedTrades'

vi.mock('../../api/client', () => ({
  tradingApi: {
    realizedSummary: vi.fn(),
    realizedItems: vi.fn(),
    realizedItem: vi.fn(),
    realizedSale: vi.fn(),
    realizedInventory: vi.fn(),
    realizedWarnings: vi.fn(),
    startRealizedRebuild: vi.fn(),
    realizedRebuildStatus: vi.fn(),
  },
}))

const flags = {
  estimated: false, price_missing: false, assumed_me: false, assumed_decryptor: false,
  coverage: 1, tax_missing: false, detail_missing: false, no_structure_bonus: null,
}

function summary(partial: Partial<TradingRealizedSummary> = {}): TradingRealizedSummary {
  return {
    period: { start: '2026-09-17', end: '2026-10-17', since_t0: false, days: 30 },
    status: {
      empty: false, needs_rebuild: false, sharing_changed: false, stale: false, hint: null,
      cutover_t0: '2026-09-01T00:00:00+00:00', archive_since: null, last_recompute: null,
      last_wallet_sync: null, last_jobs_sync: null, run_id: 1,
    },
    revenue: 40, cost_of_goods_sold: 10, realized_profit: 30, margin: 0.75, coverage: 1,
    estimated: false, units_sold: 1, sale_count: 1, price_missing: false, tax_missing_sales: 0,
    cost_structure: [], by_day: [{ date: '2026-10-04', revenue: 40, cost: 10, profit: 30 }],
    flags,
    portfolio_average_margin: 0.2,
    portfolio_daily_profit_volatility: 4,
    ...partial,
  }
}

function items(): TradingRealizedItemList {
  return {
    period: { start: null, end: null, since_t0: false, days: 30 },
    needs_rebuild: false,
    rows: [{
      type_id: 34, type_name: 'Tritanium', units_sold: 2, avg_buy_landed: 5, avg_net_sell: 8,
      profit: 6, margin: null, coverage: 1, warnings: [], flags,
    }],
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <RealizedTrades />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('Trading realized profit page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(tradingApi.realizedRebuildStatus).mockResolvedValue({
      run_id: null, job_name: 'rebuild_realized', status: 'idle', result: null, error: null,
    })
    vi.mocked(tradingApi.realizedInventory).mockResolvedValue({ needs_rebuild: false, rows: [] })
    vi.mocked(tradingApi.realizedWarnings).mockResolvedValue({ needs_rebuild: false, rows: [] })
    vi.mocked(tradingApi.realizedSummary).mockResolvedValue(summary())
    vi.mocked(tradingApi.realizedItems).mockResolvedValue(items())
  })

  it('shows the recompute hint and no profit figure when a rebuild is required', async () => {
    vi.mocked(tradingApi.realizedSummary).mockResolvedValue(summary({
      realized_profit: 0, revenue: 0, margin: null,
      status: {
        empty: true, needs_rebuild: true, sharing_changed: false, stale: false,
        hint: 'Recompute to build realized profit from the archive.',
        cutover_t0: null, archive_since: null, last_recompute: null,
        last_wallet_sync: null, last_jobs_sync: null, run_id: null,
      },
    }))
    vi.mocked(tradingApi.realizedItems).mockResolvedValue({ ...items(), rows: [], needs_rebuild: true })
    renderPage()
    expect(await screen.findByText(/Recompute to build realized profit/)).toBeInTheDocument()
    expect(screen.queryByText('Realized profit')).toBeNull()
  })

  it('shows the freight split from the API cost structure', async () => {
    vi.mocked(tradingApi.realizedSummary).mockResolvedValue(summary({
      cost_structure: [
        { key: 'freight_courier', label: 'Freight from couriers', amount: 160 },
        { key: 'freight_configured', label: 'Freight (configured rate)', amount: 20 },
      ],
    }))
    renderPage()
    expect(await screen.findByText('Freight from couriers: 160 ISK')).toBeInTheDocument()
    expect(screen.getByText('Freight (configured rate): 20 ISK')).toBeInTheDocument()
  })

  it('shows the 30-day margin and volatility from the portfolio figures', async () => {
    renderPage()
    expect(await screen.findByText('Average margin')).toBeInTheDocument()
    expect(screen.getByText('20%')).toBeInTheDocument()
    expect(screen.getByText('Daily profit volatility')).toBeInTheDocument()
    expect(screen.getByText('4 ISK')).toBeInTheDocument()
  })

  it('shows API profit and leaves a null margin as a dash', async () => {
    renderPage()
    expect(await screen.findByText('30 ISK')).toBeInTheDocument()
    expect(screen.getByText('Tritanium')).toBeInTheDocument()
    expect(screen.getByText('6 ISK')).toBeInTheDocument()
    expect(screen.getAllByText('–').length).toBeGreaterThan(0)
  })

  it('asks for the selected period', async () => {
    renderPage()
    await screen.findByText('Tritanium')
    await userEvent.click(screen.getByRole('radio', { name: '7 days' }))
    await waitFor(() => expect(tradingApi.realizedSummary).toHaveBeenCalledWith('days=7'))
    expect(tradingApi.realizedItems).toHaveBeenCalledWith('days=7')
  })

  it('drills from an item to its sale and the purchase lot', async () => {
    const detail: TradingRealizedItemDetail = {
      period: items().period,
      needs_rebuild: false,
      item: items().rows[0],
      sales: [{
        sale_ref: 'tx:1', type_id: 34, type_name: 'Tritanium', quantity: 2,
        revenue: 16, cost: 10, profit: 6, margin: 0.375, coverage: 1,
        sale_date: '2026-10-04T12:00:00+00:00', gross: 16, sales_tax: 0, broker_fee: 0,
        location_id: 1, location_name: 'C-J', net_price: 8, warnings: [], flags, source: 'market',
      }],
    }
    const sale: TradingRealizedSaleDetail = {
      sale: detail.sales[0],
      lots: [{
        quantity: 2, origin: 'bought', layer_date: '2026-10-01T12:00:00+00:00',
        location_id: 60003760, location_name: 'Jita 4-4', unit_price: 5, freight: 100, fees: 1, unit_cost: 5,
      }],
    }
    vi.mocked(tradingApi.realizedItem).mockResolvedValue(detail)
    vi.mocked(tradingApi.realizedSale).mockResolvedValue(sale)
    renderPage()
    await userEvent.click(await screen.findByText('Tritanium'))
    expect(await screen.findByText('C-J')).toBeInTheDocument()
    await userEvent.click(screen.getByText('C-J'))
    expect(await screen.findByText('Jita 4-4')).toBeInTheDocument()
    expect(screen.getByText('100 ISK')).toBeInTheDocument()
    expect(tradingApi.realizedSale).toHaveBeenCalledWith('tx:1')
  })
})
