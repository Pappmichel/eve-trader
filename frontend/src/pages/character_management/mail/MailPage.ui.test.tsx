import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charMailApi } from '../../../api/client'
import type { MailArchiveRow, MailFolders, MailOpened, MailPage as MailPageData, MailRow } from '../../../api/types'
import MailPage from './MailPage'

vi.mock('../../../api/client', () => ({
  charMailApi: {
    folders: vi.fn(), mails: vi.fn(), open: vi.fn(), search: vi.fn(),
    archive: vi.fn(), setArchive: vi.fn(), refreshArchive: vi.fn(),
  },
  ApiError: class ApiError extends Error {},
}))

const api = vi.mocked(charMailApi)
const NO_WRITE = { send: 'not_enabled', organize: 'not_enabled' } as const

function labels(unreadInbox = 0) {
  return [
    { label_id: 1, name: 'Inbox', color: null, unread_count: unreadInbox, system: true },
    { label_id: 2, name: 'Sent', color: null, unread_count: 0, system: true },
    { label_id: 32, name: 'Contracts', color: null, unread_count: 0, system: false },
  ]
}

function folders(over: Partial<MailFolders> = {}): MailFolders {
  return {
    characters: [
      { character_id: 1, character_name: 'Alice', archived: false, state: 'ok', labels: labels(2), lists: [], total_unread: 2, capabilities: NO_WRITE },
      { character_id: 2, character_name: 'Bob', archived: true, state: 'ok', labels: labels(1), lists: [], total_unread: 1, capabilities: NO_WRITE },
    ],
    unread: { '1': 3, '2': 0, '4': 0, '8': 0, '16': 0 },
    ...over,
  }
}

function row(id: number, over: Partial<MailRow> = {}): MailRow {
  return {
    mail_id: id, from_id: 9, from_name: 'Sender One', subject: `Subject ${id}`,
    timestamp: `2026-09-${String(id).padStart(2, '0')}T10:00:00Z`, is_read: true, recipients: [],
    received_by: [{ character_id: 1, character_name: 'Alice', is_read: true, labels: [1], archived: false }],
    ...over,
  }
}

function page(mails: MailRow[], next: Record<string, number> = {}): MailPageData {
  return { mails, next_cursors: next, characters: [{ character_id: 1, character_name: 'Alice', archived: false, state: 'ok' }] }
}

function archiveRow(over: Partial<MailArchiveRow> = {}): MailArchiveRow {
  return {
    character_id: 1, character_name: 'Alice', shared: true, archive_enabled: false, reauth_needed: false,
    backfill_state: 'off', headers_complete: false, error: null, last_refresh_at: null,
    counts: { headers: 0, bodies: 0 }, ...over,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <Notifications />
      <ModalsProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <MailPage />
          </MemoryRouter>
        </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  api.folders.mockResolvedValue(folders())
  api.mails.mockResolvedValue(page([row(1)]))
  api.archive.mockResolvedValue({ characters: [archiveRow()] })
})

