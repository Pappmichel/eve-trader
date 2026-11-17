import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { hubsApi } from '../api/client'
import type { ObservedFreightRow } from '../api/types'
import { ObservedFreightHint } from './ObservedFreightHint'

vi.mock('../api/client', () => ({ hubsApi: { observedFreight: vi.fn() } }))

const ROW: ObservedFreightRow = {
  region_id: 10000002, hub: 'Jita', source: 'courier_contracts', suggested: 686, shipments: 14,
  volume_m3: 90000, window_days: 90, min_shipments: 3, start: '', end: '', reason: null, message: null,
}
const EMPTY: ObservedFreightRow = {
  ...ROW, region_id: 10000043, hub: 'Amarr', suggested: null, shipments: 1, reason: 'too_few_shipments',
  message: 'Fewer than 3 finished courier shipments from this hub to the home structure in the last 90 days.',
}

function renderHint(regionId: number, current: number, onApply = vi.fn(), compact = false) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MantineProvider>
        <ObservedFreightHint regionId={regionId} current={current} onApply={onApply} compact={compact} />
      </MantineProvider>
    </QueryClientProvider>,
  )
  return onApply
}

describe('ObservedFreightHint', () => {
  beforeEach(() => { vi.mocked(hubsApi.observedFreight).mockReset().mockResolvedValue([ROW, EMPTY]) })

  it('shows the hub rate with its shipment count and applies it only on click', async () => {
    const onApply = renderHint(10000002, 800)
    expect(await screen.findByText(/Actual last 90 days: 686 ISK\/m³ \(14 shipments\)/)).toBeInTheDocument()
    expect(screen.getByText(/other members' shipments/)).toBeInTheDocument()
    expect(onApply).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Use this value' }))
    expect(onApply).toHaveBeenCalledExactlyOnceWith(686)
  })

  it('offers no button when the field already holds the rate', async () => {
    renderHint(10000002, 686)
    expect(await screen.findByText(/686 ISK\/m³/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Use this value' })).toBeDisabled()
  })

  it('shows the reason and no button for a hub without enough shipments', async () => {
    renderHint(10000043, 800)
    expect(await screen.findByText(/no value \(1 of 3 shipments needed\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use this value' })).not.toBeInTheDocument()
  })

  it('points to the freight table for All hubs and renders nothing for a custom region', async () => {
    renderHint(0, 800)
    expect(screen.getByText(/shown in the freight table below/)).toBeInTheDocument()
  })

  it('renders nothing for a region that is not a trade hub', async () => {
    renderHint(10000016, 800)
    await vi.waitFor(() => expect(hubsApi.observedFreight).toHaveBeenCalled())
    expect(screen.queryByTestId('observed-freight')).not.toBeInTheDocument()
  })

  it('drops the explanation in compact mode', async () => {
    renderHint(10000002, 800, vi.fn(), true)
    expect(await screen.findByText(/686 ISK\/m³/)).toBeInTheDocument()
    expect(screen.queryByText(/other members' shipments/)).not.toBeInTheDocument()
  })
})
