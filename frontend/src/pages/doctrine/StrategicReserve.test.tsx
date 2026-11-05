import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StrategicReserve from './StrategicReserve'
import { doctrineApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  doctrineApi: { strategicReserve: vi.fn(), updateFitting: vi.fn() },
}))

const FITTING = {
  fitting_id: 'f1', fitting_name: 'Rifter Fleet', doctrine_name: 'Frigates', hull_name: 'Rifter',
  reserve_target: 3, complete_sets: 2, missing_units: 2,
}
const BASE = {
  state: 'ok', include_hulls: true, assembled_ships_not_counted: 0, fittings: [FITTING], below_target: [], missing: [],
  hangar: { owner_type: 'corporation', owner_id: 1, location_id: 7, location_name: 'C-J', hangar_flag: 'CorpSAG5' },
}

function renderPage(result: object) {
  vi.mocked(doctrineApi.strategicReserve).mockResolvedValue(result as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><MemoryRouter><StrategicReserve /></MemoryRouter></MantineProvider>
    </QueryClientProvider>,
  )
}

describe('Doctrine StrategicReserve', () => {
  beforeEach(() => vi.clearAllMocks())

  it('asks for a hangar when none is set', async () => {
    renderPage({ ...BASE, state: 'not_configured', hangar: null })
    expect(await screen.findByText(/No reserve hangar set yet/)).toBeTruthy()
  })

  it('says when the owner does not share assets', async () => {
    renderPage({ ...BASE, state: 'not_shared' })
    expect(await screen.findByText('Hangar owner not shared')).toBeTruthy()
  })

  it('says when the chosen container is gone and names a chosen one', async () => {
    renderPage({
      ...BASE, state: 'container_missing',
      hangar: { ...BASE.hangar, container_item_id: 555, container_name: 'Fleet Reserve' },
    })
    expect(await screen.findByText('Container not found')).toBeTruthy()
    expect(screen.getByText('Container Fleet Reserve')).toBeTruthy()
  })

  it('warns about fittings below their target', async () => {
    renderPage({
      ...BASE,
      below_target: [{ fitting_id: 'f1', fitting_name: 'Rifter Fleet', complete_sets: 2, reserve_target: 3 }],
    })
    expect(await screen.findByText('Below target')).toBeTruthy()
    expect(screen.getByText(/2 of 3 fits/)).toBeTruthy()
  })

  it('stays quiet at target and reports assembled ships it did not count', async () => {
    renderPage({ ...BASE, assembled_ships_not_counted: 16 })
    expect(await screen.findByText(/16 assembled ship\(s\)/)).toBeTruthy()
    expect(screen.queryByText('Below target')).toBeNull()
  })
})
