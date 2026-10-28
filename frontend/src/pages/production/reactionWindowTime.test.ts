import { describe, expect, it } from 'vitest'

import { formatEveUtc, fromUtcInputValue, toUtcInputValue } from './reactionWindowTime'

describe('reactionWindowTime (EVE UTC)', () => {
  it('round-trips a UTC ISO through datetime-local without local-zone shift', () => {
    const stored = '2026-11-03T17:00:00.000Z'
    expect(toUtcInputValue(stored)).toBe('2026-11-03T17:00')
    expect(fromUtcInputValue('2026-11-03T17:00')).toBe(stored)
  })

  it('treats the datetime-local string as UTC, not the browser zone', () => {
    // A local Date("2026-11-03T17:00") would shift by the host offset; we must not.
    const iso = fromUtcInputValue('2026-01-15T00:30')
    expect(iso).toBe('2026-01-15T00:30:00.000Z')
    expect(toUtcInputValue(iso)).toBe('2026-01-15T00:30')
  })

  it('returns empty / null for blank input', () => {
    expect(toUtcInputValue(null)).toBe('')
    expect(fromUtcInputValue('')).toBeNull()
    expect(fromUtcInputValue('   ')).toBeNull()
  })

  it('formats display labels in UTC', () => {
    expect(formatEveUtc('2026-11-03T17:00:00.000Z')).toBe('2026-11-03 17:00 UTC')
    expect(formatEveUtc(null)).toBe('not set')
  })
})
