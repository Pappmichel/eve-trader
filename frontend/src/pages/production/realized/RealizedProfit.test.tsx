import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charactersApi, gateApi, productionApi } from '../../../api/client'
import type { RealizedFlags, RealizedStatus, RealizedSummary } from '../../../api/types'
import { resetAutoRebuildForTests } from './period'
import RealizedProfit from './RealizedProfit'

vi.mock('../../../api/client', () => {
  class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    productionApi: {
      realizedSummary: vi.fn(),
      realizedProducts: vi.fn(),
      realizedProduct: vi.fn(),
      realizedSaleChain: vi.fn(),
      realizedJobs: vi.fn(),
      realizedInventory: vi.fn(),
      realizedWarnings: vi.fn(),
      realizedOpening: vi.fn(),
      realizedEntries: vi.fn(),
      realizedArchiveStatus: vi.fn(),
      startRealizedRebuild: vi.fn(),
      realizedRebuildStatus: vi.fn(),
      reapplyRealizedFreight: vi.fn(),
      setRealizedOpeningCost: vi.fn(),
      addRealizedEntry: vi.fn(),
      deleteRealizedEntry: vi.fn(),
      structureNames: vi.fn(),
      itemNameOptions: vi.fn(),
    },
    charactersApi: { owners: vi.fn() },
    gateApi: { status: vi.fn() },
  }
})

function flags(partial: Partial<RealizedFlags> = {}): RealizedFlags {
  return {
    estimated: false, price_missing: false, assumed_me: false, assumed_decryptor: false,
    coverage: null, tax_missing: false, detail_missing: false, no_structure_bonus: null,
    ...partial,
  }
}

function status(partial: Partial<RealizedStatus> = {}): RealizedStatus {
  return {
    empty: false, needs_rebuild: false, sharing_changed: false, stale: false, hint: null,
    cutover_t0: '2026-09-01T00:00:00Z', archive_since: '2026-09-01T00:00:00Z',
    last_recompute: '2026-10-01T00:00:00Z', last_wallet_sync: null, last_jobs_sync: null,
    run_id: 1,
    ...partial,
  }
}

function summary(partial: Partial<RealizedSummary> = {}): RealizedSummary {
  return {
    period: { start: '2026-09-17T00:00:00Z', end: '2026-10-17T00:00:00Z', since_t0: false, days: 30 },
    status: status(),
    revenue: 1000, cost_of_goods_sold: 400, realized_profit: 600, margin: 0.6, coverage: 0.95,
    estimated: false, units_sold: 2, price_missing: false, tax_missing_sales: 0,
    fees_without_sale: 1, fees_on_other_trading: 2, capital_in_bpos: 3, inventory_at_cost: 4,
    cancelled_job_losses: 5, failed_invention_losses: 6, manual_costs: 7, manual_revenues: 8,
    profit_including_manual: 601, cost_structure: [], by_day: [],
    balances_as_of_rebuild: ['capital_in_bpos', 'inventory_at_cost'],
    flags: flags(),
    ...partial,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <MantineProvider>
      <ModalsProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <RealizedProfit />
          </MemoryRouter>
        </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>,
  )
  return client
}

