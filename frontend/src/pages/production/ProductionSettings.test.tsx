import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { productionApi } from '../../api/client'
import type { ObservedSellFees, ProductionSettings as ProductionSettingsT } from '../../api/types'
import ProductionSettings from './ProductionSettings'

vi.mock('../../api/client', () => ({
  hubsApi: { list: async () => (await import('../../testHubs')).TEST_HUBS, observedFreight: async () => [{ region_id: 10000002, hub: 'Jita', source: 'courier_contracts', suggested: 686, shipments: 14, volume_m3: 9e4, window_days: 90, min_shipments: 3, start: '', end: '', reason: null, message: null }] },
  productionApi: {
    settings: vi.fn(),
    updateSettings: vi.fn(),
    structureOptions: vi.fn(),
    systemSettings: vi.fn(),
    systemCostIndices: vi.fn(),
    realizedObservedFees: vi.fn(),
    resolveStructureName: vi.fn(),
    allSolarSystems: vi.fn(),
    structureNames: vi.fn(),
  },
}))
vi.mock('./DynamicTargetSettingsCard', () => ({ DynamicTargetSettingsCard: () => null }))
vi.mock('./ReactionCapacitySettingsSection', () => ({ ReactionCapacitySettingsSection: () => null }))

const SETTINGS = {
  component_overbuild: 0.1, hub_region_id: 10000002, haul_cost_per_m3: 900, market_fees: 0.036,
  facility_tax_rate: 0.0025, min_margin: 0.1, min_daily_profit: 0, bpc_inventory: 0.5,
  home_market: 'cj', home_location_id: null, stock_hangar_flags: [], excluded_system_ids: [],
  reaction_structure_type: 'Tatara', reaction_rig_tier: 'T2', component_structure_type: 'Azbel',
  component_rig_tier: 'T2', supercapital_structure_type: 'Citadel (no bonuses)',
  supercapital_rig_tier: 'No Rig', manufacturing_structure_type: 'Azbel', manufacturing_rig_tier: 'T2',
  encryption_skill_level: 5, datacore_skill_1_level: 5, datacore_skill_2_level: 5,
  industry_skill_level: 5, advanced_industry_skill_level: 4, reactions_skill_level: 5, specialist_skill_level: 5,
  reaction_cost_index_override: null, component_cost_index_override: null,
  manufacturing_cost_index_override: null, alchemy_reactions_enabled: false,
} as unknown as ProductionSettingsT

const FEES: ObservedSellFees = {
  setting: 'market_fees', window_days: 30, min_sales: 20, sales: 25, quote: 0.0537, suggested: 0.0537,
  sales_tax_rate: 0.036, broker_fee_rate: 0.0177, upper_bound_quote: null, upper_bound_value: null,
  order_fees_without_sale: null, reason: null, message: null,
  relist_note: 'Relist and order-modification fees are included only as far as they are attributed to a sale.',
}

// Mantine's Select labels both its input and its option list; the input is the one to click.
const selectInput = (name: string) =>
  screen.getAllByLabelText(name).find((e) => e.tagName === 'INPUT') as HTMLElement
const findSelectInput = async (name: string) =>
  (await screen.findAllByLabelText(name)).find((e) => e.tagName === 'INPUT') as HTMLElement

function setup(fees: ObservedSellFees) {
  vi.mocked(productionApi.settings).mockResolvedValue(SETTINGS)
  vi.mocked(productionApi.updateSettings).mockResolvedValue(SETTINGS)
  vi.mocked(productionApi.structureOptions).mockResolvedValue(
    { hangar_division_flags: [], structure_types: [], rig_tiers: ['No Rig', 'T1-Rig', 'Thukker'],
      invention_rig_tiers: ['No Rig', 'Invention T1-Rig'] } as any)
  vi.mocked(productionApi.systemSettings).mockResolvedValue({} as any)
  vi.mocked(productionApi.systemCostIndices).mockResolvedValue({} as any)
  vi.mocked(productionApi.allSolarSystems).mockResolvedValue([])
  vi.mocked(productionApi.structureNames).mockResolvedValue({})
  vi.mocked(productionApi.realizedObservedFees).mockResolvedValue(fees)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><ProductionSettings /></MantineProvider>
    </QueryClientProvider>,
  )
}

