import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'

vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn(() => 'id') } }))

import { notifications } from '@mantine/notifications'
import { NotificationBell } from './components/NotificationBell'
import { notify, resetNotificationsForTests } from './notify'

function renderBell() {
  return render(<MantineProvider><NotificationBell /></MantineProvider>)
}

describe('notify + NotificationBell', () => {
  beforeEach(() => {
    sessionStorage.clear()
    resetNotificationsForTests()
    vi.mocked(notifications.show).mockClear()
  })

  it('forwards to Mantine unchanged', () => {
    const options = { title: 'Saved', message: 'ok', color: 'accent' }
    notify(options)
    expect(notifications.show).toHaveBeenCalledWith(options)
  })

  it('shows an unread count, then lists entries and clears the count when opened', async () => {
    const user = userEvent.setup()
    renderBell()
    notify({ title: 'First', message: 'one', color: 'accent' })
    notify({ title: 'Second', message: 'two', color: 'danger' })

    const bell = await screen.findByRole('button', { name: 'Notifications (2 unread)' })
    await user.click(bell)

    expect(await screen.findByText('Second')).toBeInTheDocument()
    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument()
  })

  it('keeps at most 50 entries and can be cleared', async () => {
    const user = userEvent.setup()
    renderBell()
    for (let i = 0; i < 55; i++) notify({ title: `n${i}`, message: '' })
    await user.click(screen.getByRole('button', { name: /Notifications/ }))
    expect(await screen.findByText('n54')).toBeInTheDocument()
    expect(screen.queryByText('n4')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(await screen.findByText('Nothing yet in this session.')).toBeInTheDocument()
  })
})
