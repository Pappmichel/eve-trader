import { describe, expect, it } from 'vitest'

import { locationLabel } from './locationLabel'

describe('locationLabel', () => {
  it('prefers the name', () => {
    expect(locationLabel({ location_id: 1046835728433, location_name: 'Fort', location_status: 'forbidden' })).toBe('Fort')
  })

  it('says why a structure has no name', () => {
    expect(locationLabel({ location_id: 1046835728433, location_status: 'transient' }))
      .toBe('Structure 1046835728433 (lookup failed, will retry)')
  })

  it('falls back to the id, or the caller text without one', () => {
    expect(locationLabel({ location_id: 5_000_000 })).toBe('Location 5000000')
    expect(locationLabel({ location_id: null }, 'Unknown')).toBe('Unknown')
  })
})
