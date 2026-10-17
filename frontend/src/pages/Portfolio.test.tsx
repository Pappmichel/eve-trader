import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { gateApi, portfolioApi, productionApi } from '../api/client'
import type { GateStatus, PortfolioOverview, RealizedSummary, TotalWealth } from '../api/types'
import Portfolio from './Portfolio'

vi.mock('../api/client', () => ({
  gateApi: { status: vi.fn() },
  portfolioApi: {
    overview: vi.fn(),
    history: vi.fn(),
    wealth: vi.fn(),
    manualPrices: vi.fn(),
  },
  productionApi: {
    itemNameOptions: vi.fn(),
    realizedSummary: vi.fn(),
  },
}))

function gate(tools: string[] | undefined): GateStatus {
  return {
    enabled: true, logged_in: true, character_name: 'Pilot',
    tools: tools as string[],
    suspended: false, pending_access_requests: null,
  }
}

const overview: PortfolioOverview = {
  trading_realized_profit: 10,
  trading_average_margin: 0.2,
  trading_daily_profit_volatility: null,
  trading_trade_count: 1,
  production_stock_value: 5,
  production_stock_targets_configured: true,
  combined_value: 15,
}

const wealth: TotalWealth = {
  total_wealth: 100,
  wealth_assets_value: 80,
  wealth_blueprints_value: 10,
  wealth_wallet_balance: 10,
  wealth_priced_items: 1,
  wealth_unpriced_items: 0,
  characters_missing_wallet_scope: [],
}

function renderPortfolio() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Portfolio />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('Portfolio realized production profit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(portfolioApi.overview).mockResolvedValue(overview)
    vi.mocked(portfolioApi.history).mockResolvedValue([])
    vi.mocked(portfolioApi.wealth).mockResolvedValue(wealth)
    vi.mocked(portfolioApi.manualPrices).mockResolvedValue([])
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([])
    vi.mocked(productionApi.realizedSummary).mockResolvedValue({
      realized_profit: 1234,
      coverage: 0.5,
      estimated: true,
      status: { empty: false, needs_rebuild: false },
    } as RealizedSummary)
  })

  it('does not fetch production profit unless the production grant is loaded', async () => {
    vi.mocked(gateApi.status).mockResolvedValue(gate(['portfolio']))
    renderPortfolio()
    expect(await screen.findByText('Trading: Realized Profit')).toBeInTheDocument()
    await waitFor(() => expect(gateApi.status).toHaveBeenCalled())
    expect(screen.queryByText('Realized Production profit')).toBeNull()
    expect(productionApi.realizedSummary).not.toHaveBeenCalled()
  })

  it('does not treat a missing tools list as every grant', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...gate([]), tools: undefined as unknown as string[] })
    renderPortfolio()
    expect(await screen.findByText('Trading: Realized Profit')).toBeInTheDocument()
    await waitFor(() => expect(gateApi.status).toHaveBeenCalled())
    expect(productionApi.realizedSummary).not.toHaveBeenCalled()
  })

  it('shows the last 30 days from the summary action when production is granted', async () => {
    vi.mocked(gateApi.status).mockResolvedValue(gate(['portfolio', 'production']))
    renderPortfolio()
    expect(await screen.findByText('Realized Production profit')).toBeInTheDocument()
    expect(await screen.findByText('1,234 ISK')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('estimated')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Realized profit' })).toHaveAttribute('href', '/production/realized')
    expect(productionApi.realizedSummary).toHaveBeenCalledWith('days=30')
  })

  it('shows no figure before the first recompute', async () => {
    vi.mocked(gateApi.status).mockResolvedValue(gate(['portfolio', 'production']))
    vi.mocked(productionApi.realizedSummary).mockResolvedValue({
      realized_profit: 0, coverage: null, estimated: false,
      status: { empty: true, needs_rebuild: true },
    } as RealizedSummary)
    renderPortfolio()
    expect(await screen.findByText(/Not computed yet/)).toBeInTheDocument()
    expect(screen.queryByText('0 ISK')).toBeNull()
  })
})
