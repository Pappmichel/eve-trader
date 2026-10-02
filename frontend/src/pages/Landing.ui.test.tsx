import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { gateApi } from '../api/client'
import Landing from './Landing'

vi.mock('../api/client', () => ({
  gateApi: { status: vi.fn() },
  authApi: { start: vi.fn() },
}))

function renderLanding() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Landing />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('Landing Character Management card', () => {
  it('hides the Character Management card when no sub-tool grant is present', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({
      enabled: true, logged_in: true, character_name: 'Alice',
      tools: ['trading', 'production', 'portfolio'],
      suspended: false, pending_access_requests: null,
    })
    renderLanding()
    expect(await screen.findByRole('heading', { name: 'Trading' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Character Management' })).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('heading', { name: 'Admin' })).not.toBeInTheDocument()
  })

  it('shows the Character Management card when the characters grant is present', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({
      enabled: true, logged_in: true, character_name: 'Alice',
      tools: ['trading', 'characters'],
      suspended: false, pending_access_requests: null,
    })
    renderLanding()
    expect(await screen.findByRole('heading', { name: 'Character Management' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Admin' })).not.toBeInTheDocument()
    })
  })

  it('has no standalone Characters card any more', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({
      enabled: true, logged_in: true, character_name: 'Alice',
      tools: ['trading', 'characters'],
      suspended: false, pending_access_requests: null,
    })
    renderLanding()
    await screen.findByRole('heading', { name: 'Character Management' })
    expect(screen.queryByRole('heading', { name: 'Characters' })).not.toBeInTheDocument()
  })
})
