import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { doctrineApi } from '../../api/client'
import type { DoctrineRealizedContractDetail, DoctrineRealizedSummary } from '../../api/types'
import RealizedProfit from './RealizedProfit'

vi.mock('../../api/client', () => ({
  doctrineApi: {
    realizedSummary: vi.fn(),
    realizedFittings: vi.fn(),
    realizedContracts: vi.fn(),
    realizedContract: vi.fn(),
    realizedWarnings: vi.fn(),
    startRealizedRebuild: vi.fn(),
    realizedRebuildStatus: vi.fn(),
  },
}))

const flags = {
  estimated: false, price_missing: false, assumed_me: false, assumed_decryptor: false,
  coverage: 1, tax_missing: false, detail_missing: false, no_structure_bonus: null,
}

function summary(partial: Partial<DoctrineRealizedSummary> = {}): DoctrineRealizedSummary {
  return {
    period: { start: '2026-09-17', end: '2026-10-17', since_t0: false, days: 30 },
    status: {
      empty: false, needs_rebuild: false, sharing_changed: false, stale: false, hint: null,
      cutover_t0: '2026-09-01T00:00:00+00:00', archive_since: null, last_recompute: null,
      last_wallet_sync: null, last_jobs_sync: null, run_id: 1,
    },
    contracts: 1, revenue: 100, cost_of_goods_sold: 35, realized_profit: 65, margin: 0.65,
    coverage: 1, estimated: false, price_missing: false, production_saving: 65,
    cost_by_origin: [
      { kind: 'produced', location_id: null, location_name: null, cost: 15, quantity: 1 },
      { kind: 'market', location_id: 60000042, location_name: 'Home', cost: 20, quantity: 1 },
    ],
    by_day: [{ date: '2026-10-04', revenue: 100, cost: 35, profit: 65 }],
    flags, ...partial,
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

describe('Doctrine realized profit page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(doctrineApi.realizedRebuildStatus).mockResolvedValue({
      run_id: null, job_name: 'rebuild_realized', status: 'idle', result: null, error: null,
    })
    vi.mocked(doctrineApi.realizedSummary).mockResolvedValue(summary())
    vi.mocked(doctrineApi.realizedFittings).mockResolvedValue({
      needs_rebuild: false,
      rows: [{
        fitting_id: 'fit', fitting_name: 'Stabber Fleet', contracts: 1, revenue: 100, cost: 35,
        profit: 65, produced_cost: 15, bought_cost: 20, avg_price: 100, avg_cost: 35, avg_profit: 65,
        produced_share: 15 / 35, bought_share: 20 / 35,
      }],
    })
    vi.mocked(doctrineApi.realizedContracts).mockResolvedValue({
      needs_rebuild: false,
      rows: [{
        sale_ref: 'contract:70', contract_id: 70, sale_date: '2026-10-04T12:00:00+00:00',
        fitting_id: 'fit', fitting_name: 'Stabber Fleet', buyer_id: 999, buyer_name: 'Buyer',
        issuer_id: 1, issuer_name: 'Alice', price: 100, revenue: 100, cost: 35, profit: 65,
        margin: 0.65, coverage: 1, warnings: [], production_saving: 65, before_archive: false, flags,
      }],
      before_archive: [],
    })
  })

  it('shows the recompute hint and no profit figure when a rebuild is required', async () => {
    vi.mocked(doctrineApi.realizedSummary).mockResolvedValue(summary({
      realized_profit: 0, revenue: 0, margin: null, contracts: 0,
      status: {
        empty: true, needs_rebuild: true, sharing_changed: false, stale: false,
        hint: 'Recompute to build realized profit from the archive.',
        cutover_t0: null, archive_since: null, last_recompute: null,
        last_wallet_sync: null, last_jobs_sync: null, run_id: null,
      },
    }))
    renderPage()
    expect(await screen.findByText(/Recompute to build realized profit/)).toBeInTheDocument()
    expect(screen.queryByText('65 ISK')).toBeNull()
    expect(screen.queryByText('Stabber Fleet')).toBeNull()
  })

  it('shows the freight split when the summary carries it', async () => {
    vi.mocked(doctrineApi.realizedSummary).mockResolvedValue(summary({
      freight_from_couriers: 40, freight_configured: 5,
    }))
    renderPage()
    expect(await screen.findByText('Freight from couriers: 40 ISK')).toBeInTheDocument()
    expect(screen.getByText('Freight (configured rate): 5 ISK')).toBeInTheDocument()
  })

  it('shows the summary, origin split and fitting table', async () => {
    renderPage()
    expect((await screen.findAllByText('65 ISK')).length).toBeGreaterThan(0)
    expect(screen.getByText(/Own production: 15 ISK/)).toBeInTheDocument()
    expect(screen.getByText(/Market · Home: 20 ISK/)).toBeInTheDocument()
    expect(screen.getAllByText('Stabber Fleet').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Buyer').length).toBeGreaterThan(0)
  })

  it('asks for the selected period', async () => {
    renderPage()
    await screen.findAllByText('Stabber Fleet')
    await userEvent.click(screen.getByRole('radio', { name: '7 days' }))
    await waitFor(() => expect(doctrineApi.realizedSummary).toHaveBeenCalledWith('days=7'))
    expect(doctrineApi.realizedFittings).toHaveBeenCalledWith('days=7')
    expect(doctrineApi.realizedContracts).toHaveBeenCalledWith('days=7')
  })

  it('drills into a contract and shows the production chain', async () => {
    const detail: DoctrineRealizedContractDetail = {
      sale_ref: 'contract:70', contract_id: 70, sale_date: '2026-10-04T12:00:00+00:00',
      fitting_id: 'fit', fitting_name: 'Stabber Fleet', buyer_id: 999, buyer_name: 'Buyer',
      issuer_id: 1, issuer_name: 'Alice', price: 100, revenue: 100, cost: 35, profit: 65,
      margin: 0.65, coverage: 1, warnings: [], production_saving: 65, before_archive: false, flags,
      cost_by_origin: [],
      lines: [{
        type_id: 88012, type_name: 'Hull', quantity: 1, origin: 'produced', unit_cost: 15, cost: 15,
        origins: { produced: 15 },
        chain: [{
          kind: 'job', origin: 'produced', type_id: 88012, type_name: 'Hull', quantity: 1, unit: 'item',
          unit_cost: 15, unit_price: null, reference_price: null, date: null, end_date: null,
          location_id: null, freight: null, freight_label: null, freight_rate_per_m3: null,
          source_ref: null, job_id: 77, job_ref: 'job:1', activity_id: 1, runs: 1, successful_runs: 1,
          state: 'finished', job_cost: 5, facility_id: null, output_quantity: 1, cost_total: 15,
          share: 1, cost_share: 15, sale_ref: null, source: null, flags, children: [],
        }],
      }],
    }
    vi.mocked(doctrineApi.realizedContract).mockResolvedValue(detail)
    renderPage()
    const buyers = await screen.findAllByText('Buyer')
    await userEvent.click(buyers[buyers.length - 1])
    expect(await screen.findByText('Hull production chain')).toBeInTheDocument()
    expect(screen.getAllByText(/Production saving/).length).toBeGreaterThan(0)
    expect(doctrineApi.realizedContract).toHaveBeenCalledWith('contract:70')
  })
})
