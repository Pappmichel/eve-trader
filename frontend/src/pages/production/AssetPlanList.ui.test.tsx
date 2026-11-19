import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { productionApi } from '../../api/client'
import AssetPlanList from './AssetPlanList'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  productionApi: { assetPlan: vi.fn(), settings: vi.fn(), refreshAssetPlan: vi.fn() },
}))

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter><AssetPlanList /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('AssetPlanList', () => {
  it('offers Recompute when the cached plan has no jobs', async () => {
    vi.mocked(productionApi.assetPlan).mockResolvedValue({ jobs: [] } as never)
    vi.mocked(productionApi.settings).mockResolvedValue({} as never)
    vi.mocked(productionApi.refreshAssetPlan).mockResolvedValue({ jobs: 3 })

    renderPage()

    expect(await screen.findByText(/Nothing to build/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Recompute/ }))
    await waitFor(() => expect(productionApi.refreshAssetPlan).toHaveBeenCalledTimes(1))
  })
})
