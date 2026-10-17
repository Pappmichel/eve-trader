// Period selector -> query string for /api/production/realized/summary and
// /products. The backend rejects since_t0 combined with days or a range,
// and a custom range needs both ends. Default is the last 30 days.

export type RealizedPeriod =
  | { mode: 'days'; days: 7 | 30 | 90 }
  | { mode: 'since_t0' }
  | { mode: 'custom'; start: string; end: string }

export const DEFAULT_PERIOD: RealizedPeriod = { mode: 'days', days: 30 }

const MS_PER_DAY = 86_400_000

export function realizedPeriodSearch(period: RealizedPeriod): string {
  if (period.mode === 'days') return `days=${period.days}`
  if (period.mode === 'since_t0') return 'since_t0=true'
  const params = new URLSearchParams()
  params.set('start', period.start)
  params.set('end', period.end)
  return params.toString()
}

/** True when the selected window is longer than 90 days (weekly chart bars). */
export function periodLongerThan90Days(
  period: RealizedPeriod,
  cutoverT0: string | null | undefined,
  now: Date = new Date(),
): boolean {
  if (period.mode === 'days') return period.days > 90
  if (period.mode === 'custom') {
    const start = Date.parse(period.start)
    const end = Date.parse(period.end)
    if (Number.isNaN(start) || Number.isNaN(end)) return false
    return (end - start) / MS_PER_DAY > 90
  }
  if (!cutoverT0) return false
  const t0 = Date.parse(cutoverT0)
  if (Number.isNaN(t0)) return false
  return (now.getTime() - t0) / MS_PER_DAY > 90
}

export function customRangeError(start: string, end: string): string | null {
  if (!start || !end) return 'A custom range needs both a start and an end date.'
  const startAt = Date.parse(start)
  const endAt = Date.parse(end)
  if (Number.isNaN(startAt) || Number.isNaN(endAt)) return 'Enter valid dates.'
  if (endAt <= startAt) return 'End must be after start.'
  return null
}

/** Once per page open: stale archive, or a rebuild is required and an archive exists. */
export function shouldAutoRecompute(status: {
  stale: boolean
  needs_rebuild: boolean
  cutover_t0: string | null
  archive_since: string | null
}): boolean {
  const archiveExists = Boolean(status.cutover_t0 || status.archive_since)
  return status.stale || (status.needs_rebuild && archiveExists)
}

// Survives React StrictMode's mount/unmount/remount (dev) so a page open
// starts one rebuild. A real navigation away clears it on a short timer that
// the next mount cancels.
let autoRebuildArmed = false
let autoRebuildReset: ReturnType<typeof setTimeout> | undefined

export function claimAutoRebuild(): boolean {
  if (autoRebuildArmed) return false
  autoRebuildArmed = true
  return true
}

export function holdAutoRebuild(): void {
  clearTimeout(autoRebuildReset)
}

export function scheduleAutoRebuildReset(): void {
  clearTimeout(autoRebuildReset)
  autoRebuildReset = setTimeout(() => { autoRebuildArmed = false }, 100)
}

export function resetAutoRebuildForTests(): void {
  autoRebuildArmed = false
  clearTimeout(autoRebuildReset)
}
