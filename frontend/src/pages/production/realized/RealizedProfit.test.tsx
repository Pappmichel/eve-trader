import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charactersApi, gateApi, productionApi } from '../../../api/client'
import type {
  RealizedFlags, RealizedJobRow, RealizedProductRow, RealizedSaleRow, RealizedStatus, RealizedSummary,
} from '../../../api/types'
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
      realizedCourier: vi.fn(),
      countHiddenArchive: vi.fn(),
      deleteHiddenArchive: vi.fn(),
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

function product(partial: Partial<RealizedProductRow> = {}): RealizedProductRow {
  return {
    type_id: 34, type_name: 'Tritanium', units_sold: 2, revenue: 100, cost: 40,
    cost_per_unit: 20, net_sell_per_unit: 50, profit: 60, margin: 0.6, coverage: 1,
    planned_cost_per_unit: null, planned_coverage: null, planned_difference_per_unit: null,
    warnings: [], flags: flags(),
    ...partial,
  }
}

function sale(partial: Partial<RealizedSaleRow> = {}): RealizedSaleRow {
  return {
    sale_ref: 'sale-1', type_id: 34, type_name: 'Tritanium', quantity: 2,
    revenue: 100, cost: 40, profit: 60, coverage: 1, sale_date: '2026-10-02T00:00:00Z',
    gross: 100, sales_tax: null, broker_fee: 0, location_id: 1, net_price: 50,
    warnings: [], cost_structure: [], flags: flags(), source: 'market',
    ...partial,
  }
}

