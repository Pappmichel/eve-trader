import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import type { CharInfoSyncResult } from '../api/types'

// Character Management pages whose snapshot kinds the scheduler no longer
// refreshes in the background (esi_data registry `schedule_mode = "on_demand"`,
// docs/SCHEDULER_REWORK_PLAN.md) sync themselves when opened with old data.
// 6 h = the default "normal" freshness tier; a fixed value because the tier
// settings live behind the Trading grant, which these pages' users may lack.
export const AUTO_SYNC_MAX_AGE_HOURS = 6

// `stamps` holds one entry per shown character: the newest attempt (or
// success) timestamp over the page's kinds, or null if there is none yet.
// Stale = at least one character has never been tried or was last tried
// longer ago than `maxAgeHours`. Using the *attempt* time means a kind that
// keeps failing (or needs re-auth) is retried once per window, not on every
// page open.
export function isStale(
  stamps: ReadonlyArray<string | null | undefined>,
  maxAgeHours: number = AUTO_SYNC_MAX_AGE_HOURS,
  now: number = Date.now(),
): boolean {
  return stamps.some((s) => {
    if (!s) return true
    const t = Date.parse(s)
    return Number.isNaN(t) || now - t > maxAgeHours * 3_600_000
  })
}

// Newest timestamp among `values`, ignoring empties.
export function newestStamp(values: ReadonlyArray<string | null | undefined>): string | null {
  const present = values.filter((v): v is string => !!v).sort()
  return present.length ? present[present.length - 1] : null
}

// Runs `sync` once per mount when `ready && stale`, then refetches
// `invalidateKeys`. Silent apart from per-character failures (same toast as the
// manual Refresh); a failing request is swallowed - the Refresh button still
// reports it.
export function useSyncWhenStale(opts: {
  ready: boolean
  stale: boolean
  sync: () => Promise<CharInfoSyncResult>
  invalidateKeys: string[][]
}) {
  const queryClient = useQueryClient()
  const started = useRef(false)
  const { ready, stale, sync, invalidateKeys } = opts

  useEffect(() => {
    if (!ready || !stale || started.current) return
    started.current = true
    void (async () => {
      try {
        const result = await sync()
        for (const f of result?.failed ?? []) {
          notifications.show({
            title: `Sync failed for ${f.name ?? f.owner_id}`,
            message: f.error ?? 'Unknown error',
            color: 'danger',
          })
        }
      } catch {
        // see above
      } finally {
        for (const key of invalidateKeys) void queryClient.invalidateQueries({ queryKey: key })
      }
    })()
  }, [ready, stale, sync, invalidateKeys, queryClient])
}
