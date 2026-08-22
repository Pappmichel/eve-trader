import { describe, expect, it } from 'vitest'
import { dateTime, duration, isk, pct, qty } from './format'

describe('isk', () => {
  it('formats a positive number with thousands separators and a unit suffix', () => {
    expect(isk(1234567)).toBe('1,234,567 ISK')
  })

  it('rounds to the nearest whole ISK', () => {
    expect(isk(1234.6)).toBe('1,235 ISK')
  })

  it('renders null/undefined/NaN as an em dash, not "0" or "NaN"', () => {
    expect(isk(null)).toBe('–')
    expect(isk(undefined)).toBe('–')
    expect(isk(NaN)).toBe('–')
  })
})

describe('qty', () => {
  it('formats with thousands separators, no unit suffix', () => {
    expect(qty(1234567)).toBe('1,234,567')
  })

  it('renders null/undefined/NaN as an em dash', () => {
    expect(qty(null)).toBe('–')
    expect(qty(undefined)).toBe('–')
    expect(qty(NaN)).toBe('–')
  })
})

describe('pct', () => {
  it('scales a fraction to a percentage with one decimal place', () => {
    expect(pct(0.1234)).toBe('12.3%')
  })

  it('renders null/undefined/NaN as an em dash', () => {
    expect(pct(null)).toBe('–')
    expect(pct(undefined)).toBe('–')
    expect(pct(NaN)).toBe('–')
  })
})

describe('dateTime', () => {
  it('renders null/undefined as "never"', () => {
    expect(dateTime(null)).toBe('never')
    expect(dateTime(undefined)).toBe('never')
    expect(dateTime('')).toBe('never')
  })

  it('treats an offset-less ISO string as UTC (appends "Z") rather than local time', () => {
    // Backend's older datetime.utcnow().isoformat() convention - no
    // trailing offset at all. A bare `new Date(...)` on this string would
    // be silently mis-parsed as local time by the JS Date constructor.
    const naive = dateTime('2026-06-15T12:00:00')
    const explicit = dateTime('2026-06-15T12:00:00Z')
    expect(naive).toBe(explicit)
  })

  it('leaves an already-offset ISO string untouched', () => {
    expect(dateTime('2026-06-15T12:00:00+00:00')).toBe(dateTime('2026-06-15T12:00:00Z'))
  })
})

describe('duration', () => {
  it('renders null/undefined/NaN as an em dash', () => {
    expect(duration(null)).toBe('–')
    expect(duration(undefined)).toBe('–')
    expect(duration(NaN)).toBe('–')
  })

  it('renders zero or negative seconds as "done"', () => {
    expect(duration(0)).toBe('done')
    expect(duration(-5)).toBe('done')
  })

  it('formats minutes only, under an hour', () => {
    expect(duration(90)).toBe('1m')
    expect(duration(3599)).toBe('59m')
  })

  it('formats hours and minutes, under a day', () => {
    expect(duration(3661)).toBe('1h 1m')
  })

  it('formats days, hours, and minutes', () => {
    expect(duration(90061)).toBe('1d 1h 1m')
  })
})
