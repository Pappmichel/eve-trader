import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { HubSelect } from './HubSelect'
import { hubsApi } from '../api/client'

vi.mock('../api/client', () => ({ hubsApi: { freight: vi.fn(), updateFreight: vi.fn() } }))

function renderSelect(value: number, allowAll: boolean, onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><HubSelect label="Market hub" value={value} onChange={onChange} allowAll={allowAll} /></MantineProvider>
    </QueryClientProvider>,
  )
  return onChange
}

const FREIGHT = [
  { region_id: 10000002, hub: 'Jita', freight_cost_per_m3: 800 },
  { region_id: 10000043, hub: 'Amarr', freight_cost_per_m3: null },
  { region_id: 10000032, hub: 'Dodixie', freight_cost_per_m3: null },
  { region_id: 10000030, hub: 'Rens', freight_cost_per_m3: null },
]

describe('HubSelect', () => {
  beforeEach(() => {
    vi.mocked(hubsApi.freight).mockReset().mockResolvedValue(FREIGHT)
    vi.mocked(hubsApi.updateFreight).mockReset().mockResolvedValue(FREIGHT)
  })

  it('offers "All hubs" only when allowed', async () => {
    const onChange = renderSelect(10000002, true)
    await userEvent.click(screen.getByRole('combobox', { name: 'Market hub' }))
    await userEvent.click(await screen.findByRole('option', { name: 'All hubs (best per item)' }))
    expect(onChange).toHaveBeenCalledWith(0)
  })

  it('has no "All hubs" option for single-hub tools', async () => {
    renderSelect(10000002, false)
    await userEvent.click(screen.getByRole('combobox', { name: 'Market hub' }))
    expect(screen.queryByRole('option', { name: 'All hubs (best per item)' })).toBeNull()
  })

  it('shows and saves the shared freight table in All hubs mode', async () => {
    renderSelect(0, true)
    const amarr = await screen.findByRole('textbox', { name: 'Amarr' })
    expect((screen.getByRole('textbox', { name: 'Jita' }) as HTMLInputElement).value).toBe('800')
    await userEvent.type(amarr, '1200')
    await userEvent.click(screen.getByRole('button', { name: 'Save freight table' }))
    await waitFor(() => expect(hubsApi.updateFreight).toHaveBeenCalled())
    expect(vi.mocked(hubsApi.updateFreight).mock.calls[0][0]).toEqual([
      { region_id: 10000002, freight_cost_per_m3: 800 },
      { region_id: 10000043, freight_cost_per_m3: 1200 },
      { region_id: 10000032, freight_cost_per_m3: null },
      { region_id: 10000030, freight_cost_per_m3: null },
    ])
  })
})
