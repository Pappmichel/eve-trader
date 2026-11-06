import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it } from 'vitest'

import { PriceDataAlert } from './PriceDataAlert'

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
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
})
