import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { gateApi, portfolioApi, productionApi, tradingApi } from '../api/client'
import Landing from './Landing'

vi.mock('../api/client', () => ({
  gateApi: { status: vi.fn() },
  authApi: { start: vi.fn() },
  tradingApi: { kpis: vi.fn() },
  productionApi: { kpis: vi.fn() },
  portfolioApi: { history: vi.fn() },
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(tradingApi.kpis).mockRejectedValue(new Error('unset'))
  vi.mocked(productionApi.kpis).mockRejectedValue(new Error('unset'))
  vi.mocked(portfolioApi.history).mockRejectedValue(new Error('unset'))
})

function renderLanding() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Landing />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('Landing Character Management card', () => {
  it('hides the Character Management card when no sub-tool grant is present', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({
      enabled: true, logged_in: true, character_name: 'Alice',
      tools: ['trading', 'production', 'portfolio'],
      suspended: false, pending_access_requests: null,
    })
    renderLanding()
    expect(await screen.findByRole('heading', { name: 'Trading' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Character Management' })).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('heading', { name: 'Admin' })).not.toBeInTheDocument()
  })

  it('shows the Character Management card when the characters grant is present', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({
      enabled: true, logged_in: true, character_name: 'Alice',
      tools: ['trading', 'characters'],
      suspended: false, pending_access_requests: null,
    })
    renderLanding()
    expect(await screen.findByRole('heading', { name: 'Character Management' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Admin' })).not.toBeInTheDocument()
    })
  })

  it('has no standalone Characters card any more', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({
      enabled: true, logged_in: true, character_name: 'Alice',
      tools: ['trading', 'characters'],
      suspended: false, pending_access_requests: null,
    })
    renderLanding()
    await screen.findByRole('heading', { name: 'Character Management' })
    expect(screen.queryByRole('heading', { name: 'Characters' })).not.toBeInTheDocument()
  })
})

describe('Landing KPI tiles', () => {
  const status = (tools: string[] | undefined) => ({
    enabled: tools !== undefined, logged_in: true, character_name: 'Alice',
    tools: tools as string[], suspended: false, pending_access_requests: null,
  })
  const kpis = { shortlist_count: 4, import_candidates: 2, own_sell_orders: 7, new_recommendations: 0 }

  it('shows KPI lines only for granted tools', async () => {
    vi.mocked(gateApi.status).mockResolvedValue(status(['trading']))
    vi.mocked(tradingApi.kpis).mockResolvedValue(kpis)
    renderLanding()
    expect(await screen.findByText('own orders')).toBeInTheDocument()
    expect(productionApi.kpis).not.toHaveBeenCalled()
    expect(portfolioApi.history).not.toHaveBeenCalled()
  })

  it('shows every tile when the gate is off (tools undefined)', async () => {
    vi.mocked(gateApi.status).mockResolvedValue(status(undefined))
    vi.mocked(tradingApi.kpis).mockResolvedValue(kpis)
    vi.mocked(productionApi.kpis).mockResolvedValue({ stock_targets: 3, active_jobs: 5, open_special_orders: 1 })
    vi.mocked(portfolioApi.history).mockResolvedValue([
      { snapshot_date: '2026-10-01', combined_value: 100, total_wealth: null },
      { snapshot_date: '2026-10-02', combined_value: 2500, total_wealth: 9000 },
    ] as never)
    renderLanding()
    expect(await screen.findByText('stock targets')).toBeInTheDocument()
    expect(await screen.findByText('2,500 ISK')).toBeInTheDocument()
    expect(screen.getByText('9,000 ISK')).toBeInTheDocument()
    expect(screen.getByText('to import')).toBeInTheDocument()
  })

  it('shows a skeleton while loading', async () => {
    vi.mocked(gateApi.status).mockResolvedValue(status(['trading']))
    vi.mocked(tradingApi.kpis).mockReturnValue(new Promise(() => {}))
    renderLanding()
    expect(await screen.findByTestId('kpi-skeleton')).toBeInTheDocument()
    expect(screen.queryByTestId('kpi-line')).not.toBeInTheDocument()
  })

  it('renders no KPI line when the request fails', async () => {
    vi.mocked(gateApi.status).mockResolvedValue(status(['trading']))
    vi.mocked(tradingApi.kpis).mockRejectedValue(new Error('boom'))
    renderLanding()
    await screen.findByRole('heading', { name: 'Trading' })
    await waitFor(() => expect(screen.queryByTestId('kpi-skeleton')).not.toBeInTheDocument())
    expect(screen.queryByTestId('kpi-line')).not.toBeInTheDocument()
  })
})
