import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { gateApi } from '../../api/client'
import CharacterManagementHub from './CharacterManagementHub'

vi.mock('../../api/client', () => ({
  gateApi: { status: vi.fn() },
}))

function renderHub() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/character-management']}>
          <Routes>
            <Route path="/character-management" element={<CharacterManagementHub />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

const base = { enabled: true, logged_in: true, character_name: 'Alice', suspended: false, pending_access_requests: null }

describe('CharacterManagementHub', () => {
  it('lists the Characters sub-tool when the grant is held', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['characters'] })
    renderHub()
    expect(await screen.findByRole('heading', { name: 'Characters' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', '/character-management/characters')
  })

  it('says so when no sub-tool grant is held', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['trading'] })
    renderHub()
    expect(await screen.findByText(/no Character Management tools/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Characters' })).not.toBeInTheDocument()
  })
})