describe('ProductionSettings observed sell fees', () => {
  it('shows the actual rate beside the field and saves nothing by itself', async () => {
    setup(FEES)
    expect(await screen.findByText(/Actual last 30 days: 5\.37 % \(25 sales\)/)).toBeInTheDocument()
    expect(screen.getByLabelText('Market sell fees')).toHaveValue('3.60%')
    expect(productionApi.updateSettings).not.toHaveBeenCalled()
  })

  it('"Use this value" saves only the changed field through the normal settings save', async () => {
    setup(FEES)
    await userEvent.click(await screen.findByRole('button', { name: 'Use this value' }))
    await waitFor(() => expect(productionApi.updateSettings).toHaveBeenCalledTimes(1))
    expect(vi.mocked(productionApi.updateSettings).mock.calls[0][0]).toEqual({ market_fees: 0.0537 })
  })

  it('shows why there is no value and offers no button', async () => {
    setup({
      ...FEES, sales: 4, quote: null, suggested: null, sales_tax_rate: null, broker_fee_rate: null,
      reason: 'too_few_sales', message: 'Fewer than 20 market sales with a known sales tax in the last 30 days.',
    })
    expect(await screen.findByText(/no value \(4 of 20 sales needed\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use this value' })).not.toBeInTheDocument()
  })
})

describe('ProductionSettings observed freight', () => {
  it('"Use this value" saves only the freight field through the settings save', async () => {
    setup(FEES)
    expect(await screen.findByText(/Actual last 90 days: 686 ISK\/m³ \(14 shipments\)/)).toBeInTheDocument()
    expect(productionApi.updateSettings).not.toHaveBeenCalled()
    await userEvent.click(screen.getAllByRole('button', { name: 'Use this value' })[0])
    await waitFor(() => expect(productionApi.updateSettings).toHaveBeenCalledTimes(1))
    expect(vi.mocked(productionApi.updateSettings).mock.calls[0][0]).toEqual({ haul_cost_per_m3: 686 })
  })
})

describe('ProductionSettings industry skills', () => {
  it('shows the four levels and saves a changed one as its own field', async () => {
    setup(FEES)
    const adv = await screen.findByLabelText('Advanced Industry')
    expect(adv).toHaveValue('4')
    expect(screen.getByLabelText('Industry')).toHaveValue('5')
    await userEvent.clear(adv)
    await userEvent.type(adv, '5')
    await userEvent.click(screen.getByRole('button', { name: 'Save Settings' }))
    await waitFor(() => expect(productionApi.updateSettings).toHaveBeenCalledTimes(1))
    expect(vi.mocked(productionApi.updateSettings).mock.calls[0][0]).toEqual({ advanced_industry_skill_level: 5 })
  })
})

describe('ProductionSettings TE rigs', () => {
  it('saves a TE tier and the invention rig as their own fields', async () => {
    setup(FEES)
    const te = (await findSelectInput('TE rig - Manufacturing'))
    expect(te).toHaveValue('Same as ME')
    await userEvent.click(te)
    await userEvent.click(await screen.findByRole('option', { name: 'No Rig' }))
    await userEvent.click(selectInput('Invention cost rig (invention structure)'))
    await userEvent.click(await screen.findByRole('option', { name: 'Invention T1-Rig' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save Settings' }))
    await waitFor(() => expect(productionApi.updateSettings).toHaveBeenCalledTimes(1))
    expect(vi.mocked(productionApi.updateSettings).mock.calls[0][0]).toEqual({
      manufacturing_te_rig_tier: 'No Rig', invention_rig_tier: 'Invention T1-Rig',
    })
  })

  it('does not offer Thukker for the reaction TE rig', async () => {
    setup(FEES)
    await userEvent.click((await findSelectInput('TE rig - Reactions')))
    expect(screen.queryByRole('option', { name: 'Thukker' })).not.toBeInTheDocument()
  })
})

describe('ProductionSettings rig sheet link', () => {
  it('saves the link as its own field', async () => {
    setup(FEES)
    const field = await screen.findByLabelText('Rig sheet link')
    await userEvent.click(field)
    await userEvent.paste('https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/edit')
    await userEvent.click(screen.getByRole('button', { name: 'Save Settings' }))
    await waitFor(() => expect(productionApi.updateSettings).toHaveBeenCalledTimes(1))
    expect(vi.mocked(productionApi.updateSettings).mock.calls[0][0]).toEqual({
      rig_sheet_url: 'https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/edit',
    })
  })
})
