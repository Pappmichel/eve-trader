import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { stationTradingApi } from '../../api/client'
import type {
  StationRealizedItemDetail, StationRealizedItemList, StationRealizedSaleDetail, StationRealizedSummary,
} from '../../api/types'
import RealizedProfit from './RealizedProfit'

vi.mock('../../api/client', () => ({
  stationTradingApi: {
    realizedSummary: vi.fn(),
    realizedItems: vi.fn(),
    realizedItem: vi.fn(),
    realizedSale: vi.fn(),
    realizedPosition: vi.fn(),
    realizedWarnings: vi.fn(),
    startRealizedRebuild: vi.fn(),
    realizedRebuildStatus: vi.fn(),
  },
}))

const flags = {
  estimated: false, price_missing: false, assumed_me: false, assumed_decryptor: false,
  coverage: 1, tax_missing: false, detail_missing: false, no_structure_bonus: null,
}

function summary(partial: Partial<StationRealizedSummary> = {}): StationRealizedSummary {
  return {
    period: { start: '2026-09-17', end: '2026-10-17', since_t0: false, days: 30 },
    status: {
      empty: false, needs_rebuild: false, sharing_changed: false, stale: false, hint: null,
      cutover_t0: '2026-09-01T00:00:00+00:00', archive_since: null, last_recompute: null,
      last_wallet_sync: null, last_jobs_sync: null, run_id: 1,
    },
    revenue: 12, cost_of_goods_sold: 5, realized_profit: 7, margin: 7 / 12, coverage: 1,
    estimated: false, units_sold: 1, sale_count: 1, avg_buy: 5, avg_sell_net: 12, spread: 7,
    turnover_days: 3, price_missing: false, tax_missing_sales: 1,
    fees_by_day: [{ date: '2026-10-04', broker_fee: 0, sales_tax: null, total: 0, tax_missing_sales: 1 }],
    by_day: [{ date: '2026-10-04', revenue: 12, cost: 5, profit: 7 }],
    open_position_cost: 5, open_position_units: 1, flags, ...partial,
  }
}

function items(): StationRealizedItemList {
  return {
    period: { start: null, end: null, since_t0: false, days: 30 },
    needs_rebuild: false,
    rows: [{
      type_id: 34, type_name: 'Tritanium', units_sold: 1, avg_buy: 5, avg_sell_net: 12,
      spread: 7, profit: 7, margin: null, turnover_days: 3, coverage: 1, warnings: [], flags,
    }],
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <RealizedProfit />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('Station Trading realized profit page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(stationTradingApi.realizedRebuildStatus).mockResolvedValue({
      run_id: null, job_name: 'rebuild_realized', status: 'idle', result: null, error: null,
    })
    vi.mocked(stationTradingApi.realizedPosition).mockResolvedValue({
      needs_rebuild: false, cost: 5, units: 1,
      rows: [{ type_id: 34, type_name: 'Tritanium', quantity: 1, cost_per_unit: 5, total: 5, flags }],
    })
    vi.mocked(stationTradingApi.realizedSummary).mockResolvedValue(summary())
    vi.mocked(stationTradingApi.realizedItems).mockResolvedValue(items())
  })

  it('shows the recompute hint and no profit figure when a rebuild is required', async () => {
    vi.mocked(stationTradingApi.realizedSummary).mockResolvedValue(summary({
      realized_profit: 0, revenue: 0, margin: null, spread: null, avg_buy: null, avg_sell_net: null,
      turnover_days: null, open_position_cost: null, fees_by_day: [],
      status: {
        empty: true, needs_rebuild: true, sharing_changed: false, stale: false,
        hint: 'Recompute to build realized profit from the archive.',
        cutover_t0: null, archive_since: null, last_recompute: null,
        last_wallet_sync: null, last_jobs_sync: null, run_id: null,
      },
    }))
    vi.mocked(stationTradingApi.realizedItems).mockResolvedValue({ ...items(), rows: [], needs_rebuild: true })
    renderPage()
    expect(await screen.findByText(/Recompute to build realized profit/)).toBeInTheDocument()
    expect(screen.queryByText('Realized profit')).toBeNull()
    expect(screen.queryByText('7 ISK')).toBeNull()
  })

  it('shows API spread, fees and the open position, and leaves a null margin as a dash', async () => {
    renderPage()
    expect((await screen.findAllByText('7 ISK')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Tritanium').length).toBeGreaterThan(0)
    expect(screen.getAllByText('3 d').length).toBeGreaterThan(0)
    expect(screen.getByText('Fees per day')).toBeInTheDocument()
    expect(screen.getByText('Open position')).toBeInTheDocument()
    expect(screen.getAllByText('–').length).toBeGreaterThan(0)
  })

  it('asks for the selected period', async () => {
    renderPage()
    await screen.findByText('Fees per day')
    await userEvent.click(screen.getByRole('radio', { name: '7 days' }))
    await waitFor(() => expect(stationTradingApi.realizedSummary).toHaveBeenCalledWith('days=7'))
    expect(stationTradingApi.realizedItems).toHaveBeenCalledWith('days=7')
  })

  it('drills from an item to its sale and the buy lot', async () => {
    const detail: StationRealizedItemDetail = {
      period: items().period,
      needs_rebuild: false,
      item: items().rows[0],
      sales: [{
        sale_ref: 'tx:1', type_id: 34, type_name: 'Tritanium', quantity: 1,
        revenue: 12, cost: 5, profit: 7, margin: 7 / 12, coverage: 1,
        sale_date: '2026-10-04T12:00:00+00:00', gross: 12, sales_tax: null, broker_fee: 0,
        location_id: 60003760, location_name: 'Jita 4-4', net_price: 12, warnings: [],
        flags, source: 'market',
      }],
    }
    const sale: StationRealizedSaleDetail = {
      sale: detail.sales[0],
      lots: [{
        quantity: 1, origin: 'bought', layer_date: '2026-10-01T12:00:00+00:00',
        location_id: 60003760, location_name: 'Jita 4-4', unit_price: 5, freight: 0, fees: 0, unit_cost: 5,
      }],
    }
    vi.mocked(stationTradingApi.realizedItem).mockResolvedValue(detail)
    vi.mocked(stationTradingApi.realizedSale).mockResolvedValue(sale)
    renderPage()
    const names = await screen.findAllByText('Tritanium')
    await userEvent.click(names[0])
    expect(await screen.findByText('2026-10-04T12:00')).toBeInTheDocument()
    await userEvent.click(screen.getByText('2026-10-04T12:00'))
    expect(await screen.findByText('Buy lots')).toBeInTheDocument()
    expect(stationTradingApi.realizedSale).toHaveBeenCalledWith('tx:1')
    expect(screen.getByText('2026-10-01T12:00')).toBeInTheDocument()
  })
})
