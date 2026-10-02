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

// User feedback, 2026-10-01: "Possible to add other market hubs?" - the buy
// hub (jita_region_id) was a bare region-id number field, labelled "Jita"
// everywhere regardless of what was actually entered.
describe('TradingSettings buy hub selection', () => {
  it('shows the configured hub (Jita) pre-selected, by name', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(tradingApi.walletDivisionOptions).mockResolvedValue({ wallet_division_ids: [1, 2] })
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    renderPage()

    const hubInput = await screen.findByDisplayValue('Jita (The Forge)')
    expect(hubInput).toBeInTheDocument()
  })

  it('switches the hub and renames every other "Jita"-labelled field to match', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(tradingApi.walletDivisionOptions).mockResolvedValue({ wallet_division_ids: [1, 2] })
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    const user = userEvent.setup()
    renderPage()

    await screen.findByDisplayValue('Jita (The Forge)')
    expect(screen.getByLabelText(/Buyer name \(Jita\)/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Freight cost Jita→structure/)).toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: 'Buy hub' }))
    await user.click(await screen.findByRole('option', { name: 'Amarr (Domain)' }))

    expect(screen.getByLabelText(/Buyer name \(Amarr\)/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Freight cost Amarr→structure/)).toBeInTheDocument()
  })

  it('keeps a region id outside the four known hubs as a selectable "Custom" option, not data loss', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue({ ...SETTINGS, jita_region_id: 10000016 } as any)
    vi.mocked(tradingApi.walletDivisionOptions).mockResolvedValue({ wallet_division_ids: [] })
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    renderPage()

    const hubInput = await screen.findByDisplayValue('Custom (region 10000016)')
    expect(hubInput).toBeInTheDocument()
  })

  it('saves the real region id for the picked hub, not a label string', async () => {
    vi.mocked(tradingApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(tradingApi.walletDivisionOptions).mockResolvedValue({ wallet_division_ids: [] })
    vi.mocked(productionApi.structureNames).mockResolvedValue({})
    vi.mocked(tradingApi.updateSettings).mockResolvedValue(SETTINGS as any)
    const user = userEvent.setup()
    renderPage()

    await screen.findByDisplayValue('Jita (The Forge)')
    await user.click(screen.getByRole('combobox', { name: 'Buy hub' }))
    await user.click(await screen.findByRole('option', { name: 'Dodixie (Sinq Laison)' }))
    await user.click(screen.getByRole('button', { name: 'Save Settings' }))

    await waitFor(() => expect(tradingApi.updateSettings).toHaveBeenCalled())
    const sent = vi.mocked(tradingApi.updateSettings).mock.calls[0][0]
    expect(sent.jita_region_id).toBe(10000032)
  })
})