describe('Mail page', () => {
  it('shows folders with unread badges and the unified inbox by default', async () => {
    api.mails.mockResolvedValue(page([row(2, { is_read: false }), row(1)]))
    renderPage()
    expect(await screen.findByText('Subject 2')).toBeInTheDocument()
    expect(api.mails).toHaveBeenCalledWith({ labelId: 1, characterId: null, cursors: null })
    const inbox = screen.getAllByRole('button', { name: /Inbox/ })[0]
    expect(within(inbox).getByText('3')).toBeInTheDocument()            // unified unread
    expect(screen.getAllByText('Alice').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Bob').length).toBeGreaterThan(0)
    // the unread row is visually distinct (bold), the read one is not
    expect(screen.getByText('Subject 2')).toHaveStyle({ fontWeight: '700' })
    expect(screen.getByText('Subject 1')).not.toHaveStyle({ fontWeight: '700' })
  })

  it('shows which character (and whether from the archive) received a mail in the unified inbox', async () => {
    api.mails.mockResolvedValue(page([row(4, {
      received_by: [
        { character_id: 1, character_name: 'Alice', is_read: true, labels: [1], archived: false },
        { character_id: 2, character_name: 'Bob', is_read: false, labels: [1], archived: true },
      ],
    })]))
    renderPage()
    expect(await screen.findByText('Subject 4')).toBeInTheDocument()
    expect(screen.getAllByText('Alice').length).toBeGreaterThan(1)          // folder tree + chip
    expect(screen.getByText('Bob · archive')).toBeInTheDocument()
  })

  it('loads more with the per-character cursors and merges the pages', async () => {
    api.mails
      .mockResolvedValueOnce(page([row(9), row(8)], { '1': 8 }))
      .mockResolvedValueOnce(page([row(7)]))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Subject 9')
    await user.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByText('Subject 7')).toBeInTheDocument()
    expect(api.mails).toHaveBeenLastCalledWith({ labelId: 1, characterId: null, cursors: { '1': 8 } })
    expect(screen.getByText('Subject 9')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()   // exhausted
  })

  it('selecting a character folder asks for exactly that character and label', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Subject 1')
    await user.click(screen.getByRole('button', { name: /Bob/ }))
    await user.click(await screen.findByRole('button', { name: 'Contracts' }))
    await waitFor(() => expect(api.mails).toHaveBeenLastCalledWith({ labelId: 32, characterId: 2, cursors: null }))
  })

  it('opens a mail and renders its body sanitized - no script, no handlers, no dead links', async () => {
    const opened: MailOpened = {
      ...row(1), character_id: 1, archived: false,
      recipients: [{ recipient_id: 3, recipient_type: 'character', name: 'Bob' }],
      body: 'Hi <b>there</b><script>window.__pwned = 1</script><img src=x onerror="window.__pwned = 2">'
        + '<a href="javascript:window.__pwned=3">bad</a> <a href="https://example.org/x">good</a>',
    }
    api.open.mockResolvedValue(opened)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Open mail Subject 1/ }))
    const body = await screen.findByTestId('mail-body')
    expect(api.open).toHaveBeenCalledWith(1, 1)
    expect(body.querySelector('script, img')).toBeNull()
    expect(body.innerHTML).not.toContain('onerror')
    expect(body.querySelector('b')?.textContent).toBe('there')
    const links = Array.from(body.querySelectorAll('a'))
    expect(links.find((a) => a.textContent === 'bad')?.getAttribute('href')).toBeNull()
    expect(links.find((a) => a.textContent === 'good')?.getAttribute('rel')).toContain('noopener')
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
    expect(screen.getByText(/From/)).toBeInTheDocument()
  })

  it('filters the loaded mail client-side without another request', async () => {
    api.mails.mockResolvedValue(page([row(1, { subject: 'Fleet doctrine' }), row(2, { subject: 'Hello' })]))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Fleet doctrine')
    await user.type(screen.getByLabelText('Filter loaded mail'), 'doctr')
    expect(screen.queryByText('Hello')).not.toBeInTheDocument()
    expect(screen.getByText('Fleet doctrine')).toBeInTheDocument()
    expect(api.mails).toHaveBeenCalledTimes(1)
  })

  it('archive search is only offered when a character is archived, and shows the results', async () => {
    api.archive.mockResolvedValue({ characters: [archiveRow({
      archive_enabled: true, backfill_state: 'done', counts: { headers: 5, bodies: 5 },
      last_refresh_at: new Date().toISOString(),
    })] })
    api.search.mockResolvedValue({
      mails: [row(7, { subject: 'Found in archive' })], searched: [], unsearchable: [
        { character_id: 2, character_name: 'Bob', archived: false, state: 'ok' },
      ],
    })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Subject 1')
    await user.type(screen.getByLabelText('Filter loaded mail'), 'archive')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Search archive' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Search archive' }))
    expect(await screen.findByText('Found in archive')).toBeInTheDocument()
    expect(api.search).toHaveBeenCalledWith('archive', null)
    expect(screen.getByText(/not searchable, live only: Bob/)).toBeInTheDocument()
  })

  it('without any archive the search button is disabled', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Subject 1')
    await user.type(screen.getByLabelText('Filter loaded mail'), 'anything')
    expect(screen.getByRole('button', { name: 'Search archive' })).toBeDisabled()
  })

  it('refreshes a stale archive once when opened', async () => {
    api.archive.mockResolvedValue({ characters: [archiveRow({
      archive_enabled: true, backfill_state: 'done', last_refresh_at: '2020-01-01T00:00:00Z',
      counts: { headers: 1, bodies: 1 },
    })] })
    api.refreshArchive.mockResolvedValue({ characters: [] })
    renderPage()
    await waitFor(() => expect(api.refreshArchive).toHaveBeenCalledTimes(1))
    await screen.findByText('Subject 1')
    expect(api.refreshArchive).toHaveBeenCalledTimes(1)          // not again on re-render
  })

  it('does not refresh an archive that is still fresh, or one that is not shared', async () => {
    api.archive.mockResolvedValue({ characters: [
      archiveRow({ archive_enabled: true, last_refresh_at: new Date().toISOString(), counts: { headers: 1, bodies: 1 } }),
      archiveRow({ character_id: 2, character_name: 'Bob', archive_enabled: true, shared: false, last_refresh_at: null }),
    ] })
    renderPage()
    await screen.findByText('Subject 1')
    await new Promise((r) => setTimeout(r, 50))
    expect(api.refreshArchive).not.toHaveBeenCalled()
  })

  it('reports a character that needs a re-authorize and a character that errored', async () => {
    api.mails.mockResolvedValue({
      mails: [], next_cursors: {},
      characters: [
        { character_id: 1, character_name: 'Alice', archived: false, state: 'reauth_needed' },
        { character_id: 2, character_name: 'Bob', archived: false, state: 'error', detail: 'ESI returned HTTP 500' },
      ],
    })
    renderPage()
    expect(await screen.findByText(/Alice: needs a re-authorize/)).toBeInTheDocument()
    expect(screen.getByText(/Bob: ESI returned HTTP 500/)).toBeInTheDocument()
    expect(screen.getByText('No mail here.')).toBeInTheDocument()
  })

  it('points to the Characters page when nobody shares Mail', async () => {
    api.folders.mockResolvedValue({ characters: [], unread: {} })
    api.mails.mockResolvedValue({ mails: [], next_cursors: {}, characters: [] })
    renderPage()
    expect(await screen.findByText(/No character shares Mail yet/)).toBeInTheDocument()
  })
})

