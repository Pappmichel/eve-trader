import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { adminApi, productionApi } from '../../api/client'
import type { AccessRequestRow, AdminUser } from '../../api/types'
import AdminPage from './AdminPage'

vi.mock('../../api/client', () => ({
  adminApi: {
    accessRequests: vi.fn(), addAllowlistEntry: vi.fn(), addUser: vi.fn(), allowlist: vi.fn(), allowlistImpact: vi.fn(),
    approveAccessRequest: vi.fn(), backups: vi.fn(), createBackup: vi.fn(), deleteAccessRequest: vi.fn(),
    errorGroups: vi.fn(), errors: vi.fn(), locationReport: vi.fn(), refreshAffiliations: vi.fn(),
    refreshJitaPriceCache: vi.fn(), rejectAccessRequest: vi.fn(), removeAllowlistEntry: vi.fn(), removeUser: vi.fn(),
    searchAllowlist: vi.fn(), setStructureResolutionFallback: vi.fn(), setToolGrants: vi.fn(),
    startStructureNameResolve: vi.fn(), structureResolutionFallback: vi.fn(), structureResolveStatus: vi.fn(),
    tenants: vi.fn(), users: vi.fn(), purgeTenantPreview: vi.fn(), purgeTenant: vi.fn(),
  },
  productionApi: { sdeCounts: vi.fn(), sdeFreshness: vi.fn() },
  ApiError: class ApiError extends Error {},
}))
vi.mock('../../components/CcpNotice', () => ({ default: () => null }))

function user(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    character_id: 90000001, character_name: 'Alice Example', tenant_id: 't-1', tenant_name: 'Alice Example',
    tool_keys: ['portfolio', 'characters'], corporation_id: null, corporation_name: 'Example Corp',
    alliance_id: null, alliance_name: null, affiliation_checked_at: null, access_suspended: false, ...overrides,
  }
}

function request(overrides: Partial<AccessRequestRow> = {}): AccessRequestRow {
  return {
    character_id: 90000002, character_name: 'Bob Applicant', corporation_id: 5, corporation_name: 'Example Corp',
    alliance_id: null, alliance_name: null, status: 'pending', requested_at: null, last_login_at: null,
    decided_at: null, decided_by_character_id: null, no_longer_allowlisted: false, ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider env="test">
      <Notifications />
      <ModalsProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter><AdminPage /></MemoryRouter>
        </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(productionApi.sdeCounts).mockResolvedValue({} as never)
  vi.mocked(productionApi.sdeFreshness).mockResolvedValue({} as never)
  vi.mocked(adminApi.structureResolveStatus).mockResolvedValue({ run_id: null, status: 'idle' })
  vi.mocked(adminApi.structureResolutionFallback).mockResolvedValue({ global_structure_resolution_fallback: false } as never)
  vi.mocked(adminApi.backups).mockResolvedValue([])
  vi.mocked(adminApi.allowlist).mockResolvedValue([])
  vi.mocked(adminApi.tenants).mockResolvedValue([])
  vi.mocked(adminApi.users).mockResolvedValue([user()])
  vi.mocked(adminApi.accessRequests).mockImplementation(async (status?: string) =>
    (status === 'pending' ? [request()] : []) as never)
  vi.mocked(adminApi.errorGroups).mockResolvedValue({ rows: [], total: 0 } as never)
  vi.mocked(adminApi.errors).mockResolvedValue({ rows: [], total: 0 } as never)
})

describe('Admin page', () => {
  it('has one h1 and lists the registered users', async () => {
    renderPage()
    expect(await screen.findByText('Example Corp', { selector: 'div,span,td,p' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Admin')
    expect(screen.getAllByText('90000001').length).toBeGreaterThan(0)
  })

  it('saves exactly the ticked tools (replace, not merge) and ticks Characters for an ESI tool', async () => {
    // Start from a user with no grants so the only change is the one tick below.
    vi.mocked(adminApi.users).mockResolvedValue([user({ tool_keys: [] })])
    vi.mocked(adminApi.setToolGrants).mockResolvedValue({} as never)
    const ui = userEvent.setup()
    renderPage()
    const trading = await screen.findByRole('checkbox', { name: 'trading' })
    await ui.click(trading)
    expect(screen.getByRole('checkbox', { name: 'characters' })).toBeChecked()
    await ui.click(screen.getByRole('button', { name: 'Save' }))
    expect(adminApi.setToolGrants).toHaveBeenCalledTimes(1)
    const [characterId, keys] = vi.mocked(adminApi.setToolGrants).mock.calls[0]
    expect(characterId).toBe(90000001)
    expect([...keys].sort()).toEqual(['characters', 'trading'])
  })

  it('unticking a tool sends the remaining set, not just the change', async () => {
    vi.mocked(adminApi.users).mockResolvedValue([user({ tool_keys: ['portfolio', 'characters', 'sorting'] })])
    vi.mocked(adminApi.setToolGrants).mockResolvedValue({} as never)
    const ui = userEvent.setup()
    renderPage()
    await ui.click(await screen.findByRole('checkbox', { name: 'sorting' }))
    await ui.click(screen.getByRole('button', { name: 'Save' }))
    const keys = vi.mocked(adminApi.setToolGrants).mock.calls[0][1]
    expect([...keys].sort()).toEqual(['characters', 'portfolio'])
  })

  it('removes a user only after the confirmation', async () => {
    vi.mocked(adminApi.removeUser).mockResolvedValue({
      tokens_deleted: 0, revoked: 0, revoke_failed: 0, revoke_skipped: false, discord_unlinked: false,
    } as never)
    const ui = userEvent.setup()
    renderPage()
    await ui.click(await screen.findByRole('button', { name: 'Remove user Alice Example' }))
    expect(adminApi.removeUser).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    await ui.click(within(dialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(adminApi.removeUser).toHaveBeenCalledWith(90000001))
  })

  it('names the add-user and allowlist search inputs', async () => {
    renderPage()
    expect(await screen.findByLabelText('Character name')).toBeInTheDocument()
    expect(screen.getByLabelText('Corporation or alliance name')).toBeInTheDocument()
  })

  it('rejects a pending access request', async () => {
    vi.mocked(adminApi.rejectAccessRequest).mockResolvedValue({} as never)
    const ui = userEvent.setup()
    renderPage()
    await ui.click(await screen.findByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(adminApi.rejectAccessRequest).toHaveBeenCalledTimes(1))
    // react-query passes a mutation context as a second argument; only the id matters.
    expect(vi.mocked(adminApi.rejectAccessRequest).mock.calls[0][0]).toBe(90000002)
  })

  it('approves a pending access request with the ticked tools', async () => {
    vi.mocked(adminApi.approveAccessRequest).mockResolvedValue({} as never)
    const ui = userEvent.setup()
    renderPage()
    await ui.click(await screen.findByRole('button', { name: 'Approve' }))
    const dialog = await screen.findByRole('dialog')
    // Preselected: every tool except admin and module_reprocessing.
    expect(within(dialog).getByRole('checkbox', { name: 'admin' })).not.toBeChecked()
    await ui.click(within(dialog).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(adminApi.approveAccessRequest).toHaveBeenCalledTimes(1))
    const [characterId, keys] = vi.mocked(adminApi.approveAccessRequest).mock.calls[0]
    expect(characterId).toBe(90000002)
    expect(keys).not.toContain('admin')
    expect(keys).not.toContain('module_reprocessing')
    expect(keys).toContain('characters')
  })
})
