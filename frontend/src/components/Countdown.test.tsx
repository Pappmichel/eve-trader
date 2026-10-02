import { MantineProvider } from '@mantine/core'
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Countdown } from './Countdown'

function show(until: string | null | undefined) {
  return render(<MantineProvider><Countdown until={until} doneLabel="none" /></MantineProvider>)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-29T10:00:00Z'))
})
afterEach(() => vi.useRealTimers())

describe('Countdown', () => {
  it('shows the time left, days and hours only when non-zero', () => {
    show('2026-09-30T12:30:00Z')
    expect(screen.getByText('1d 2h 30m')).toBeInTheDocument()
  })

  it('shows the done label for a missing, invalid or past date', () => {
    for (const value of [null, undefined, 'not a date', '2026-09-29T09:59:00Z']) {
      const { unmount } = show(value)
      expect(screen.getByText('none')).toBeInTheDocument()
      unmount()
    }
  })

  it('keeps counting down and flips to the done label when the time is up', () => {
    show('2026-09-29T10:05:00Z')
    expect(screen.getByText('5m')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(3 * 60_000) })
    expect(screen.getByText('2m')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(3 * 60_000) })
    expect(screen.getByText('none')).toBeInTheDocument()
  })
})
