import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, charMailApi } from '../../../api/client'
import type { MailCapabilityState, MailFolders, MailOpened, MailRow } from '../../../api/types'
import MailPage from './MailPage'

vi.mock('../../../api/client', () => ({
  charMailApi: {
    folders: vi.fn(), mails: vi.fn(), open: vi.fn(), search: vi.fn(), archive: vi.fn(),
    setArchive: vi.fn(), refreshArchive: vi.fn(), searchRecipients: vi.fn(), send: vi.fn(),
    markRead: vi.fn(), setLabels: vi.fn(), deleteMail: vi.fn(), createLabel: vi.fn(), deleteLabel: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  },
}))

const api = vi.mocked(charMailApi)

function folders(send: MailCapabilityState, organize: MailCapabilityState): MailFolders {
  return {
    characters: [{
      character_id: 1, character_name: 'Alice', archived: false, state: 'ok', total_unread: 1,
      capabilities: { send, organize }, lists: [],
      labels: [
        { label_id: 1, name: 'Inbox', color: null, unread_count: 1, system: true },
        { label_id: 32, name: 'Contracts', color: '#ff6600', unread_count: 0, system: false },
        { label_id: 33, name: 'Fleet', color: '#ffffff', unread_count: 0, system: false },
      ],
    }],
    unread: { '1': 1 },
  }
}

function row(over: Partial<MailRow> = {}): MailRow {
  return {
    mail_id: 5, from_id: 9, from_name: 'Sender One', subject: 'Fleet op', timestamp: '2026-09-28T18:00:00Z',
    is_read: false, recipients: [],
    received_by: [{ character_id: 1, character_name: 'Alice', is_read: false, labels: [1, 32], archived: false }],
    ...over,
  }
}

function opened(over: Partial<MailOpened> = {}): MailOpened {
  return {
    ...row(), character_id: 1, archived: false, body: 'Bring <b>logi</b><br>and boosters',
    recipients: [
      { recipient_id: 1, recipient_type: 'character', name: 'Alice' },
      { recipient_id: 2, recipient_type: 'character', name: 'Friend' },
      { recipient_id: 700, recipient_type: 'corporation', name: 'Some Corp' },
    ],
    ...over,
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

function setup(send: MailCapabilityState = 'ready', organize: MailCapabilityState = 'ready', mail = opened()) {
  api.folders.mockResolvedValue(folders(send, organize))
  api.mails.mockResolvedValue({
    mails: [row()], next_cursors: {},
    characters: [{ character_id: 1, character_name: 'Alice', archived: false, state: 'ok' }],
  })
  api.open.mockResolvedValue(mail)
  api.archive.mockResolvedValue({ characters: [] })
  api.markRead.mockResolvedValue({ character_id: 1, mail_id: 5, read: true })
  api.setLabels.mockResolvedValue({ character_id: 1, mail_id: 5, labels: [] })
  api.deleteMail.mockResolvedValue({ character_id: 1, mail_id: 5, deleted: true })
  api.searchRecipients.mockResolvedValue({ results: [] })
  api.send.mockResolvedValue({ sent: true, mail_id: 9, recipients: [{ recipient_id: 2, recipient_type: 'character', name: 'Friend' }] })
  return renderPage()
}

async function openMail(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /Open mail Fleet op/ }))
  await screen.findByTestId('mail-body')
}

beforeEach(() => vi.resetAllMocks())

