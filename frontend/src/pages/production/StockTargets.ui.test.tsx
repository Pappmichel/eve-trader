import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { productionApi } from '../../api/client'
import type { DynamicTargetSettings, StockTarget } from '../../api/types'
import StockTargets from './StockTargets'

vi.mock('../../api/client', () => ({
  productionApi: {
    stockTargets: vi.fn(),
    manualStock: vi.fn(),
    manualStockEntries: vi.fn(),
    manualListedStock: vi.fn(),
    manualBuildBuy: vi.fn(),
    selectedDecryptors: vi.fn(),
    decryptors: vi.fn(),
    plan: vi.fn(),
    stockValue: vi.fn(),
    marketStatus: vi.fn(),
    itemNameOptions: vi.fn(),
    addStockTarget: vi.fn(),
    removeStockTarget: vi.fn(),
    updateStockTarget: vi.fn(),
    setManualStock: vi.fn(),
    addManualStockEntry: vi.fn(),
    removeManualStockEntry: vi.fn(),
    setManualBuildBuy: vi.fn(),
    clearManualBuildBuy: vi.fn(),
    setSelectedDecryptor: vi.fn(),
    clearSelectedDecryptor: vi.fn(),
    // Dynamic stock targets (docs/DYNAMIC_STOCK_TARGETS_PLAN.md section 10).
    dynamicTargetSettings: vi.fn(),
    updateDynamicTargetSettings: vi.fn(),
    setStockTargetMode: vi.fn(),
    setAllStockTargetModes: vi.fn(),
    recalculateDynamicTargets: vi.fn(),
    // LocationPicker's own dependencies (docs/MANUAL_TRACKING_PLAN.md phase 3) -
    // ManualStockEntriesSection's add form renders one.
    searchLocations: vi.fn(),
    setManualLocationName: vi.fn(),
    resolveStructureName: vi.fn(),
  },
}))

const SETTINGS_OFF: DynamicTargetSettings = {
  dynamic_targets_enabled: false,
  dynamic_target_window_days: 28,
  dynamic_target_min_history_days: 14,
  dynamic_target_market_share: 0.1,
  dynamic_target_coverage_days: 14,
  dynamic_target_reference_margin: 0.3,
  dynamic_target_factor_min: 0.25,
  dynamic_target_factor_max: 2,
  dynamic_target_smoothing: 0.3,
  dynamic_target_deadband: 0.1,
  dynamic_target_day_cap_multiple: 3,
}

function target(overrides: Partial<StockTarget> = {}): StockTarget {
  return {
    type_id: 34, type_name: 'Tritanium', backup_stock: 10, home_market_stock: 5, jita_market_stock: null,
    target_mode: 'fixed', dynamic_min: null, dynamic_max: null, effective_home_market_stock: 5,
    dynamic_day: null, reason: null, velocity_source: null, velocity_used: null, velocity_own: null,
    velocity_market: null, margin: null, margin_source: null, factor: null, gate_margin: null,
    gate_blocked: null, max_day_share: null,
    ...overrides,
  }
}

const DYNAMIC_ROW = target({
  type_id: 35, type_name: 'Pyerite', home_market_stock: 20, target_mode: 'dynamic', dynamic_min: 0, dynamic_max: 60,
  effective_home_market_stock: 33, dynamic_day: '2026-10-24', reason: 'ok', velocity_source: 'own', velocity_used: 2.1,
  velocity_own: 2.1, velocity_market: 1.4, margin: 0.31, margin_source: 'live', factor: 1.05, max_day_share: 0.2,
})

function mockApi(targets: StockTarget[], settings: DynamicTargetSettings = SETTINGS_OFF) {
  vi.mocked(productionApi.stockTargets).mockResolvedValue(targets)
  vi.mocked(productionApi.manualStock).mockResolvedValue({ '34': 100 })
  vi.mocked(productionApi.manualStockEntries).mockResolvedValue([])
  vi.mocked(productionApi.manualListedStock).mockResolvedValue([])
  vi.mocked(productionApi.searchLocations).mockResolvedValue([])
  vi.mocked(productionApi.manualBuildBuy).mockResolvedValue({ '34': 'Build' })
  vi.mocked(productionApi.selectedDecryptors).mockResolvedValue({})
  vi.mocked(productionApi.decryptors).mockResolvedValue([])
  vi.mocked(productionApi.plan).mockResolvedValue({ inventory: [] } as never)
  vi.mocked(productionApi.stockValue).mockResolvedValue({ total_value: 0, priced_items: 0, unpriced_items: 0 })
  vi.mocked(productionApi.marketStatus).mockResolvedValue([])
  vi.mocked(productionApi.itemNameOptions).mockResolvedValue([])
  vi.mocked(productionApi.dynamicTargetSettings).mockResolvedValue(settings)
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MantineProvider>
      <ModalsProvider>
        <QueryClientProvider client={client}>
          <StockTargets />
        </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>,
  )
}

// Mantine's Select puts the aria-label on more than one element (input and listbox); the first is the input.
async function modeInput(typeName: string): Promise<HTMLElement> {
  const matches = await screen.findAllByLabelText(`Target mode for ${typeName}`)
  return matches.find((el) => el.tagName === 'INPUT') ?? matches[0]
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Stock Targets delete dialog', () => {
  it('says only the targets are deleted and manual stock stays', async () => {
    mockApi([target()])

    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Remove stock target for Tritanium' }))

    expect(await screen.findByText(
      'Remove the stock target for Tritanium? Backup, home, and Jita targets are deleted. Manual stock and the build/buy override stay.',
    )).toBeInTheDocument()
    expect(screen.queryByText(/manual stock, and overrides are all deleted/i)).not.toBeInTheDocument()
  })
})

