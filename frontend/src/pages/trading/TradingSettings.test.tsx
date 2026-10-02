import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import TradingSettings from './TradingSettings'
import { tradingApi, productionApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  tradingApi: {
    settings: vi.fn(),
    updateSettings: vi.fn(),
    walletDivisionOptions: vi.fn(),
  },
  productionApi: {
    structureNames: vi.fn(),
  },
}))

// Minimal but complete TradingSettings shape - every field the page reads.
const SETTINGS = {
  import_cost_per_m3: 1500, structure_sell_haircut: 0.02, min_profit_threshold: 100,
  min_margin_threshold: 0.1, skip_grace_period_days: 14, enforce_shortlist_cap: false,
  max_active_shortlist_items: 500, max_shortlist_growth_per_run: 50, min_hit_rate: 0.5,
  min_avg_movement: 10, excluded_path_prefixes: [], safe_mode_max_ids: 500,
  lookback_days: 30, jita_region_id: 10000002, reference_region_id: 10000009,
  structure_id: null, structure_market_slug: null, buyer_character_name: null,
  seller_character_name: null, wallet_division_ids: [], esi_frequent_interval_hours: 1,
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
    vi.mocked(tradingApi.walletDivisionOptions).mockResolvedValue({ wallet_division_ids: [1, 2] })
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    renderPage()

    expect(await screen.findByLabelText(/Buyer name \(Amarr\)/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Freight cost Amarr→structure/)).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Buy hub' })).not.toBeInTheDocument()
  })
})
