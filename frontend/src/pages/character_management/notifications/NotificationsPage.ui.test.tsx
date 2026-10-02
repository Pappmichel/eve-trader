import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charNotificationsApi } from '../../../api/client'
import type { NotificationItem, NotificationsList } from '../../../api/types'
import NotificationsPage from './NotificationsPage'

vi.mock('../../../api/client', () => ({
  charNotificationsApi: { list: vi.fn(), detail: vi.fn(), setRead: vi.fn(), sync: vi.fn() },
  ApiError: class ApiError extends Error {},
}))

function item(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    character_id: 1, character_name: 'Alice', notification_id: 1, type: 'StructureUnderAttack',
    category: 'structures', sent_at: '2026-09-28T10:00:00Z', summary: 'Structure under attack - Jita',
    read: false, read_in_game: false, sender_id: null, sender_type: null, ...overrides,
  }
}

function list(overrides: Partial<NotificationsList> = {}): NotificationsList {
  return {
    items: [item()], total: 1, unread_total: 1,
    types: [{ type: 'StructureUnderAttack', label: 'Structure under attack', count: 1 }],
    categories: [{ category: 'structures', label: 'Structures', count: 1 }],
    characters: [{ character_id: 1, character_name: 'Alice', synced_at: new Date().toISOString(), last_error: null }],
    hidden_characters: [],
    ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <Notifications />
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <NotificationsPage />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

beforeEach(() => vi.resetAllMocks())

describe('Notifications page', () => {
  it('syncs once on open when a character is stale or never synced, but not when it is fresh', async () => {
    vi.mocked(charNotificationsApi.sync).mockResolvedValue({ ok: true, characters: {}, in_flight: [], failed: [] })
    const chars = (synced_at: string | null) =>
      [{ character_id: 1, character_name: 'Alice', synced_at, last_error: null }]
    vi.mocked(charNotificationsApi.list).mockResolvedValue(list({ characters: chars(null) }))
    const first = renderPage()
    await screen.findByText('Structure under attack - Jita')
    await waitFor(() => expect(charNotificationsApi.sync).toHaveBeenCalledTimes(1))
    first.unmount()

    vi.mocked(charNotificationsApi.sync).mockClear()
    vi.mocked(charNotificationsApi.list).mockResolvedValue(list({ characters: chars(new Date().toISOString()) }))
    renderPage()
    await screen.findByText('Structure under attack - Jita')
    expect(charNotificationsApi.sync).not.toHaveBeenCalled()
  })

  it('says so when no character is shared', async () => {
    vi.mocked(charNotificationsApi.list).mockResolvedValue(list({ items: [], total: 0, characters: [] }))
    renderPage()
    expect(await screen.findByText(/No character is shared with Notifications/)).toBeInTheDocument()
  })

  it('lists notifications, marks unread ones and flags a character that was never synced', async () => {
    vi.mocked(charNotificationsApi.list).mockResolvedValue(list({
      items: [item(), item({ notification_id: 2, summary: 'War declared', read: true, read_in_game: true })],
      total: 2,
      characters: [
        { character_id: 1, character_name: 'Alice', synced_at: 'x', last_error: null },
        { character_id: 2, character_name: 'Bob', synced_at: null, last_error: null },
      ],
    }))
    renderPage()
    expect(await screen.findByText('Structure under attack - Jita')).toBeInTheDocument()
    expect(screen.getByText('new')).toBeInTheDocument()                         // only the unread one
    expect(screen.getByText('read in game')).toBeInTheDocument()                // no toggle for an in-game read
    expect(screen.getByText(/Not synced yet: Bob/)).toBeInTheDocument()
  })

  it('marks one read and reloads', async () => {
    vi.mocked(charNotificationsApi.list).mockResolvedValue(list())
    vi.mocked(charNotificationsApi.setRead).mockResolvedValue({ changed: 1 })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Mark read' }))
    expect(charNotificationsApi.setRead).toHaveBeenCalledWith(1, [1], true)
    await waitFor(() => expect(charNotificationsApi.list).toHaveBeenCalledTimes(2))
  })

  it('sends the unread filter and goes back to the first page when a filter changes', async () => {
    vi.mocked(charNotificationsApi.list).mockResolvedValue(list({ total: 120 }))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('1–50 of 120')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(charNotificationsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 })))
    await user.click(screen.getByRole('checkbox', { name: /Unread only/ }))
    await waitFor(() => expect(charNotificationsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ unreadOnly: true, offset: 0 })))
  })

  it('opens the parsed details, or says they could not be read', async () => {
    vi.mocked(charNotificationsApi.list).mockResolvedValue(list())
    vi.mocked(charNotificationsApi.detail).mockResolvedValue({
      character_id: 1, notification_id: 1, type: 'StructureUnderAttack', category: 'structures',
      sent_at: null, summary: 'Structure under attack - Jita', parsed: true, read: false,
      details: [{ key: 'shieldPercentage', value: '71.2' }],
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Structure under attack - Jita' }))
    expect(await screen.findByText('shieldPercentage')).toBeInTheDocument()
    expect(screen.getByText('71.2')).toBeInTheDocument()
  })
})
