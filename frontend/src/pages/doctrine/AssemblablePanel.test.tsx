import { fireEvent, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AssemblablePanel } from './AssemblablePanel'
import { doctrineApi, gateApi, productionApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  doctrineApi: { assemblable: vi.fn() },
  gateApi: { status: vi.fn() },
  productionApi: { maxBuildable: vi.fn() },
}))

const HULL = {
  type_id: 1, type_name: 'Rifter', slot_section: 'hull', item_class: 'exact', per_set: 1, on_hand: 3,
  sets: 3, short_for_next: 0, buildable: true, also_needed_by: [],
}
const MODULE = {
  type_id: 2, type_name: 'Damage Control II', slot_section: 'low/med/high/rig/subsystem/service',
  item_class: 'exact', per_set: 1, on_hand: 2, sets: 2, short_for_next: 1, buildable: true,
  also_needed_by: ['Slasher'],
}
const RESULT = {
  fitting_id: 'f1', fitting_name: 'Rifter Fleet', hull_type_id: 1, hull_name: 'Rifter', assets_available: true,
  stockpile_locations: [{ location_id: 60003760, name: 'Jita IV - Moon 4' }], max_sets: 2, next_sets: 3,
  bottlenecks: [MODULE], requirements: [HULL, MODULE],
}

function renderPanel(tools: string[] | undefined, result: object = RESULT) {
  vi.mocked(doctrineApi.assemblable).mockResolvedValue(result as never)
  vi.mocked(gateApi.status).mockResolvedValue({ tools } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MantineProvider><AssemblablePanel fittingId="f1" /></MantineProvider></QueryClientProvider>)
}

describe('Doctrine AssemblablePanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the count, the stockpile and the bottleneck', async () => {
    renderPanel(['doctrine', 'production'])
    expect(await screen.findByText('What stops fit 3')).toBeTruthy()
    expect(screen.getByText('Stockpile: Jita IV - Moon 4')).toBeTruthy()
    expect(screen.getByText(/also needed by Slasher/)).toBeTruthy()
  })

  it('asks Production on demand when the grant is held', async () => {
    vi.mocked(productionApi.maxBuildable).mockResolvedValue({ max_quantity: 5, bottlenecks: [] } as never)
    renderPanel(['doctrine', 'production'])
    fireEvent.click(await screen.findByText('Check Production'))
    expect(await screen.findByText(/Production can build 5 now/)).toBeTruthy()
    expect(productionApi.maxBuildable).toHaveBeenCalledWith('2', 'full', 1)
  })

  it('hides the Production check without the grant', async () => {
    renderPanel(['doctrine'])
    expect(await screen.findByText('What stops fit 3')).toBeTruthy()
    expect(screen.queryByText('Check Production')).toBeNull()
  })

  it('shows a hint without shared assets', async () => {
    renderPanel(['doctrine'], { ...RESULT, assets_available: false, max_sets: null, bottlenecks: [], requirements: [] })
    expect(await screen.findByText(/No asset data yet/)).toBeTruthy()
  })
})
