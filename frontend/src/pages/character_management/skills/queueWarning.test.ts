import { describe, expect, it } from 'vitest'

import { warningSummary, warningText } from './queueWarning'

describe('queue guard wording', () => {
  it('words every warning kind', () => {
    expect(warningText({ kind: 'empty', hours_left: null })).toBe('queue empty')
    expect(warningText({ kind: 'paused', hours_left: null })).toBe('queue paused')
    expect(warningText({ kind: 'ended', hours_left: 0 })).toBe('queue finished')
    expect(warningText({ kind: 'ends_soon', hours_left: 5.5 })).toBe('ends in 5.5 h')
    expect(warningText({ kind: 'ends_soon', hours_left: 12 })).toBe('ends in 12 h')
    expect(warningText({ kind: 'ends_soon', hours_left: 0.4 })).toBe('ends in under 1 h')
  })
  it('summarises a count for the hub badge, nothing for zero', () => {
    expect(warningSummary(0)).toBeUndefined()
    expect(warningSummary(1)).toBe('1 queue warning')
    expect(warningSummary(3)).toBe('3 queue warnings')
  })
})
