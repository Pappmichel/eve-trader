import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { gateApi, portfolioApi, productionApi } from '../api/client'
import type { GateStatus, PortfolioOverview, RealizedToolCards, TotalWealth } from '../api/types'
import Portfolio from './Portfolio'

vi.mock('../api/client', () => ({
  gateApi: { status: vi.fn() },
  portfolioApi: {
    overview: vi.fn(),
    wealthChange: vi.fn(),
    history: vi.fn(),
    wealth: vi.fn(),
    wealthBreakdown: vi.fn(),
    capital: vi.fn(),
    manualPrices: vi.fn(),
    attention: vi.fn(),
    realizedCards: vi.fn(),
    realizedHistory: vi.fn(),
  },
  productionApi: {
    itemNameOptions: vi.fn(),
  },
}))

function gate(tools: string[]): GateStatus {
  return {
    enabled: true, logged_in: true, character_name: 'Pilot',
    tools,
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
  wealth_change: {
    d1: { isk: 5, pct: 0.05 },
    d7: null,
    d30: { isk: -10, pct: -0.1 },
  },
}

const wealth: TotalWealth = {
  total_wealth: 100,
  wealth_assets_value: 80,
  wealth_blueprints_value: 10,
  wealth_wallet_balance: 10,
  wealth_listed_value: 0,
  wealth_contract_value: 0,
  wealth_escrow: null,
  wealth_priced_items: 1,
  wealth_unpriced_items: 1,
  characters_missing_wallet_scope: [],
  sync: { assets: null, blueprints: null, listed: null, contracts: null, wallet: null, escrow: null },
  freight_per_m3: 900,
}

const breakdown = {
  total: 100,
  by_owner: [{ label: 'Alice', value: 100, share: 1 }],
  by_location: [{ label: 'C-J', value: 100, share: 1 }],
  by_category: [{ label: 'Material', value: 100, share: 1 }],
  top: [],
  unpriced: [{
    type_id: 34, type_name: 'Tritanium', quantity: 5, positions: 1,
    owner_name: 'Alice', location_name: 'Jita', component: 'assets',
  }],
  unpriced_types: 1,
}

function cards(rows: RealizedToolCards['rows'], total: number | null): RealizedToolCards {
  return { rows, total, period: '30', freight: null }
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

describe('Portfolio overview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(portfolioApi.overview).mockResolvedValue(overview)
    vi.mocked(portfolioApi.wealthChange).mockResolvedValue(overview.wealth_change)
    vi.mocked(portfolioApi.history).mockResolvedValue([])
    vi.mocked(portfolioApi.wealth).mockResolvedValue(wealth)
    vi.mocked(portfolioApi.wealthBreakdown).mockResolvedValue(breakdown)
    vi.mocked(portfolioApi.capital).mockResolvedValue({
      source: 'realized-profit ledger',
      buckets: [{
        key: 'stock', label: 'Stock', quantity: 1, cost_basis: 40, market_value: 50,
        unrealized: 10, coverage: 1, estimated: false,
      }],
      total: { cost_basis: 40, market_value: 50, unrealized: 10, estimated: false, coverage: 1, excluded: [] },
    })
    vi.mocked(portfolioApi.manualPrices).mockResolvedValue([])
    vi.mocked(portfolioApi.attention).mockResolvedValue({ items: [] })
    vi.mocked(portfolioApi.realizedCards).mockResolvedValue(cards([], null))
    vi.mocked(portfolioApi.realizedHistory).mockResolvedValue({
      bucket: 'day', channels: [], period: '30', rows: [],
    })
    vi.mocked(gateApi.status).mockResolvedValue(gate(['portfolio']))
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([])
  })

  it('shows total wealth and its changes, and does not show combined value', async () => {
    renderPortfolio()
    expect(await screen.findByRole('heading', { name: 'Portfolio Overview' })).toBeInTheDocument()
    expect(await screen.findByText('100 ISK')).toBeInTheDocument()
    expect(await screen.findByText(/1 day: 5 ISK \(5%\)/)).toBeInTheDocument()
    expect(screen.getByText(/7 days: –/)).toBeInTheDocument()
    expect(screen.getByText(/30 days: -10 ISK \(-10%\)/)).toBeInTheDocument()
    expect(screen.queryByText('Combined Value')).toBeNull()
    expect(screen.queryByText('Trading + Production combined')).toBeNull()
    expect(screen.queryByText('Trading: Realized Profit')).toBeNull()
    expect(await screen.findByText('Listed')).toBeInTheDocument()
    expect(screen.getByText('Escrow')).toBeInTheDocument()
    expect(screen.getByText(/Jita sell price\s+plus freight \(900 ISK\/m³/)).toBeInTheDocument()
    expect(await screen.findByText('Tritanium · Alice · Jita · 5')).toBeInTheDocument()
    expect(screen.getAllByText('Alice').length).toBeGreaterThan(0)
    expect(screen.getAllByText('100 ISK · 100%').length).toBeGreaterThan(0)
    expect(await screen.findByText('From the realized-profit ledger. Cost is the oldest layer of that type.')).toBeInTheDocument()
    expect(screen.getByText('Stock')).toBeInTheDocument()
    expect(screen.getByText('Unrealized 10 ISK')).toBeInTheDocument()
    // The overview also computes Production's stock value; the page no longer reads it.
    expect(portfolioApi.overview).not.toHaveBeenCalled()
  })

  it('renders one card per tool from realized-cards, including a single-tool total', async () => {
    vi.mocked(portfolioApi.realizedCards).mockResolvedValue(cards([
      {
        tool: 'doctrine', profit: 65, revenue: 100, margin: 0.65, coverage: 1,
        estimated: false, empty: false, sales_count: 1, link: '/doctrine/realized',
      },
    ], 65))
    renderPortfolio()
    expect(await screen.findByText('Doctrine')).toBeInTheDocument()
    expect(screen.getAllByText('65 ISK').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'Open report' })).toHaveAttribute('href', '/doctrine/realized')
    expect(screen.getByText('Realized profit total')).toBeInTheDocument()
    expect(portfolioApi.realizedCards).toHaveBeenCalledWith('30')
  })

  it('shows a dash and no zero when a tool still needs a recompute', async () => {
    vi.mocked(portfolioApi.realizedCards).mockResolvedValue(cards([
      {
        tool: 'production', profit: null, revenue: null, margin: null, coverage: null,
        estimated: false, empty: true, sales_count: null, link: '/production/realized',
      },
    ], null))
    renderPortfolio()
    const production = await screen.findByText('Production')
    const card = production.closest('.et-panel')
    expect(card).not.toBeNull()
    expect(within(card as HTMLElement).queryByText('0 ISK')).toBeNull()
    expect(screen.queryByText('Realized profit total')).toBeNull()
  })

  it('remembers the period and refetches cards with it', async () => {
    const user = userEvent.setup()
    renderPortfolio()
    expect(await screen.findByRole('heading', { name: 'Portfolio Overview' })).toBeInTheDocument()
    const periodControl = screen.getByRole('radiogroup', { name: 'Realized period' })
    await user.click(within(periodControl).getByRole('radio', { name: '7d' }))
    await waitFor(() => expect(portfolioApi.realizedCards).toHaveBeenCalledWith('7'))
    expect(localStorage.getItem('portfolio-realized-period')).toBe('7')
  })

  it('shows the attention list and the freight split from the API', async () => {
    vi.mocked(portfolioApi.attention).mockResolvedValue({
      items: [{
        key: 'wallet_scope',
        text: 'Alice has no wallet scope, so their balance is left out of Total Wealth.',
        link: '/character-management/characters',
      }],
    })
    vi.mocked(portfolioApi.realizedCards).mockResolvedValue({
      ...cards([], null),
      freight: { courier: 12, configured: 3 },
    })
    renderPortfolio()
    const link = await screen.findByRole('link', { name: /Alice has no wallet scope/ })
    expect(link).toHaveAttribute('href', '/character-management/characters')
    expect(await screen.findByText('Freight from couriers 12 ISK · configured rate 3 ISK')).toBeInTheDocument()
    expect(screen.queryByText('Wallet balance not included for some characters')).toBeNull()
  })

  it('names capital buckets left out of the total and the unpriced cap', async () => {
    vi.mocked(portfolioApi.capital).mockResolvedValue({
      source: 'realized-profit ledger',
      buckets: [
        {
          key: 'stock', label: 'Stock', quantity: 1, cost_basis: 40, market_value: 50,
          unrealized: 10, coverage: 1, estimated: false,
        },
        {
          key: 'in_production', label: 'In production', quantity: 2, cost_basis: 100, market_value: null,
          unrealized: null, coverage: null, estimated: false,
        },
      ],
      total: {
        cost_basis: 40, market_value: 50, unrealized: 10, estimated: false, coverage: 1,
        excluded: ['in_production'],
      },
    })
    vi.mocked(portfolioApi.wealthBreakdown).mockResolvedValue({ ...breakdown, unpriced_types: 250 })
    renderPortfolio()
    expect(await screen.findByText(/Without In production/)).toBeInTheDocument()
    expect(await screen.findByText(/1 of 250 types shown/)).toBeInTheDocument()
  })
})
