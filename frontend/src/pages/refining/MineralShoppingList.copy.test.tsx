import { fireEvent, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

const MINERAL = { type_id: 34, name: 'Tritanium', required_qty: 1000 }
const PURCHASE = {
  type_id: 1, item: 'Compressed Veldspar', category: 'ore', family: 'Veldspar', is_ice: false, portions: 1, units: 100,
  volume_m3: 15, landed_cost_per_unit: 2, total_cost: 200, hub_region_id: null, hub_name: null, depth_checked: true,
}
const PLAN = {
  ore_purchases: [PURCHASE], reprocess_purchases: [PURCHASE], direct_purchases: [], coverage: [],
  ore_cost: 200, reprocess_cost: 200, direct_cost: 0, total_cost: 200, lp_cost: 200, all_direct_cost: 300,
  savings_vs_all_direct: 100, total_volume_m3: 15, shortfall: false, approximate: false,
  byproducts: [], byproduct_value: 0, net_cost: 0, byproducts_credited: false,
}

vi.mock('../../api/client', () => {
  const api = {
    refinableMinerals: async () => [MINERAL],
    mineralRequirements: async () => [MINERAL],
    shoppableMinerals: async () => [MINERAL],
    shoppingRequirements: async () => [MINERAL],
    settings: async () => ({ hub_region_id: 10000002 }),
    optimizeShoppingList: async () => PLAN,
  }
  return { refiningApi: api, moduleReprocessingApi: api, productionApi: { plan: async () => null } }
})

describe.each([
  ['Ore & Minerals', '../refining/MineralShoppingList'],
  ['Module Reprocessing', '../module_reprocessing/MineralShoppingList'],
])('%s shopping list', (_tool, path) => {
  afterEach(() => vi.unstubAllGlobals())

  it('offers a copy button on the ore names of the finished list', async () => {
    vi.resetModules()
    vi.stubGlobal('isSecureContext', true)
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const { default: Page } = await import(/* @vite-ignore */ path)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MantineProvider><Page /></MantineProvider>
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: /optimize/i }))
    await screen.findByText('Compressed Veldspar')

    for (const button of screen.getAllByRole('button', { name: /copy value/i })) fireEvent.click(button)

    expect(writeText).toHaveBeenCalledWith('Compressed Veldspar')
  })
})
