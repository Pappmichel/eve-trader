// "New data available" (FRONTEND_PLAN.md B.12). The backend reports when each
// data source last changed (/api/updates/versions); the first answer is the
// baseline, and a later answer that differs means the scheduler (or another
// tab) changed data behind this page. The user's own actions are not "new":
// useAction / useBackgroundJob call acceptOwnDataChange, which makes the next
// answer the new baseline instead.
import { useSyncExternalStore } from 'react'
import type { QueryClient } from '@tanstack/react-query'

export type DataVersions = Record<string, string | null>

export const DATA_VERSIONS_KEY = ['updates', 'versions']

export const DATA_SOURCE_LABELS: Record<string, string> = {
  esi: 'ESI data',
  trading_pipeline: 'Trading pipeline',
  shortlist: 'Shortlist',
  portfolio: 'Portfolio snapshot',
  jita_prices: 'Jita prices',
}

let baseline: DataVersions | null = null
let latest: DataVersions | null = null
let acceptNext = false
let changed: string[] = []
const listeners = new Set<() => void>()

function emit() {
  const next = latest && baseline
    ? Object.keys(latest).filter((k) => latest![k] != null && latest![k] !== baseline![k])
    : []
  if (next.join() !== changed.join()) changed = next
  listeners.forEach((l) => l())
}

export function recordDataVersions(versions: DataVersions): void {
  if (baseline === null || acceptNext) {
    baseline = versions
    acceptNext = false
  }
  latest = versions
  emit()
}

// The current user just changed data themselves: refetch the versions and
// take that answer as the new baseline.
export function acceptOwnDataChange(queryClient: QueryClient): void {
  acceptNext = true
  queryClient.invalidateQueries({ queryKey: DATA_VERSIONS_KEY })
}

// "Refresh" on the notice: reload every active query and start over from
// the newest versions.
export function refreshForNewData(queryClient: QueryClient): void {
  baseline = latest
  emit()
  queryClient.invalidateQueries()
}

export function useChangedDataSources(): string[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    () => changed,
  )
}

// Tests only.
export function resetDataVersions(): void {
  baseline = null
  latest = null
  acceptNext = false
  changed = []
}
