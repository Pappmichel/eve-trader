import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, productionApi } from '../../api/client'
import Invention from './Invention'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  productionApi: {
    sdeCounts: vi.fn(), decryptors: vi.fn(), settings: vi.fn(), plan: vi.fn(),
    t1BpcInventionNeeds: vi.fn(), refreshPlan: vi.fn(), estimateInvention: vi.fn(), itemNameOptions: vi.fn(),
  },
}))

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter><Invention /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('Invention', () => {
  it('shows a hint, not a load error, when no build list was computed yet', async () => {
    vi.mocked(productionApi.sdeCounts).mockResolvedValue({ sde_types: 10 } as never)
    vi.mocked(productionApi.decryptors).mockResolvedValue([] as never)
    vi.mocked(productionApi.settings).mockResolvedValue({} as never)
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([] as never)
    vi.mocked(productionApi.plan).mockResolvedValue({ invention_list: [] } as never)
    vi.mocked(productionApi.t1BpcInventionNeeds).mockRejectedValue(
      new ApiError(400, "No build list computed yet. Run 'Compute Buy/Build List' first."))

    renderPage()

    expect(await screen.findByText(/No build list computed yet/)).toBeInTheDocument()
    expect(screen.queryByText(/Failed to load/)).toBeNull()
  })
})
