import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { PriceDataAlert } from './PriceDataAlert'

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['production', 'sde', 'item-names'], [
    { type_id: 34, type_name: 'Tritanium' },
    { type_id: 35, type_name: 'Pyerite' },
  ])
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>{ui}</MantineProvider>
    </QueryClientProvider>,
  )
}

describe('PriceDataAlert', () => {
  it('shows nothing when flags are missing or all fine', () => {
    wrap(<PriceDataAlert flags={undefined} />)
    wrap(<PriceDataAlert flags={{ adjusted_prices_available: true, cost_indices_available: true }} />)
    // Older cached plan without the new fields: undefined must not warn.
    wrap(<PriceDataAlert flags={{}} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows red alerts only when a flag is strictly false', () => {
    wrap(
      <PriceDataAlert
        flags={{
          adjusted_prices_available: false,
          cost_indices_available: false,
        }}
      />,
    )
    expect(screen.getByText(/Job Cost figures are currently unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/System cost indices unavailable/i)).toBeInTheDocument()
  })

  it('shows a yellow stale warning with rounded hours', () => {
    wrap(<PriceDataAlert flags={{ price_data_stale_hours: 3.7 }} />)
    expect(screen.getByText(/Using older ESI industry data/i)).toBeInTheDocument()
    expect(screen.getByText(/about 4 hours ago/i)).toBeInTheDocument()
  })

  it('shows a red alert when market quotes are unavailable', () => {
    wrap(<PriceDataAlert flags={{ prices_available: false }} />)
    expect(screen.getByText(/Market quotes are unavailable/i)).toBeInTheDocument()
  })

  it('shows a yellow job cost hint with the count and up to 10 names', () => {
    const ids = [34, 35, ...Array.from({ length: 10 }, (_, i) => 1000 + i)]
    wrap(<PriceDataAlert flags={{ adjusted_prices_available: true, adjusted_prices_missing: ids }} />)
    expect(screen.getByText(/Job cost understated/i)).toBeInTheDocument()
    expect(screen.getByText(/no adjusted price for 12 materials/i)).toBeInTheDocument()
    expect(screen.getByText(/Tritanium, Pyerite/)).toBeInTheDocument()
    expect(screen.getByText(/and 2 more/)).toBeInTheDocument()
  })

  it('shows no job cost hint when nothing is missing', () => {
    wrap(<PriceDataAlert flags={{ adjusted_prices_available: true, adjusted_prices_missing: [] }} />)
    expect(screen.queryByText(/Job cost understated/i)).not.toBeInTheDocument()
  })
})
