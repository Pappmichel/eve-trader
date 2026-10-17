import { describe, expect, it } from 'vitest'

import {
  customRangeError,
  periodLongerThan90Days,
  realizedPeriodSearch,
  shouldAutoRecompute,
} from './period'

describe('realizedPeriodSearch', () => {
  it('maps the presets, since T0, and a custom range onto query params', () => {
    expect(realizedPeriodSearch({ mode: 'days', days: 7 })).toBe('days=7')
    expect(realizedPeriodSearch({ mode: 'days', days: 30 })).toBe('days=30')
    expect(realizedPeriodSearch({ mode: 'days', days: 90 })).toBe('days=90')
    expect(realizedPeriodSearch({ mode: 'since_t0' })).toBe('since_t0=true')
    expect(realizedPeriodSearch({ mode: 'custom', start: '2026-07-01', end: '2026-10-01' }))
      .toBe('start=2026-07-01&end=2026-10-02')
  })

  it('does not send since_t0 together with days', () => {
    expect(realizedPeriodSearch({ mode: 'since_t0' })).not.toContain('days=')
    expect(realizedPeriodSearch({ mode: 'days', days: 30 })).not.toContain('since_t0')
  })
})

describe('periodLongerThan90Days', () => {
  it('uses weekly bars only past 90 days', () => {
    expect(periodLongerThan90Days({ mode: 'days', days: 90 }, null)).toBe(false)
    expect(periodLongerThan90Days({ mode: 'days', days: 7 }, null)).toBe(false)
    expect(periodLongerThan90Days(
      { mode: 'custom', start: '2026-01-01', end: '2026-05-01' },
      null,
    )).toBe(true)
    expect(periodLongerThan90Days(
      { mode: 'custom', start: '2026-07-01', end: '2026-09-01' },
      null,
    )).toBe(false)
    expect(periodLongerThan90Days(
      { mode: 'since_t0' },
      '2026-01-01T00:00:00Z',
      new Date('2026-10-01T00:00:00Z'),
    )).toBe(true)
    expect(periodLongerThan90Days({ mode: 'since_t0' }, null, new Date('2026-10-01T00:00:00Z'))).toBe(false)
  })
})

describe('customRangeError', () => {
  it('requires both ends and a forward range', () => {
    expect(customRangeError('', '2026-10-01')).toMatch(/both/)
    expect(customRangeError('2026-10-02', '2026-10-01')).toMatch(/before/)
    // The end date is inclusive, so a one-day range is valid.
    expect(customRangeError('2026-10-01', '2026-10-01')).toBeNull()
    expect(realizedPeriodSearch({ mode: 'custom', start: '2026-10-31', end: '2026-10-31' }))
      .toBe('start=2026-10-31&end=2026-11-01')
    expect(customRangeError('2026-07-01', '2026-10-01')).toBeNull()
  })
})

describe('shouldAutoRecompute', () => {
  const base = { stale: false, needs_rebuild: false, cutover_t0: null, archive_since: null }

  it('recomputes a stale report, or a rebuild when an archive exists', () => {
    expect(shouldAutoRecompute({ ...base, stale: true })).toBe(true)
    expect(shouldAutoRecompute({ ...base, needs_rebuild: true, archive_since: '2026-09-01' })).toBe(true)
    expect(shouldAutoRecompute({ ...base, needs_rebuild: true, cutover_t0: '2026-09-01' })).toBe(true)
    expect(shouldAutoRecompute({ ...base, needs_rebuild: true })).toBe(false)
    expect(shouldAutoRecompute(base)).toBe(false)
  })
})
