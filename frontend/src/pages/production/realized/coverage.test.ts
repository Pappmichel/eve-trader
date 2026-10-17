import { describe, expect, it } from 'vitest'

import { coverageColor } from './coverage'

describe('coverageColor', () => {
  it('is green at 90%, yellow at 70%, red below, and unknown for null', () => {
    expect(coverageColor(0.9)).toBe('accent')
    expect(coverageColor(1)).toBe('accent')
    expect(coverageColor(0.7)).toBe('warn')
    expect(coverageColor(0.899)).toBe('warn')
    expect(coverageColor(0.699)).toBe('danger')
    expect(coverageColor(0)).toBe('danger')
    expect(coverageColor(null)).toBeNull()
    expect(coverageColor(undefined)).toBeNull()
    expect(coverageColor(Number.NaN)).toBeNull()
  })
})
