import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charContactsApi } from '../../../api/client'
import type { ContactsCharacter } from '../../../api/types'
import ContactsPage from './ContactsPage'

vi.mock('../../../api/client', () => ({
  charContactsApi: { characters: vi.fn(), contacts: vi.fn(), calendar: vi.fn(), event: vi.fn() },
  ApiError: class ApiError extends Error {},
}))

const ok = { state: 'ok', value: null } as const
const notShared = { state: 'not_shared', value: null } as const

function character(overrides: Partial<ContactsCharacter> = {}): ContactsCharacter {
  return { character_id: 1, character_name: 'Alice', contacts: ok, calendar: ok, ...overrides }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter><ContactsPage /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

beforeEach(() => vi.resetAllMocks())

describe('Contacts & Calendar page', () => {
  it('says so without registered characters', async () => {
    vi.mocked(charContactsApi.characters).mockResolvedValue({ characters: [] })
    renderPage()
    expect(await screen.findByText(/No ESI characters registered/)).toBeInTheDocument()
  })

  it('lists contacts with badges and labels, and filters them', async () => {
    vi.mocked(charContactsApi.characters).mockResolvedValue({ characters: [character()] })
    vi.mocked(charContactsApi.contacts).mockResolvedValue({
      state: 'ok',
      value: {
        labels: ['Friends'],
        contacts: [
          { contact_id: 1, name: 'Zed', contact_type: 'character', standing: 5, is_blocked: false, is_watched: true, labels: ['Friends'] },
          { contact_id: 2, name: 'Evil Corp', contact_type: 'corporation', standing: -10, is_blocked: true, is_watched: false, labels: [] },
        ],
      },
    })
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('Zed')).toBeInTheDocument()
    expect(screen.getByText('watched')).toBeInTheDocument()
    expect(screen.getByText('blocked')).toBeInTheDocument()
    expect(screen.getByText('-10.0')).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('Filter...'), 'friends')
    expect(screen.queryByText('Evil Corp')).not.toBeInTheDocument()
    expect(screen.getByText('Zed')).toBeInTheDocument()
  })

  it('does not call ESI for a kind that is not shared, and says why', async () => {
    vi.mocked(charContactsApi.characters).mockResolvedValue({ characters: [character({ contacts: notShared })] })
    renderPage()
    expect(await screen.findByText('not shared')).toBeInTheDocument()
    expect(charContactsApi.contacts).not.toHaveBeenCalled()
  })

  it('opens a calendar event lazily', async () => {
    vi.mocked(charContactsApi.characters).mockResolvedValue({ characters: [character()] })
    vi.mocked(charContactsApi.contacts).mockResolvedValue({ state: 'ok', value: { labels: [], contacts: [] } })
    vi.mocked(charContactsApi.calendar).mockResolvedValue({
      state: 'ok', value: [{ event_id: 2, title: 'Fleet', event_date: '2026-10-02T18:00:00Z', importance: 1, response: 'accepted' }],
    })
    vi.mocked(charContactsApi.event).mockResolvedValue({
      state: 'ok', value: {
        event_id: 2, title: 'Fleet', date: '2026-10-02T18:00:00Z', duration: 60, importance: 1,
        owner_name: 'FC', owner_type: 'character', response: 'accepted', text: 'Bring guns',
      },
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Calendar' }))
    const open = await screen.findByRole('button', { name: 'Fleet' })
    expect(charContactsApi.event).not.toHaveBeenCalled()
    await user.click(open)
    expect(await screen.findByText('Bring guns')).toBeInTheDocument()
    expect(charContactsApi.event).toHaveBeenCalledWith(1, 2)
  })
})
