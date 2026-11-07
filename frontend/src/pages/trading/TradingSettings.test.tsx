import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import TradingSettings from './TradingSettings'
import { tradingApi, productionApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  tradingApi: {
    settings: vi.fn(),
    updateSettings: vi.fn(),
    realizedObservedFees: vi.fn(),
  },
  productionApi: {
    structureNames: vi.fn(),
  },
  sdeApi: {
    regions: vi.fn().mockResolvedValue([]),
  },
}))

// Minimal but complete TradingSettings shape - every field the page reads.
const SETTINGS = {
  import_cost_per_m3: 1500, structure_sell_haircut: 0.02, min_profit_threshold: 100,
  min_margin_threshold: 0.1, skip_grace_period_days: 14, enforce_shortlist_cap: false,
  max_active_shortlist_items: 500, max_shortlist_growth_per_run: 50, min_hit_rate: 0.5,
  min_avg_movement: 10, min_paired_days: 5, excluded_path_prefixes: [], safe_mode_max_ids: 500,
  lookback_days: 30, jita_region_id: 10000002, reference_region_id: 10000009,
  structure_id: null, structure_market_slug: null, buyer_character_name: null,
  seller_character_name: null, esi_frequent_interval_hours: 1,
  esi_normal_interval_hours: 6, esi_rare_interval_hours: 24, esi_stale_clear_multiples: 3,
  scheduler_enabled: false,
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider><TradingSettings /></MantineProvider>
    </QueryClientProvider>,
  )
}

// The buy hub itself is picked on the Shortlist page (GitHub issue #222);
// Settings only labels hub-dependent fields with it.
describe('TradingSettings buy hub labels', () => {
  it('labels hub-dependent fields with the configured hub', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue({ ...SETTINGS, jita_region_id: 10000043 } as any)
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    renderPage()

    expect(await screen.findByLabelText(/Buyer name \(Amarr\)/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Freight cost Amarr→structure/)).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Buy hub' })).not.toBeInTheDocument()
  })
})

describe('TradingSettings observed sell fees', () => {
  const FEES = {
    setting: 'structure_sell_haircut', window_days: 30, min_sales: 20, sales: 25, quote: 0.0537,
    suggested: 0.9463, sales_tax_rate: 0.036, broker_fee_rate: 0.0177, reason: null, message: null,
    relist_note: 'Relist and order-modification fees are included only as far as they are attributed to a sale.',
  }

  it('shows the actual rate beside the haircut and saves nothing by itself', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    vi.mocked(tradingApi.realizedObservedFees).mockResolvedValue(FEES as any)
    renderPage()

    expect(await screen.findByText(/Actual last 30 days: 94\.63 % \(25 sales\)/)).toBeInTheDocument()
    expect(tradingApi.updateSettings).not.toHaveBeenCalled()
  })

  it('"Use this value" saves the whole form with the new haircut through the settings save', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(tradingApi.updateSettings).mockResolvedValue(SETTINGS as any)
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    vi.mocked(tradingApi.realizedObservedFees).mockResolvedValue(FEES as any)
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: 'Use this value' }))
    await waitFor(() => expect(tradingApi.updateSettings).toHaveBeenCalledTimes(1))
    expect(vi.mocked(tradingApi.updateSettings).mock.calls[0][0]).toMatchObject({
      structure_sell_haircut: 0.9463, import_cost_per_m3: 1500, min_profit_threshold: 100,
    })
  })

  it('shows the reason and no button when the figure has no value', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    vi.mocked(tradingApi.realizedObservedFees).mockResolvedValue({
      ...FEES, sales: 0, quote: null, suggested: null, reason: 'needs_rebuild',
      message: 'The shared wallets changed since the last rebuild. Rebuild the realized-profit ledger first.',
    } as any)
    renderPage()

    expect(await screen.findByText(/Rebuild the realized-profit ledger first/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use this value' })).not.toBeInTheDocument()
  })
})
