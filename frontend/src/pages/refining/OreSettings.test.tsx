import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import OreSettings from './OreSettings'
import { refiningApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  refiningApi: {
    settings: vi.fn(),
    settingsOptions: vi.fn(),
    updateSettings: vi.fn(),
  },
}))

const SETTINGS = {
  structure_type: 'Tatara', rig_tier: 'T2', security_status: 0,
  implant: 'none', reprocessing_skill_level: 5, reprocessing_efficiency_skill_level: 5,
  scrapmetal_processing_skill_level: 5, ore_family_skill_levels: {}, refining_tax_rate: 0.05,
}
const OPTIONS = { structure_types: ['Tatara'], rig_tiers: ['T2'], implants: ['none'] }

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider><OreSettings /></MantineProvider>
    </QueryClientProvider>,
  )
}

describe('OreSettings security_status sign bug (confirmed real bug, 2026-10-01)', () => {
  it('keeps the minus sign when typing -0.5', async () => {
    vi.mocked(refiningApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(refiningApi.settingsOptions).mockResolvedValue(OPTIONS as any)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText(/System security/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '-0.5')
    expect(input.value).toBe('-0.5')
  })

  it('saves the real negative number, not its positive absolute value', async () => {
    vi.mocked(refiningApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(refiningApi.settingsOptions).mockResolvedValue(OPTIONS as any)
    vi.mocked(refiningApi.updateSettings).mockResolvedValue(SETTINGS as any)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText(/System security/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '-0.5')
    await user.click(screen.getByRole('button', { name: 'Save Settings' }))

    await waitFor(() => expect(refiningApi.updateSettings).toHaveBeenCalled())
    const sent = vi.mocked(refiningApi.updateSettings).mock.calls[0][0] as any
    expect(sent.security_status).toBe(-0.5)
  })

  it('still works for -1 (a value that never passes through a "-0" intermediate)', async () => {
    vi.mocked(refiningApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(refiningApi.settingsOptions).mockResolvedValue(OPTIONS as any)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText(/System security/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '-1')
    expect(input.value).toBe('-1')
  })

  it('still works for a positive value like 0.7', async () => {
    vi.mocked(refiningApi.settings).mockResolvedValue(SETTINGS as any)
    vi.mocked(refiningApi.settingsOptions).mockResolvedValue(OPTIONS as any)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText(/System security/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '0.7')
    expect(input.value).toBe('0.7')
  })
})
