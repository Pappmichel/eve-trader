import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ShoppingList from './ShoppingList'
import { doctrineApi } from '../../api/client'

vi.mock('../../api/client', () => ({ doctrineApi: { shoppingList: vi.fn(), settings: vi.fn() } }))

const ROW = {
  type_id: 1, type_name: 'Damage Control II', shortfall: 6, build_cost: null, cj_price: null,
  jita_landed_price: 110, recommended_source: 'Jita', total_cost: 660, hub_region_id: 10000043, hub_name: 'Amarr',
  required_total: 6, available: 0, jita_hangar: 0, on_order: 0, to_buy: 6,
}

function renderPage(hubRegionId: number) {
  vi.mocked(doctrineApi.shoppingList).mockResolvedValue({ rows: [ROW] } as never)
  vi.mocked(doctrineApi.settings).mockResolvedValue({ hub_region_id: hubRegionId } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MantineProvider><ShoppingList /></MantineProvider></QueryClientProvider>)
}

describe('Doctrine ShoppingList best hub column', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the winning hub per item when set to All hubs', async () => {
    renderPage(0)
    expect(await screen.findByText('Amarr')).toBeTruthy()
    expect(screen.getByText('Best hub')).toBeTruthy()
    expect(screen.getByText('Best hub (landed)')).toBeTruthy()
  })

  it('shows buyer columns and warnings when buyers are configured', async () => {
    vi.mocked(doctrineApi.shoppingList).mockResolvedValue({
      rows: [{ ...ROW, jita_hangar: 2, on_order: 4, to_buy: 0, total_cost: 0 }],
      warnings: ['Jason Andven: Market Orders are not shared with Doctrine - buy orders not counted.'],
      buyers_configured: true,
    } as never)
    vi.mocked(doctrineApi.settings).mockResolvedValue({ hub_region_id: 10000002 } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MantineProvider><ShoppingList /></MantineProvider></QueryClientProvider>)
    expect(await screen.findByText('covered')).toBeTruthy()
    expect(screen.getByText('Jita 4-4')).toBeTruthy()
    expect(screen.getByText('On order')).toBeTruthy()
    expect(screen.getByText(/Market Orders are not shared/)).toBeTruthy()
  })

  it('has no hub column for a single hub', async () => {
    renderPage(10000002)
    expect(await screen.findByText('Jita (landed)')).toBeTruthy()
    expect(screen.queryByText('Amarr')).toBeNull()
  })
})
