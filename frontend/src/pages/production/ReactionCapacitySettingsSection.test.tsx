import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { productionApi } from '../../api/client'
import type { ProductionSettings } from '../../api/types'
import { ReactionCapacitySettingsSection } from './ReactionCapacitySettingsSection'

vi.mock('../../api/client', () => ({
  productionApi: {
    reactionCapacityStatus: vi.fn(),
  },
}))

const BASE: ProductionSettings = {
  component_overbuild: 0.1,
  hub_region_id: 10000002,
  haul_cost_per_m3: 900,
  market_fees: 0.036,
  facility_tax_rate: 0.0025,
  min_margin: 0.1,
  min_daily_profit: 0,
  bpc_inventory: 0.5,
  home_market: 'cj',
  home_location_id: null,
  distribution_source_location_id: null,
  invention_location_id: null,
  stock_hangar_flags: [],
  reaction_structure_type: 'Tatara',
  reaction_rig_tier: 'T2',
  component_structure_type: 'Azbel',
  component_rig_tier: 'T2',
  supercapital_structure_type: 'Citadel (no bonuses)',
  supercapital_rig_tier: 'No Rig',
  manufacturing_structure_type: 'Azbel',
  manufacturing_rig_tier: 'T2',
  encryption_skill_level: 5,
  datacore_skill_1_level: 5,
  datacore_skill_2_level: 5,
  reaction_cost_index_override: null,
  component_cost_index_override: null,
  manufacturing_cost_index_override: null,
  asset_plan_slot_days_target: null,
  alchemy_reactions_enabled: false,
  reaction_capacity_enabled: false,
  reaction_cycle_days: 13,
  reaction_slot_override: null,
  reaction_window_next: null,
  reaction_window_interval_days: 14,
  reaction_formula_payback_cycles: 1,
  reaction_next_window_demand_factor: 1,
}

function renderSection(form: ProductionSettings = BASE, set = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <ReactionCapacitySettingsSection form={form} set={set} />
      </MantineProvider>
    </QueryClientProvider>,
  )
}

describe('ReactionCapacitySettingsSection', () => {
  it('shows the capacity switch and derived status', async () => {
    vi.mocked(productionApi.reactionCapacityStatus).mockResolvedValue({
      derived_slots: 20,
      slot_override: null,
      slots_total: 20,
      slots_busy: 3,
      slots_free: 17,
      slots_busy_w2: 0,
      slots_free_w2: 20,
      window_anchor: null,
      window_next: null,
      cycle_days: 13,
      capacity_slot_days: 221,
      enabled: false,
    })
    renderSection()
    expect(screen.getByText('Reaction capacity')).toBeInTheDocument()
    expect(screen.getByText('Max job duration (days)')).toBeInTheDocument()
    expect(screen.getByTestId('reaction-capacity-enabled')).toBeInTheDocument()
    expect(await screen.findByTestId('reaction-capacity-status')).toHaveTextContent(/Derived slots: 20/)
    expect(screen.getByTestId('reaction-capacity-status')).toHaveTextContent(/Busy at window: 3/)
    expect(screen.getByTestId('reaction-capacity-status')).toHaveTextContent(/Effective next/)
  })

  it('toggles the enabled switch through set()', async () => {
    vi.mocked(productionApi.reactionCapacityStatus).mockResolvedValue({
      derived_slots: 10, slot_override: null, slots_total: 10, slots_busy: 0,
      slots_free: 10, slots_busy_w2: 0, slots_free_w2: 10,
      window_anchor: null, window_next: null,
      cycle_days: 13, capacity_slot_days: 130, enabled: false,
    })
    const set = vi.fn()
    const user = userEvent.setup()
    renderSection(BASE, set)
    await screen.findByTestId('reaction-capacity-status')
    await user.click(screen.getByLabelText(/Reaction capacity planning/))
    await waitFor(() => {
      expect(set).toHaveBeenCalledWith('reaction_capacity_enabled', true)
    })
  })
})