function job(partial: Partial<RealizedJobRow> = {}): RealizedJobRow {
  return {
    job_ref: 'character:1:77', job_id: 77, owner_type: 'character', owner_id: 1,
    owner_name: 'Pilot', facility_name: null, planned_unit_cost: null,
    activity_id: 1, blueprint_type_id: 1, blueprint_name: 'Widget Blueprint',
    product_type_id: 34, product_name: 'Widget', runs: 1, successful_runs: 1,
    start_date: '2026-10-01T00:00:00Z', end_date: '2026-10-02T00:00:00Z', status: 'delivered',
    state: 'sold', job_cost: 10, input_cost: 20, unit_cost: 30, output_quantity: 1,
    output_quantity_left: 0, output_unit: 'item', sold_share: 1, cost_per_bpc_run: null,
    facility_id: 1, contract_input: false, cost_structure: [], flags: flags(),
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
    vi.mocked(productionApi.realizedSaleChain).mockResolvedValue({
      sale: sale(),
      tree: {
        kind: 'product', origin: null, type_id: 34, type_name: 'Tritanium', quantity: 1, unit: null,
        unit_cost: null, unit_price: null, reference_price: null, date: null, end_date: null,
        location_id: null, freight: null, freight_label: null, freight_rate_per_m3: null,
        source_ref: null, job_id: null, job_ref: null, activity_id: null, runs: null,
        successful_runs: null, state: null, job_cost: null, facility_id: null, output_quantity: null,
        cost_total: null, share: null, cost_share: null, sale_ref: 'sale-1', source: null,
        flags: flags(), children: [],
      },
    })
    vi.mocked(productionApi.realizedJobs).mockResolvedValue({
      cutover_t0: null, needs_rebuild: false, rows: [], groups: [], totals: null,
    })
    vi.mocked(productionApi.realizedInventory).mockResolvedValue({ needs_rebuild: false, rows: [] })
    vi.mocked(productionApi.realizedWarnings).mockResolvedValue({ needs_rebuild: false, groups: [] })
    vi.mocked(productionApi.realizedOpening).mockResolvedValue({ cutover_t0: null, needs_rebuild: false, rows: [] })
    vi.mocked(productionApi.realizedEntries).mockResolvedValue({ rows: [] })
    vi.mocked(productionApi.realizedArchiveStatus).mockResolvedValue({ rows: [] })
    vi.mocked(productionApi.realizedCourier).mockResolvedValue({
      home_location_id: null, configured_rate_per_m3: 0, months: [],
    })
    vi.mocked(productionApi.countHiddenArchive).mockResolvedValue({
      owner_type: 'character', owner_id: 0, kind: 'wallet', count: 0,
    })
    vi.mocked(productionApi.deleteHiddenArchive).mockResolvedValue({
      owner_type: 'character', owner_id: 0, kind: 'wallet', deleted: 0,
    })
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

  it('shows warning names and readable text instead of ids', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedWarnings).mockResolvedValue({
      needs_rebuild: false,
      groups: [
        {
          kind: 'archive_gap', count: 1, items: [{
            owner_type: 'corporation', owner_id: 98723738, owner_name: 'RichlTech',
            data_kind: 'wallet_journal', gap_start: '2026-10-01T00:00:00Z', gap_end: '2026-10-02T00:00:00Z',
          }],
        },
        {
          kind: 'trading_overlap', count: 1, items: [{
            message: 'trading_overlap: Production type 34 is also bought on the market',
            text: 'Tritanium is also bought on the market', type_id: 34, type_name: 'Tritanium',
          }],
        },
      ],
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Warnings' }))
    expect(await screen.findByText('Owner: RichlTech')).toBeInTheDocument()
    expect(screen.getByText('Data: wallet journal')).toBeInTheDocument()
    expect(screen.getByText('Tritanium is also bought on the market')).toBeInTheDocument()
    expect(screen.queryByText(/98723738/)).toBeNull()
    expect(screen.queryByText(/type 34/)).toBeNull()
  })

  it('shows the first 50 items of a large warning group until asked for all', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedWarnings).mockResolvedValue({
      needs_rebuild: false,
      groups: [{
        kind: 'coverage_below_threshold', count: 120,
        items: Array.from({ length: 120 }, (_, i) => ({ message: `m ${i}`, text: `Sale ${i}` })),
      }],
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Warnings' }))
    expect(await screen.findByText('Sale 49')).toBeInTheDocument()
    expect(screen.queryByText('Sale 50')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Show all 120' }))
    expect(await screen.findByText('Sale 119')).toBeInTheDocument()
  })

  it('shows job spending apart from the cost of goods sold', async () => {
    vi.mocked(productionApi.realizedSummary).mockResolvedValue(summary({
      spending: {
        job_fees: [
          { activity_id: 1, label: 'Manufacturing', jobs: 616, amount: 6_412_000_000 },
          { activity_id: 8, label: 'Invention', jobs: 379, amount: 156_000_000 },
        ],
        job_fees_total: 6_568_000_000, invention_total: 982_000_000,
        produced_in_stock: 69_400_000_000, running_jobs: 389, in_running_jobs: 3_500_000_000,
      },
    }))
    renderPage()
    expect(await screen.findByText('Job fees paid')).toBeInTheDocument()
    expect(screen.getByText('6,568,000,000 ISK')).toBeInTheDocument()
    expect(screen.getByText('Manufacturing: 6,412,000,000 ISK (616 jobs)')).toBeInTheDocument()
    expect(screen.getByText('389 jobs · balance as of the last recompute')).toBeInTheDocument()
    expect(screen.getByText('Cost of goods sold breakdown')).toBeInTheDocument()
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

  it('shows planned cost and the difference from realized cost', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedProducts).mockResolvedValue({
      period: { start: null, end: null, since_t0: false, days: 30 },
      needs_rebuild: false,
      rows: [
        product({ planned_cost_per_unit: 8, planned_coverage: 1, planned_difference_per_unit: 12 }),
        product({ type_id: 35, type_name: 'Pyerite', planned_cost_per_unit: null, planned_coverage: null }),
      ],
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Products' }))
    expect(await screen.findByRole('button', { name: /Planned cost\/unit/ })).toBeInTheDocument()
    expect(screen.getByText('8 ISK')).toBeInTheDocument()
    expect(screen.getByText('12 ISK')).toBeInTheDocument()
    const pyerite = screen.getByText('Pyerite').closest('tr')
    expect(pyerite?.textContent).toContain('–')
  })

  it('badges a contract sale and does not call it tax missing', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedProducts).mockResolvedValue({
      period: { start: null, end: null, since_t0: false, days: 30 },
      needs_rebuild: false,
      rows: [product()],
    })
    vi.mocked(productionApi.realizedProduct).mockResolvedValue({
      period: { start: null, end: null, since_t0: false, days: 30 },
      needs_rebuild: false,
      product: product(),
      sales: [
        sale({ sale_ref: 'contract:9', source: 'contract', sales_tax: null }),
        sale({ sale_ref: 'market:1', source: 'market', sales_tax: null }),
      ],
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Products' }))
    await user.click(await screen.findByText('Tritanium'))
    expect(await screen.findByText('contract')).toBeInTheDocument()
    expect(screen.getAllByText('tax missing')).toHaveLength(1)
  })

  it('groups jobs per product and opens one product\'s jobs', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedJobs).mockResolvedValue({
      cutover_t0: null, needs_rebuild: false,
      rows: [job({ contract_input: true }), job({ job_ref: 'character:1:78', job_id: 78, product_type_id: 35, product_name: 'Other' })],
      groups: [{
        activity_id: 1, type_id: 34, type_name: 'Widget', output_unit: 'item', jobs: 1, runs: 1,
        successful_runs: 0, running: 0, failed: 0, cancelled: 0, input_cost: 100, job_cost: 20,
        total_cost: 120, output_quantity: 10, unit_cost: 12, sold_share: 0.5, planned_unit_cost: 10,
        planned_difference_per_unit: 2, cost_per_bpc_run: null, estimated: false, assumed: false,
      }],
      totals: { jobs: 2, input_cost: 100, job_cost: 20, total_cost: 120, failed: 0, cancelled: 0, running: 0 },
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Jobs' }))
    // Default: finished jobs of the selected period, summed per product.
    await waitFor(() => expect(productionApi.realizedJobs).toHaveBeenCalledWith('days=30&state=finished'))
    expect(await screen.findByText('12 ISK')).toBeInTheDocument()
    expect(screen.getByText(/2 jobs · Total cost 120 ISK/)).toBeInTheDocument()
    await user.click(screen.getByText('Widget'))
    expect(await screen.findByRole('button', { name: 'Back to products' })).toBeInTheDocument()
    expect(screen.getByText('contract')).toBeInTheDocument()
    expect(screen.queryByText('Other')).toBeNull()
    await user.click(screen.getByText('Running'))
    await waitFor(() => expect(productionApi.realizedJobs).toHaveBeenCalledWith('days=30&state=running'))
  })

  it('shows Forge-to-home courier months under Freight', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedCourier).mockResolvedValue({
      home_location_id: 42,
      configured_rate_per_m3: 80,
      months: [{
        month: '2026-10', count: 1, volume_m3: 10, reward: 500,
        paid_isk_per_m3: 50, configured_rate_per_m3: 80,
      }],
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Ledger' }))
    expect(await screen.findByText('2026-10')).toBeInTheDocument()
    expect(screen.getByText('50 ISK')).toBeInTheDocument()
    expect(screen.getByText(/cost stays the configured rate/)).toBeInTheDocument()
  })

  it('counts hidden archive rows before deleting them', async () => {
    const user = userEvent.setup()
    vi.mocked(productionApi.realizedArchiveStatus).mockResolvedValue({
      rows: [{
        owner_type: 'character', owner_id: 7, owner_name: null,
        shared: { wallet: false, industry_jobs: true, market_orders: true, contracts: true },
        last_sync: {}, gaps: {}, hidden_kinds: ['wallet'],
      }],
    })
    vi.mocked(productionApi.countHiddenArchive).mockResolvedValue({
      owner_type: 'character', owner_id: 7, kind: 'wallet', count: 4,
    })
    vi.mocked(productionApi.deleteHiddenArchive).mockResolvedValue({
      owner_type: 'character', owner_id: 7, kind: 'wallet', deleted: 4,
    })
    renderPage()
    await screen.findByText('Cost of goods sold')
    await user.click(screen.getByRole('tab', { name: 'Ledger' }))
    await user.click(await screen.findByRole('button', { name: 'Delete archived wallet' }))
    expect(await screen.findByText(/Delete 4 archived wallet row/)).toBeInTheDocument()
    expect(productionApi.deleteHiddenArchive).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Delete archived rows' }))
    await waitFor(() => expect(productionApi.deleteHiddenArchive).toHaveBeenCalledWith('character', 7, 'wallet'))
  })
})