describe('RealizedProfit', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    resetAutoRebuildForTests()
    vi.mocked(productionApi.realizedRebuildStatus).mockResolvedValue({
      run_id: null, status: 'idle', job_name: 'rebuild_realized', progress: null, result: null, error: null,
    })
    vi.mocked(productionApi.startRealizedRebuild).mockResolvedValue({
      run_id: 'run-1', status: 'running', job_name: 'rebuild_realized', tool: 'production',
    })
    vi.mocked(productionApi.realizedProducts).mockResolvedValue({
      period: { start: null, end: null, since_t0: false, days: 30 }, needs_rebuild: false, rows: [],
    })
    vi.mocked(productionApi.realizedJobs).mockResolvedValue({ cutover_t0: null, needs_rebuild: false, rows: [] })
    vi.mocked(productionApi.realizedInventory).mockResolvedValue({ needs_rebuild: false, rows: [] })
    vi.mocked(productionApi.realizedWarnings).mockResolvedValue({ needs_rebuild: false, groups: [] })
    vi.mocked(productionApi.realizedOpening).mockResolvedValue({ cutover_t0: null, needs_rebuild: false, rows: [] })
    vi.mocked(productionApi.realizedEntries).mockResolvedValue({ rows: [] })
    vi.mocked(productionApi.realizedArchiveStatus).mockResolvedValue({ rows: [] })
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([])
    vi.mocked(charactersApi.owners).mockResolvedValue([])
    vi.mocked(gateApi.status).mockResolvedValue({
      enabled: true, logged_in: true, character_name: 'Pilot', tools: ['production'],
      suspended: false, pending_access_requests: null,
    })
    vi.mocked(productionApi.realizedSummary).mockResolvedValue(summary())
  })

  it('shows the archive hint instead of KPI cards when there is nothing to recompute', async () => {
    vi.mocked(productionApi.realizedSummary).mockResolvedValue(summary({
      status: status({
        empty: true, needs_rebuild: true, cutover_t0: null, archive_since: null, run_id: null,
        hint: 'Recompute to build realized profit from the archive.',
      }),
    }))
    renderPage()
    expect(await screen.findByText('Recompute to build realized profit from the archive.')).toBeInTheDocument()
    expect(screen.queryByText('Cost of goods sold')).toBeNull()
    await waitFor(() => expect(productionApi.startRealizedRebuild).not.toHaveBeenCalled())
  })

  it('shows the sharing-changed hint instead of an empty result table', async () => {
    vi.mocked(productionApi.realizedSummary).mockResolvedValue(summary({
      status: status({
        empty: true, needs_rebuild: true, sharing_changed: true,
        hint: 'Sharing with Production changed since the last recompute. Recompute to update.',
      }),
    }))
    vi.mocked(productionApi.realizedProducts).mockResolvedValue({
      period: { start: null, end: null, since_t0: false, days: 30 }, needs_rebuild: true, rows: [],
    })
    renderPage()
    expect(await screen.findByText(/Sharing with Production changed/)).toBeInTheDocument()
    expect(screen.queryByText('No data.')).toBeNull()
  })

  it('shows the recompute hint on a list that needs a rebuild', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedProducts).mockResolvedValue({
      period: { start: null, end: null, since_t0: false, days: 30 }, needs_rebuild: true, rows: [],
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Products' }))
    expect(await screen.findByText('Recompute to build realized profit from the archive.')).toBeInTheDocument()
    expect(screen.queryByText('No data.')).toBeNull()
  })

  it('recomputes once when the report is stale and does not loop after a refetch', async () => {
    let loads = 0
    vi.mocked(productionApi.realizedSummary).mockImplementation(async () => {
      loads += 1
      return summary({ status: status({ stale: true, empty: false, needs_rebuild: false }) })
    })
    const client = renderPage()
    await waitFor(() => expect(productionApi.startRealizedRebuild).toHaveBeenCalledTimes(1))
    await client.invalidateQueries({ queryKey: ['production', 'realized', 'summary'] })
    await waitFor(() => expect(loads).toBeGreaterThan(1))
    expect(productionApi.startRealizedRebuild).toHaveBeenCalledTimes(1)
  })

  it('confirms a freight dry run before applying it and then offers a recompute', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.reapplyRealizedFreight).mockImplementation(async (dryRun: boolean) => (
      dryRun
        ? { dry_run: true, would_update: 3, freight_rate_per_m3: 500, freight_label: 'configured rate' }
        : { dry_run: false, updated: 3, freight_rate_per_m3: 500, freight_label: 'configured rate', rebuild_required: true }
    ))
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Ledger' }))
    await user.click(await screen.findByRole('button', { name: 'Re-apply current freight rate' }))
    expect(await screen.findByText(/3 purchases would change to 500 ISK\/m³ \(configured rate\)/)).toBeInTheDocument()
    expect(productionApi.reapplyRealizedFreight).toHaveBeenCalledTimes(1)
    expect(productionApi.reapplyRealizedFreight).toHaveBeenCalledWith(true)
    await user.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(productionApi.reapplyRealizedFreight).toHaveBeenCalledWith(false))
    expect(await screen.findByText(/Recompute to include it in realized profit/)).toBeInTheDocument()
  })

  it('does not read Characters owners without the characters grant', async () => {
    renderPage()
    await screen.findByText('Cost of goods sold')
    await waitFor(() => expect(gateApi.status).toHaveBeenCalled())
    expect(charactersApi.owners).not.toHaveBeenCalled()
  })

  it('does not offer to apply a freight rate that changes nothing', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.reapplyRealizedFreight).mockResolvedValue(
      { dry_run: true, would_update: 0, freight_rate_per_m3: 500, freight_label: 'configured rate' },
    )
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Ledger' }))
    await user.click(await screen.findByRole('button', { name: 'Re-apply current freight rate' }))
    await waitFor(() => expect(productionApi.reapplyRealizedFreight).toHaveBeenCalledWith(true))
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull()
    expect(productionApi.reapplyRealizedFreight).toHaveBeenCalledTimes(1)
  })

  it('renders an unknown warning kind from its message', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedWarnings).mockResolvedValue({
      needs_rebuild: false,
      groups: [
        { kind: 'inventory_drift', count: 0, items: [] },
        { kind: 'reactor_leak', count: 1, items: [{ message: 'Seal the vat' }] },
      ],
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Warnings' }))
    expect(await screen.findByText('reactor_leak (1)')).toBeInTheDocument()
    expect(screen.getByText('Seal the vat')).toBeInTheDocument()
    expect(screen.queryByText('None.')).toBeNull()
  })
})
