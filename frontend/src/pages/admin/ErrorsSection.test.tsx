import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => ({
  adminApi: {
    errorGroups: vi.fn(),
    errors: vi.fn(),
  },
}))

import { adminApi } from '../../api/client'
import { ErrorsSection } from './ErrorsSection'

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><ErrorsSection /></MantineProvider>
    </QueryClientProvider>,
  )
}

describe('ErrorsSection', () => {
  beforeEach(() => {
    vi.mocked(adminApi.errorGroups).mockResolvedValue([{
      source: 'frontend', message: 'Boom <img src=x onerror=alert(1)>', count: 7, tenant_count: 3,
      first_seen: '2026-11-01T10:00:00Z', last_seen: '2026-11-02T10:00:00Z', detail: null, path: '/trading',
    }])
    vi.mocked(adminApi.errors).mockResolvedValue([])
  })

  it('shows grouped errors with count and tenants, message as plain text', async () => {
    renderSection()
    expect(await screen.findByText('Boom <img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(adminApi.errors).not.toHaveBeenCalled()
  })

  it('loads the raw list only when switched', async () => {
    renderSection()
    await screen.findByText(/Boom/)
    await userEvent.click(screen.getByText('Latest 200'))
    expect(adminApi.errors).toHaveBeenCalled()
  })
})
