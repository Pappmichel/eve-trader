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

  it('has no hub column for a single hub', async () => {
    renderPage(10000002)
    expect(await screen.findByText('Jita (landed)')).toBeTruthy()
    expect(screen.queryByText('Amarr')).toBeNull()
  })
})