describe('Compose', () => {
  it('is disabled with an explanation when no character may send', async () => {
    setup('not_enabled', 'ready')
    await screen.findByText('Fleet op')
    expect(screen.getByRole('button', { name: /Compose/ })).toBeDisabled()
  })

  it('composes a mail: recipients by name and by suggestion, then sends with exactly that payload', async () => {
    const user = userEvent.setup()
    setup()
    api.searchRecipients.mockResolvedValue({ results: [{ type: 'corporation', id: 700, name: 'Some Corp' }] })
    await screen.findByText('Fleet op')
    await user.click(screen.getByRole('button', { name: /Compose/ }))
    const dialog = await screen.findByRole('dialog')
    // one sender -> preselected; nothing else filled in
    expect(within(dialog).getByRole('button', { name: 'Send' })).toBeDisabled()

    await user.type(within(dialog).getByLabelText('Add recipient'), 'Friend Pilot{Enter}')
    expect(within(dialog).getByText('Friend Pilot')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Add recipient'), 'some c')
    await user.click(await within(dialog).findByRole('button', { name: /Some Corp/ }))
    expect(api.searchRecipients).toHaveBeenCalledWith(1, 'some c')
    await user.type(within(dialog).getByLabelText('Subject'), 'Op tonight')
    await user.type(within(dialog).getByLabelText('Message'), 'Be there')
    await user.click(within(dialog).getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(api.send).toHaveBeenCalledTimes(1))
    expect(api.send).toHaveBeenCalledWith({
      from_character_id: 1,
      recipients: [{ type: undefined, name: 'Friend Pilot' }, { type: 'corporation', id: 700, name: 'Some Corp' }],
      subject: 'Op tonight', body: 'Be there', approved_cost: 0,
    })
    expect(await screen.findByText('Mail sent')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('lets you remove a recipient chip', async () => {
    const user = userEvent.setup()
    setup()
    await screen.findByText('Fleet op')
    await user.click(screen.getByRole('button', { name: /Compose/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Add recipient'), 'Someone{Enter}')
    await user.click(within(dialog).getByRole('button', { name: 'Remove recipient Someone' }))
    expect(within(dialog).queryByText('Someone')).not.toBeInTheDocument()
  })

  it('asks before paying a CSPA charge, then resends with the approved cost', async () => {
    const user = userEvent.setup()
    setup()
    api.send.mockResolvedValueOnce({ sent: false, needs_approval: true, cost: 12.4 })
    await screen.findByText('Fleet op')
    await user.click(screen.getByRole('button', { name: /Compose/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Add recipient'), 'Rich Pilot{Enter}')
    await user.type(within(dialog).getByLabelText('Subject'), 'Hi')
    await user.click(within(dialog).getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/costs 12 ISK/)).toBeInTheDocument()      // nothing sent yet
    expect(api.send).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: /Send for/ }))
    await waitFor(() => expect(api.send).toHaveBeenCalledTimes(2))
    expect(api.send.mock.calls[1][0].approved_cost).toBe(13)                   // rounded UP so the approval covers it
  })

  it('keeps the draft open and shows the server message when sending fails', async () => {
    const user = userEvent.setup()
    setup()
    api.send.mockRejectedValueOnce(new ApiError(502, 'ESI did not confirm the delivery - check the Sent folder'))
    await screen.findByText('Fleet op')
    await user.click(screen.getByRole('button', { name: /Compose/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Add recipient'), 'Someone{Enter}')
    await user.type(within(dialog).getByLabelText('Subject'), 'Hi')
    await user.click(within(dialog).getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/check the Sent folder/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(within(screen.getByRole('dialog')).getByLabelText('Subject')).toHaveValue('Hi')
  })

  it('does not lose what you typed when the page re-renders behind the dialog', async () => {
    const user = userEvent.setup()
    setup()
    await screen.findByText('Fleet op')
    await user.click(screen.getByRole('button', { name: /Compose/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Subject'), 'Keep me')
    await new Promise((r) => setTimeout(r, 400))            // debounce + query settle
    expect(within(dialog).getByLabelText('Subject')).toHaveValue('Keep me')
  })
})

describe('Reader actions', () => {
  it('replies to the sender as the receiving character, quoting the body', async () => {
    const user = userEvent.setup()
    setup()
    await openMail(user)
    await user.click(screen.getByRole('button', { name: 'Reply' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Remove recipient Sender One' })).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Subject')).toHaveValue('Re: Fleet op')
    expect((within(dialog).getByLabelText('Message') as HTMLTextAreaElement).value).toContain('> Bring logi\n> and boosters')
  })

  it('reply-all also addresses the other recipients, but not the replying character', async () => {
    const user = userEvent.setup()
    setup()
    await openMail(user)
    await user.click(screen.getByRole('button', { name: 'Reply all' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Remove recipient Friend' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Remove recipient Some Corp' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Remove recipient Sender One' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Remove recipient Alice' })).not.toBeInTheDocument()
  })

  it('forwards with an empty To line', async () => {
    const user = userEvent.setup()
    setup()
    await openMail(user)
    await user.click(screen.getByRole('button', { name: 'Forward' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Subject')).toHaveValue('Fwd: Fleet op')
    expect(within(dialog).queryByRole('button', { name: /Remove recipient/ })).not.toBeInTheDocument()
  })

  it('disables every write action, with the reason, when the capabilities are missing', async () => {
    const user = userEvent.setup()
    setup('not_enabled', 'reauth_needed')
    await openMail(user)
    for (const name of ['Reply', 'Reply all', 'Forward', 'Mark read', 'Labels', 'Delete']) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
    expect(api.markRead).not.toHaveBeenCalled()                 // no silent auto-mark-read either
  })

  it('marks an unread mail read in the game when it is opened - once - if the character may organize', async () => {
    const user = userEvent.setup()
    setup()
    await openMail(user)
    await waitFor(() => expect(api.markRead).toHaveBeenCalledWith(1, 5, true))
    expect(api.markRead).toHaveBeenCalledTimes(1)
    // the reader now offers the opposite action without another fetch of the mail
    expect(await screen.findByRole('button', { name: 'Mark unread' })).toBeEnabled()
    expect(api.open).toHaveBeenCalledTimes(1)
  })

  it('does not auto-mark a mail that is already read', async () => {
    const user = userEvent.setup()
    setup('ready', 'ready', opened({
      is_read: true, received_by: [{ character_id: 1, character_name: 'Alice', is_read: true, labels: [1], archived: false }],
    }))
    await openMail(user)
    expect(screen.getByRole('button', { name: 'Mark unread' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Mark unread' }))
    expect(api.markRead).toHaveBeenCalledWith(1, 5, false)
    expect(api.markRead).toHaveBeenCalledTimes(1)
  })

  it('toggles a custom label with the full resulting label set', async () => {
    const user = userEvent.setup()
    setup()
    await openMail(user)
    await user.click(screen.getByRole('button', { name: /Labels/ }))
    expect(await screen.findByRole('checkbox', { name: 'Contracts' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Fleet' })).not.toBeChecked()
    await user.click(screen.getByRole('checkbox', { name: 'Fleet' }))
    await waitFor(() => expect(api.setLabels).toHaveBeenCalledWith(1, 5, [1, 32, 33]))
    await user.click(screen.getByRole('checkbox', { name: 'Contracts' }))
    await waitFor(() => expect(api.setLabels).toHaveBeenLastCalledWith(1, 5, [1, 33]))   // removing keeps the rest
  })

  it('deletes only after a confirmation, then closes the reader', async () => {
    const user = userEvent.setup()
    setup()
    await openMail(user)
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText(/deletes "Fleet op" in the game/)).toBeInTheDocument()
    expect(api.deleteMail).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Keep' }))
    expect(api.deleteMail).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(await screen.findByRole('button', { name: 'Delete mail' }))
    await waitFor(() => expect(api.deleteMail).toHaveBeenCalledWith(1, 5))
    await waitFor(() => expect(screen.queryByTestId('mail-body')).not.toBeInTheDocument())
  })
})

describe('Label management in Mail settings', () => {
  async function openSettings(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByText('Fleet op')
    await user.click(screen.getByRole('button', { name: /Mail settings/ }))
    return screen.findByText('Labels', { selector: 'p' })
  }

  it('creates a label with a name and an allowed colour', async () => {
    api.createLabel.mockResolvedValue({ character_id: 1, label_id: 40, name: 'Logi', color: '#ffffff' })
    const user = userEvent.setup()
    setup()
    await openSettings(user)
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
    await user.type(screen.getByLabelText('New label name'), 'Logi')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(api.createLabel).toHaveBeenCalledWith({ character_id: 1, name: 'Logi', color: '#ffffff' })
  })

  it('deletes a custom label only after a confirmation', async () => {
    api.deleteLabel.mockResolvedValue({ character_id: 1, label_id: 32, deleted: true })
    const user = userEvent.setup()
    setup()
    await openSettings(user)
    await user.click(screen.getByRole('button', { name: 'Delete label Contracts' }))
    expect(api.deleteLabel).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: 'Delete label' }))
    await waitFor(() => expect(api.deleteLabel).toHaveBeenCalledWith(1, 32))
  })

  it('explains how to enable label management when no character may organize', async () => {
    const user = userEvent.setup()
    setup('ready', 'not_enabled')
    await screen.findByText('Fleet op')
    await user.click(screen.getByRole('button', { name: /Mail settings/ }))
    expect(await screen.findByText(/tick "Organize mail" for a character/)).toBeInTheDocument()
    expect(screen.queryByLabelText('New label name')).not.toBeInTheDocument()
  })
})
