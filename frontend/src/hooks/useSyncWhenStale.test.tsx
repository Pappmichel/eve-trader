import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { isStale, newestStamp, useSyncWhenStale } from './useSyncWhenStale'

const NOW = Date.parse('2026-09-29T12:00:00Z')
const hoursAgo = (h: number) => new Date(NOW - h * 3_600_000).toISOString()

describe('isStale', () => {
  it('is stale when any character has no stamp or an old one', () => {
    expect(isStale([hoursAgo(1), null], 6, NOW)).toBe(true)
    expect(isStale([hoursAgo(1), hoursAgo(7)], 6, NOW)).toBe(true)
    expect(isStale(['not a date'], 6, NOW)).toBe(true)
  })
  it('is fresh when every character was tried recently, or there are none', () => {
    expect(isStale([hoursAgo(1), hoursAgo(5)], 6, NOW)).toBe(false)
    expect(isStale([], 6, NOW)).toBe(false)
  })
  it('newestStamp ignores empties', () => {
    expect(newestStamp([null, undefined, '2026-01-01', '2026-02-01'])).toBe('2026-02-01')
    expect(newestStamp([null])).toBeNull()
  })
})

function wrapper() {
  const client = new QueryClient()
  const spy = vi.spyOn(client, 'invalidateQueries')
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { Wrapper, spy }
}

const OK = { ok: true, in_flight: [], failed: [] } as never

describe('useSyncWhenStale', () => {
  it('syncs once when ready and stale, then refetches the keys', async () => {
    const sync = vi.fn().mockResolvedValue(OK)
    const keys = [['char-info']]
    const { Wrapper, spy } = wrapper()
    const { rerender } = renderHook(
      () => useSyncWhenStale({ ready: true, stale: true, sync, invalidateKeys: keys }),
      { wrapper: Wrapper },
    )
    await waitFor(() => expect(sync).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ['char-info'] }))
    rerender()
    expect(sync).toHaveBeenCalledTimes(1)
  })

  it('does nothing while loading or when the data is fresh', () => {
    const sync = vi.fn().mockResolvedValue(OK)
    const { Wrapper } = wrapper()
    renderHook(() => useSyncWhenStale({ ready: false, stale: true, sync, invalidateKeys: [] }), { wrapper: Wrapper })
    renderHook(() => useSyncWhenStale({ ready: true, stale: false, sync, invalidateKeys: [] }), { wrapper: Wrapper })
    expect(sync).not.toHaveBeenCalled()
  })

  it('swallows a failing sync request', async () => {
    const sync = vi.fn().mockRejectedValue(new Error('boom'))
    const { Wrapper, spy } = wrapper()
    renderHook(() => useSyncWhenStale({ ready: true, stale: true, sync, invalidateKeys: [['k']] }), { wrapper: Wrapper })
    await waitFor(() => expect(spy).toHaveBeenCalled())
  })
})