describe('Mail settings', () => {
  async function openSettings(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByText('Subject 1')
    await user.click(screen.getByRole('button', { name: /Mail settings/ }))
    return screen.findByText(/Live by default/)
  }

  it('turns the archive on for a shared character', async () => {
    api.setArchive.mockResolvedValue(archiveRow({ archive_enabled: true }))
    const user = userEvent.setup()
    renderPage()
    await openSettings(user)
    await user.click(screen.getByRole('switch', { name: 'Archive mail for Alice' }))
    expect(api.setArchive).toHaveBeenCalledWith({ character_id: 1, enabled: true, confirm_delete: undefined })
  })

  it('explains why an unshared or un-authorized character cannot be archived', async () => {
    api.archive.mockResolvedValue({ characters: [
      archiveRow({ shared: false }),
      archiveRow({ character_id: 2, character_name: 'Bob', reauth_needed: true }),
    ] })
    const user = userEvent.setup()
    renderPage()
    await openSettings(user)
    expect(screen.getByRole('switch', { name: 'Archive mail for Alice' })).toBeDisabled()
    expect(screen.getByRole('switch', { name: 'Archive mail for Bob' })).toBeDisabled()
    expect(screen.getByText(/Not shared with Mail - tick it on the Characters page first/)).toBeInTheDocument()
    expect(screen.getByText(/Needs a re-authorize with the mail scope/)).toBeInTheDocument()
  })

  it('turning the archive off asks for confirmation before deleting, then sends confirm_delete', async () => {
    api.archive.mockResolvedValue({ characters: [archiveRow({
      archive_enabled: true, backfill_state: 'done', counts: { headers: 12, bodies: 12 },
      last_refresh_at: new Date().toISOString(),
    })] })
    api.setArchive.mockResolvedValue({ character_id: 1, archive_enabled: false, deleted: { headers: 12, messages: 12 } })
    const user = userEvent.setup()
    renderPage()
    await openSettings(user)
    await user.click(screen.getByRole('switch', { name: 'Archive mail for Alice' }))
    expect(api.setArchive).not.toHaveBeenCalled()                          // nothing until confirmed
    expect(await screen.findByText(/deletes 12 archived mail\(s\) of Alice/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete archive' }))
    expect(api.setArchive).toHaveBeenCalledWith({ character_id: 1, enabled: false, confirm_delete: true })
  })

  it('keeping the archive sends nothing', async () => {
    api.archive.mockResolvedValue({ characters: [archiveRow({
      archive_enabled: true, backfill_state: 'done', counts: { headers: 3, bodies: 3 },
      last_refresh_at: new Date().toISOString(),
    })] })
    const user = userEvent.setup()
    renderPage()
    await openSettings(user)
    await user.click(screen.getByRole('switch', { name: 'Archive mail for Alice' }))
    await user.click(await screen.findByRole('button', { name: 'Keep' }))
    expect(api.setArchive).not.toHaveBeenCalled()
  })

  it('shows the backfill progress and a recorded error', async () => {
    api.archive.mockResolvedValue({ characters: [archiveRow({
      archive_enabled: true, backfill_state: 'error', error: 'ESI returned HTTP 500',
      counts: { headers: 100, bodies: 40 }, last_refresh_at: new Date().toISOString(),
    })] })
    const user = userEvent.setup()
    renderPage()
    await openSettings(user)
    expect(screen.getByText('Stopped by an error (100 mails, 40 with text)')).toBeInTheDocument()
    expect(screen.getByText('ESI returned HTTP 500')).toBeInTheDocument()
  })
})
