import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charSkillsApi, gateApi } from '../../api/client'
import CharacterManagementHub from './CharacterManagementHub'

vi.mock('../../api/client', () => ({
  gateApi: { status: vi.fn() },
  charSkillsApi: { warnings: vi.fn() },
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
  beforeEach(() => vi.resetAllMocks())

  it('lists the Characters sub-tool when the grant is held', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['characters'] })
    renderHub()
    expect(await screen.findByRole('heading', { name: 'Characters' })).toBeInTheDocument()
    // Until the gate status loads every card shows (Landing's convention).
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Character Info' })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', '/character-management/characters')
  })

  it('lists Character Info when char_info is granted, and works without characters', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_info'] })
    renderHub()
    expect(await screen.findByRole('heading', { name: 'Character Info' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Characters' })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', '/character-management/info')
  })

  it('lists Skills when char_skills is granted', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_skills'] })
    renderHub()
    expect(await screen.findByRole('heading', { name: 'Skills' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Character Info' })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', '/character-management/skills')
  })

  it('shows how many skill queues need attention as a badge on the Skills card', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_skills'] })
    vi.mocked(charSkillsApi.warnings).mockResolvedValue({ count: 2, characters: [], queue_warning_hours: 24 })
    renderHub()
    expect(await screen.findByText('2 queue warnings')).toBeInTheDocument()
  })

  it('does not ask for warnings without the Skills grant, and shows no badge for zero', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_mail'] })
    renderHub()
    await screen.findByRole('heading', { name: 'Mail' })
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Skills' })).not.toBeInTheDocument())
    expect(charSkillsApi.warnings).not.toHaveBeenCalled()
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_skills'] })
    vi.mocked(charSkillsApi.warnings).mockResolvedValue({ count: 0, characters: [], queue_warning_hours: 24 })
    renderHub()
    await screen.findAllByRole('heading', { name: 'Skills' })
    expect(screen.queryByText(/queue warning/)).not.toBeInTheDocument()
  })

  it('lists Mail when char_mail is granted', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_mail'] })
    renderHub()
    expect(await screen.findByRole('heading', { name: 'Mail' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Skills' })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', '/character-management/mail')
  })

  it('lists Notifications when char_notifications is granted', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_notifications'] })
    renderHub()
    expect(await screen.findByRole('heading', { name: 'Notifications' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Skills' })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', '/character-management/notifications')
  })

  it('lists Contacts & Calendar when char_contacts is granted', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['char_contacts'] })
    renderHub()
    expect(await screen.findByRole('heading', { name: 'Contacts & Calendar' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Skills' })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', '/character-management/contacts')
  })

  it('says so when no sub-tool grant is held', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ ...base, tools: ['trading'] })
    renderHub()
    expect(await screen.findByText(/no Character Management tools/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Characters' })).not.toBeInTheDocument()
  })
})
