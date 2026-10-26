import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AssetInventory from './AssetInventory'
import { assetManagerApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  assetManagerApi: {
    items: vi.fn(), views: vi.fn(), saveView: vi.fn(), deleteView: vi.fn(), eveList: vi.fn(),
    csvUrl: vi.fn(() => '#'), setLocationName: vi.fn(), removeLocationName: vi.fn(), retryLocation: vi.fn(),
  },
}))
vi.mock('../../notify', () => ({ notify: vi.fn() }))

const STRUCTURE = 1046835728433

function row(overrides: Record<string, unknown>) {
  return {
    key: 'k1', type_id: 2399, type_name: 'Precious Metals', group_id: 1, group_name: 'Basic Commodities',
    category_id: 43, category_name: 'Planetary Commodities', quantity: 39440, volume: 15000, value: 1e6,
    unit_price: 25, unpriced: 0, stacks: 1, owners: ['pappmichl4'], sources: ['esi'], badges: [],
    location_id: STRUCTURE, location_name: null, location_kind: 'unknown', location_status: 'forbidden',
    ...overrides,
  }
}

function renderPage(rows: ReturnType<typeof row>[]) {
  vi.mocked(assetManagerApi.items).mockResolvedValue({
    rows, total_rows: rows.length, page: 1, page_size: 50,
    totals: { stacks: 1, quantity: 1, volume: 1, value: 1, unpriced: 0, types: 1, locations: 1, owners: 1 },
    facets: { owners: [], regions: [], systems: [], locations: [], categories: [], groups: [], sources: [], badges: [] },
    manual_stock_visible: false, shared: true,
  } as never)
  vi.mocked(assetManagerApi.views).mockResolvedValue({ views: [] } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}><MantineProvider><MemoryRouter>
      <AssetInventory />
    </MemoryRouter></MantineProvider></QueryClientProvider>,
  )
}

describe('Asset Manager unnamed locations', () => {
  beforeEach(() => vi.clearAllMocks())

  it('says why a structure has no name', async () => {
    renderPage([row({})])
    expect(await screen.findByText(`Structure ${STRUCTURE} (no access)`)).toBeTruthy()
  })

  it('keeps a plain id when the lookup status is unknown', async () => {
    renderPage([row({ location_status: null, location_id: 5_000_000 })])
    expect(await screen.findByText('Location 5000000')).toBeTruthy()
    expect(screen.queryByLabelText('Location actions')).toBeNull()  // not a structure id
  })

  it('names a location from the row menu', async () => {
    vi.mocked(assetManagerApi.setLocationName).mockResolvedValue({ location_id: STRUCTURE, name: 'POCO Oxygen' })
    renderPage([row({})])
    fireEvent.click(await screen.findByLabelText('Location actions'))
    fireEvent.click(await screen.findByText('Name this location…'))
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'POCO Oxygen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(assetManagerApi.setLocationName).toHaveBeenCalledWith(STRUCTURE, 'POCO Oxygen'))
  })

  it('looks a location up again from the row menu', async () => {
    vi.mocked(assetManagerApi.retryLocation).mockResolvedValue({ location_id: STRUCTURE, name: null, status: 'forbidden' })
    renderPage([row({})])
    fireEvent.click(await screen.findByLabelText('Location actions'))
    fireEvent.click(await screen.findByText('Look up again'))
    await waitFor(() => expect(assetManagerApi.retryLocation).toHaveBeenCalledWith(STRUCTURE))
  })
})
