import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { productionApi } from '../../api/client'
import StockTargets from './StockTargets'

vi.mock('../../api/client', () => ({
  productionApi: {
    stockTargets: vi.fn(),
    manualStock: vi.fn(),
    manualStockEntries: vi.fn(),
    manualBuildBuy: vi.fn(),
    selectedDecryptors: vi.fn(),
    decryptors: vi.fn(),
    plan: vi.fn(),
    stockValue: vi.fn(),
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
    // LocationPicker's own dependencies (docs/MANUAL_TRACKING_PLAN.md phase 3) -
    // ManualStockEntriesSection's add form renders one.
    searchLocations: vi.fn(),
    setManualLocationName: vi.fn(),
    resolveStructureName: vi.fn(),
  },
}))

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

describe('Stock Targets delete dialog', () => {
  it('says only the targets are deleted and manual stock stays', async () => {
    vi.mocked(productionApi.stockTargets).mockResolvedValue([
      { type_id: 34, type_name: 'Tritanium', backup_stock: 10, home_market_stock: 5, jita_market_stock: null },
    ])
    vi.mocked(productionApi.manualStock).mockResolvedValue({ '34': 100 })
    vi.mocked(productionApi.manualStockEntries).mockResolvedValue([])
    vi.mocked(productionApi.searchLocations).mockResolvedValue([])
    vi.mocked(productionApi.manualBuildBuy).mockResolvedValue({ '34': 'Build' })
    vi.mocked(productionApi.selectedDecryptors).mockResolvedValue({})
    vi.mocked(productionApi.decryptors).mockResolvedValue([])
    vi.mocked(productionApi.plan).mockResolvedValue({ inventory: [] } as never)
    vi.mocked(productionApi.stockValue).mockResolvedValue({ total_value: 0, priced_items: 0, unpriced_items: 0 })
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([])

    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Remove stock target for Tritanium' }))

    expect(await screen.findByText(
      'Remove the stock target for Tritanium? Backup, home, and Jita targets are deleted. Manual stock and the build/buy override stay.',
    )).toBeInTheDocument()
    expect(screen.queryByText(/manual stock, and overrides are all deleted/i)).not.toBeInTheDocument()
  })
})
