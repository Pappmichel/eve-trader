import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it } from 'vitest'
import { RatesTable } from './common'

const RATES = [
  { type_id: 1, name: 'Coolant', tier: 2, per_hour: 10 },
  { type_id: 2, name: 'Water', tier: 1, per_hour: 40 },
]

describe('RatesTable', () => {
  it('shows the hub per row when hubs are given', () => {
    render(<MantineProvider><RatesTable title="Exports (sold at)" rates={RATES} hubs={{ 1: 'Amarr', 2: null }} /></MantineProvider>)
    expect(screen.getByText('Amarr')).toBeTruthy()
    expect(screen.getByText('–')).toBeTruthy()
  })

  it('has no hub column without hubs', () => {
    render(<MantineProvider><RatesTable title="Exports" rates={RATES} /></MantineProvider>)
    expect(screen.queryByText('Amarr')).toBeNull()
    expect(screen.queryByText('–')).toBeNull()
  })
})
