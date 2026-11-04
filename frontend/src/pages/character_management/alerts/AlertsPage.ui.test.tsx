import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charAlertsApi } from '../../../api/client'
import type { AlertSettings } from '../../../api/types'
import AlertsPage from './AlertsPage'

vi.mock('../../../api/client', () => ({
  charAlertsApi: { settings: vi.fn(), setSubscription: vi.fn(), linkStart: vi.fn(), unlink: vi.fn(), test: vi.fn() },
  ApiError: class ApiError extends Error {},
}))

const off = { shared: true, enabled: false, include_content: false, lead_hours: 12 }

function settings(overrides: Partial<AlertSettings> = {}): AlertSettings {
  return {
    bot_configured: true, link_configured: true, linked: true,
    characters: [{ character_id: 1, character_name: 'Alice', alerts: { skillqueue_empty: off, mail_new: off } }],
    ...overrides,
  }
}

function renderPage(initial = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <Notifications />
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initial]}>
          <AlertsPage />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('AlertsPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(charAlertsApi.setSubscription).mockResolvedValue({})
  })

  it('shows every alert switched off by default', async () => {
    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings())
    renderPage()
    expect(await screen.findByLabelText('Alice skill queue alert')).not.toBeChecked()
    expect(screen.getByLabelText('Alice new mail alert')).not.toBeChecked()
    expect(screen.getByText('Linked')).toBeInTheDocument()
  })

  it('switching an alert on sends the subscription', async () => {
    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings())
    renderPage()
    await userEvent.click(await screen.findByLabelText('Alice skill queue alert'))
    await waitFor(() => expect(charAlertsApi.setSubscription).toHaveBeenCalled())
    expect(vi.mocked(charAlertsApi.setSubscription).mock.calls[0][0]).toMatchObject(
      { character_id: 1, alert_type: 'skillqueue_empty', enabled: true },
    )
  })

  it('blocks switching on until Discord is linked and the data is shared', async () => {
    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings({
      linked: false,
      characters: [{ character_id: 1, character_name: 'Alice', alerts: {
        skillqueue_empty: { ...off, shared: false }, mail_new: off,
      } }],
    }))
    renderPage()
    expect(await screen.findByLabelText('Alice skill queue alert')).toBeDisabled()
    expect(screen.getAllByText('Link Discord first').length).toBe(2)
    expect(screen.getByRole('button', { name: /link discord account/i })).toBeInTheDocument()
  })

  it('warns plainly that mail content leaves the app', async () => {
    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings({
      characters: [{ character_id: 1, character_name: 'Alice', alerts: {
        skillqueue_empty: off, mail_new: { ...off, enabled: true, include_content: true },
      } }],
    }))
    renderPage()
    expect(await screen.findByText(/can not be recalled/i)).toBeInTheDocument()
  })

  it('explains an unconfigured server instead of offering the link', async () => {
    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings({ bot_configured: false, link_configured: false, linked: false }))
    renderPage()
    expect(await screen.findByText(/has not set up the Discord bot/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /link discord account/i })).not.toBeInTheDocument()
  })

  it('reports the outcome of the Discord redirect', async () => {
    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings())
    renderPage('/?discord=linked')
    expect(await screen.findByText('Discord linked')).toBeInTheDocument()
  })
  it('contract alert filters are sent with the subscription; doctrine ones need the grant', async () => {
    const on = { ...off, enabled: true }
    const filters = {
      min_interval_minutes: null, notify_finished: true, notify_expired: true, doctrine_only: false, sold_out_only: false,
    }
    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings({
      characters: [{ character_id: 1, character_name: 'Alice', alerts: {
        contract_status: { ...on, contract_filters: filters },
      } }],
    }))
    const { unmount } = renderPage()
    await userEvent.click(await screen.findByLabelText('Alice contract alert expired'))
    await waitFor(() => expect(charAlertsApi.setSubscription).toHaveBeenCalled())
    expect(vi.mocked(charAlertsApi.setSubscription).mock.calls[0][0]).toMatchObject({
      alert_type: 'contract_status', enabled: true, contract_filters: { ...filters, notify_expired: false },
    })
    expect(screen.queryByLabelText('Alice contract alert doctrine only')).not.toBeInTheDocument()
    unmount()

    vi.mocked(charAlertsApi.settings).mockResolvedValue(settings({
      doctrine_granted: true,
      characters: [{ character_id: 1, character_name: 'Alice', alerts: {
        contract_status: { ...on, contract_filters: filters },
      } }],
    }))
    renderPage()
    await userEvent.click(await screen.findByLabelText('Alice contract alert sold out only'))
    await waitFor(() => expect(vi.mocked(charAlertsApi.setSubscription).mock.calls.length).toBe(2))
    expect(vi.mocked(charAlertsApi.setSubscription).mock.calls[1][0].contract_filters).toMatchObject(
      { sold_out_only: true },
    )
  })
})