describe('Stock Targets dynamic targets', () => {
  it('with the switch off keeps the fixed editor, greys out the mode and explains why', async () => {
    mockApi([target(), DYNAMIC_ROW])
    renderPage()

    expect(await screen.findByText(/Dynamic targets are off - every row uses its fixed values/)).toBeInTheDocument()
    expect(await screen.findByText(/1 row\(s\) are set to dynamic and take effect once this is on/)).toBeInTheDocument()
    // the home market target stays an editable number for the dynamic row too while everything acts as fixed
    expect(await screen.findByLabelText('Home market target for Pyerite')).toBeInTheDocument()
    expect(screen.queryByLabelText('Effective home market target for Pyerite')).not.toBeInTheDocument()
    expect(await modeInput('Pyerite')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Recalculate' })).toBeDisabled()
    expect(screen.queryByLabelText('Minimum for Pyerite')).not.toBeInTheDocument()
  })

  it('with the switch on shows the computed target read-only with its reason and the min/max editors', async () => {
    mockApi([target(), DYNAMIC_ROW], { ...SETTINGS_OFF, dynamic_targets_enabled: true })
    renderPage()

    const effective = await screen.findByLabelText('Effective home market target for Pyerite')
    expect(effective).toHaveTextContent('33')
    expect(screen.queryByLabelText('Home market target for Pyerite')).not.toBeInTheDocument()
    // fixed rows keep their editable cell
    expect(screen.getByLabelText('Home market target for Tritanium')).toBeInTheDocument()
    expect(screen.getByLabelText('Minimum for Pyerite')).toBeInTheDocument()
    expect(screen.getByLabelText('Maximum for Pyerite')).toBeInTheDocument()
    expect(await modeInput('Pyerite')).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Recalculate' })).toBeEnabled()
  })

  it('shows the build-gate and overstock hints on a dynamic row', async () => {
    mockApi([DYNAMIC_ROW], { ...SETTINGS_OFF, dynamic_targets_enabled: true })
    vi.mocked(productionApi.plan).mockResolvedValue({
      inventory: [{ type_id: 35, current_stock: 80, reserved_for_orders: 0 }],
    } as never)
    vi.mocked(productionApi.marketStatus).mockResolvedValue([
      { type_id: 35, type_name: 'Pyerite', backup_target: 10, backup_current: 0, home_target: 33, home_listed: 10,
        jita_target: null, jita_listed: 0 },
    ])
    vi.mocked(productionApi.stockTargets).mockResolvedValue([{ ...DYNAMIC_ROW, gate_blocked: true, gate_margin: 0.05 }])
    renderPage()

    const cell = (await screen.findByLabelText('Effective home market target for Pyerite')).parentElement as HTMLElement
    // 80 owned + 10 listed - 10 backup - 33 target = 47 above what the row should hold
    expect(await within(cell).findByText('+47 over')).toBeInTheDocument()
    expect(within(cell).getByText('gate')).toBeInTheDocument()
  })

  it('switching a row to dynamic calls the mode endpoint', async () => {
    mockApi([target()], { ...SETTINGS_OFF, dynamic_targets_enabled: true })
    vi.mocked(productionApi.setStockTargetMode).mockResolvedValue(
      { type_id: 34, target_mode: 'dynamic', dynamic_min: 0, dynamic_max: 15 })
    const user = userEvent.setup()
    renderPage()

    await user.click(await modeInput('Tritanium'))
    await user.click(await screen.findByRole('option', { name: 'Dynamic' }))

    expect(productionApi.setStockTargetMode).toHaveBeenCalledWith(34, { mode: 'dynamic', dynamic_min: undefined, dynamic_max: undefined })
  })

  it('"All to dynamic" asks first, states the counts and then calls the bulk endpoint', async () => {
    mockApi([target(), target({ type_id: 36, type_name: 'Backup only', home_market_stock: null })])
    vi.mocked(productionApi.setAllStockTargetModes).mockResolvedValue({ mode: 'dynamic', updated: 1, skipped: [] })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'All to dynamic' }))
    expect(await screen.findByText(/1 row\(s\) with a home market target become dynamic/)).toBeInTheDocument()
    expect(screen.getByText(/1 row\(s\) without a home market target are skipped/)).toBeInTheDocument()
    expect(productionApi.setAllStockTargetModes).not.toHaveBeenCalled()

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'All to dynamic' }))
    expect(productionApi.setAllStockTargetModes).toHaveBeenCalledWith('dynamic')
  })

  it('the switch saves the whole settings object with the flag flipped', async () => {
    mockApi([target()])
    vi.mocked(productionApi.updateDynamicTargetSettings).mockResolvedValue({ ...SETTINGS_OFF, dynamic_targets_enabled: true })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('switch', { name: 'Dynamic targets on or off' }))

    expect(productionApi.updateDynamicTargetSettings).toHaveBeenCalledWith({ ...SETTINGS_OFF, dynamic_targets_enabled: true })
  })

  it('Recalculate calls the endpoint', async () => {
    mockApi([DYNAMIC_ROW], { ...SETTINGS_OFF, dynamic_targets_enabled: true })
    vi.mocked(productionApi.recalculateDynamicTargets).mockResolvedValue({ written: 1 })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Recalculate' }))
    expect(productionApi.recalculateDynamicTargets).toHaveBeenCalled()
  })
})
