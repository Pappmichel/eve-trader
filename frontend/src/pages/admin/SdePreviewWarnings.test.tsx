import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it } from 'vitest'

import { SdePreviewWarnings } from './SdePreviewWarnings'

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
}

describe('SdePreviewWarnings', () => {
  it('shows nothing for a normal dump', () => {
    wrap(<SdePreviewWarnings shrinkWarnings={[]} drift={{ checked: 40, drifts: [], unchecked: [] }} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('lists shrinking tables with old, new and the drop', () => {
    wrap(
      <SdePreviewWarnings
        shrinkWarnings={[{ table: 'sde_types', old: 53306, new: 3, drop_percent: 100 }]}
      />,
    )
    expect(screen.getByText(/Tables would shrink a lot/i)).toBeInTheDocument()
    expect(screen.getByText(/sde_types: 53,306 → 3/)).toBeInTheDocument()
  })

  it('lists drifting constants with code and dump values', () => {
    wrap(
      <SdePreviewWarnings
        shrinkWarnings={[]}
        drift={{
          checked: 40,
          unchecked: [],
          drifts: [{
            constant: 'DECRYPTORS[Accelerant].probability_multiplier', type_id: 34201,
            type_name: 'Accelerant Decryptor', attribute: 'inventionPropabilityMultiplier',
            expected: 1.2, actual: 1.25,
          }],
        }}
      />,
    )
    expect(screen.getByText(/Constants differ from the dump/i)).toBeInTheDocument()
    expect(screen.getByText(/code 1.2, dump 1.25/)).toBeInTheDocument()
  })
})
