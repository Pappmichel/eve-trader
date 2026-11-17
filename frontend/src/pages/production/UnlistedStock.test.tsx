import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { productionApi } from '../../api/client'
import type { PipelineRunStatus } from '../../api/types'
import UnlistedStock from './UnlistedStock'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    productionApi: { checkUnlistedStock: vi.fn(), checkUnlistedStockStatus: vi.fn() },
  }
})

const api = vi.mocked(productionApi)

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider><UnlistedStock /></MantineProvider>
    </QueryClientProvider>,
  )
}

const SUCCEEDED: PipelineRunStatus = {
  run_id: 'r1', job_name: 'unlisted_stock', tool: 'production', status: 'succeeded',
  result: { rows: [{
    type_id: 205, type_name: 'Nova Cruise Missile', stock_quantity: 50, sell_volume: 12,
    margin: 0.25, doctrine_shortfall: null, doctrine_names: [], production_needed: 7,
  }] },
}

describe('Production UnlistedStock page', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the last run after a reload without starting a new check', async () => {
    api.checkUnlistedStockStatus.mockResolvedValue(SUCCEEDED)
    renderPage()
    expect(await screen.findByText('Nova Cruise Missile')).toBeInTheDocument()
    expect(screen.getByText(/1 listing-target stock items/)).toBeInTheDocument()
    expect(api.checkUnlistedStock).not.toHaveBeenCalled()
  })

  it('starts the job and shows progress while it runs', async () => {
    const running: PipelineRunStatus = {
      run_id: 'r2', job_name: 'unlisted_stock', tool: 'production', status: 'running',
    }
    api.checkUnlistedStockStatus.mockResolvedValueOnce({ run_id: null, status: 'idle' })
    api.checkUnlistedStockStatus.mockResolvedValue(running)
    api.checkUnlistedStock.mockResolvedValue(running)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Check Structure Stock Without Order/ }))
    await waitFor(() => expect(api.checkUnlistedStock).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/Check Structure Stock Without Order: Running/)).toBeInTheDocument()
  })

  it('shows the error text of a failed run', async () => {
    api.checkUnlistedStockStatus.mockResolvedValue({
      run_id: 'r3', job_name: 'unlisted_stock', tool: 'production', status: 'failed',
      error: 'No Production character shared yet.',
    })
    renderPage()
    expect(await screen.findByText('No Production character shared yet.')).toBeInTheDocument()
  })

  it('says everything is listed for an empty result', async () => {
    api.checkUnlistedStockStatus.mockResolvedValue({ ...SUCCEEDED, result: { rows: [] } })
    renderPage()
    expect(await screen.findByText(/All listing-target stock in the C-J hangar is listed/)).toBeInTheDocument()
  })
})
