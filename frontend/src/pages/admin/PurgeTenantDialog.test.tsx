import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => ({
  adminApi: {
    purgeTenantPreview: vi.fn(),
    purgeTenant: vi.fn(),
  },
}))

import { adminApi } from '../../api/client'
import { PurgeTenantDialog } from './PurgeTenantDialog'

const tenant = { tenant_id: '11111111-1111-1111-1111-111111111111', name: 'Old Pilot', created_at: null }

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><PurgeTenantDialog tenant={tenant} onClose={() => {}} /></MantineProvider>
    </QueryClientProvider>,
  )
}

describe('PurgeTenantDialog', () => {
  beforeEach(() => {
    vi.mocked(adminApi.purgeTenantPreview).mockResolvedValue({
      tenant_id: tenant.tenant_id, name: tenant.name, total_rows: 12,
      tables: [{ table: 'character_assets', rows: 12 }],
    })
    vi.mocked(adminApi.purgeTenant).mockResolvedValue({
      purged: tenant.tenant_id, name: tenant.name, rows_deleted: 12, tables: [{ table: 'character_assets', rows: 12 }],
      tokens_deleted: 0, revoked: 0, revoke_failed: 0, revoke_skipped: false, discord_unlinked: false,
    })
  })

  it('shows the counts and only deletes after the exact name is typed', async () => {
    renderDialog()
    expect(await screen.findByText(/deletes 12 rows in 1 tables/)).toBeInTheDocument()
    expect(screen.getByText(/Rolling backups keep the data until they rotate out/)).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Delete for good' })
    expect(button).toBeDisabled()

    const input = screen.getByLabelText('Type the tenant name to confirm')
    await userEvent.type(input, 'Old Pilo')
    expect(button).toBeDisabled()
    await userEvent.type(input, 't')
    expect(button).toBeEnabled()

    await userEvent.click(button)
    expect(adminApi.purgeTenant).toHaveBeenCalledWith(tenant.tenant_id, 'Old Pilot')
  })
})
