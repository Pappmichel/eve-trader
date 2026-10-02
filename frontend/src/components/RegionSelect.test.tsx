import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RegionSelect } from './RegionSelect'
import { sdeApi } from '../api/client'

vi.mock('../api/client', () => ({ sdeApi: { regions: vi.fn() } }))

function renderSelect(value: number, onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><RegionSelect label="Region" value={value} onChange={onChange} /></MantineProvider>
    </QueryClientProvider>,
  )
  return onChange
}

const REGIONS = [
  { region_id: 10000002, region_name: 'The Forge' },
  { region_id: 10000009, region_name: 'Insmother' },
]

describe('RegionSelect', () => {
  beforeEach(() => vi.mocked(sdeApi.regions).mockReset())

  it('shows the region name for the current id and lists names', async () => {
    vi.mocked(sdeApi.regions).mockResolvedValue(REGIONS)
    const onChange = renderSelect(10000002)
    const input = screen.getByRole('combobox') as HTMLInputElement
    await waitFor(() => expect(input.value).toBe('The Forge'))
    await userEvent.click(input)
    await userEvent.click(await screen.findByText('Insmother'))
    expect(onChange).toHaveBeenCalledWith(10000009)
    expect(screen.queryByText(/appear after the next SDE refresh/)).toBeNull()
  })

  it('falls back to "Region <id>" and shows a hint when the list is empty', async () => {
    vi.mocked(sdeApi.regions).mockResolvedValue([])
    renderSelect(10000009)
    expect(await screen.findByText(/appear after the next SDE refresh/)).toBeTruthy()
    expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe('Region 10000009')
  })

  it('keeps an unknown id as "Region <id>" alongside the known names', async () => {
    vi.mocked(sdeApi.regions).mockResolvedValue(REGIONS)
    renderSelect(12345)
    const input = screen.getByRole('combobox') as HTMLInputElement
    await waitFor(() => expect(input.value).toBe('Region 12345'))
    await userEvent.click(input)
    expect(await screen.findByText('The Forge')).toBeTruthy()
  })
})
