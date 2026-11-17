import { useEffect, useRef } from 'react'
import { Button, Card, Group, Text } from '@mantine/core'

import type { RigSheetStatus } from '../../api/types'

// A stored sheet older than this is reloaded when the Logistics page opens.
export const RIG_SHEET_MAX_AGE_HOURS = 24
// A failed reload is not retried automatically again within this window.
const RETRY_AFTER_ERROR_HOURS = 1

// Whether opening the page should reload the sheet: a link is set and either
// the stored state is older than 24 h, or nothing was ever loaded and no fetch
// has failed yet (a failing link is only retried by the button).
export function rigSheetIsStale(status: RigSheetStatus | undefined, now: number = Date.now()): boolean {
  if (!status || !status.configured) return false
  const recentError = status.error_at != null && now - Date.parse(status.error_at) < RETRY_AFTER_ERROR_HOURS * 3_600_000
  if (recentError) return false
  if (status.fetched_at == null) return status.error_at == null
  const age = status.age_hours
  return age == null || age > RIG_SHEET_MAX_AGE_HOURS
}

function ageText(hours: number | null): string {
  if (hours == null) return 'never'
  if (hours < 1) return 'less than an hour ago'
  if (hours < 48) return `${Math.round(hours)} h ago`
  return `${Math.round(hours / 24)} days ago`
}

// State of the rig sheet source on the Logistics page: when the stored copy
// was fetched, the last error and a reload button. The planners only read the
// stored copy; this card is the only thing that triggers a fetch.
export function RigSheetCard({
  status, reloading, onReload,
}: {
  status: RigSheetStatus | undefined
  reloading: boolean
  onReload: () => void
}) {
  const started = useRef(false)
  const stale = rigSheetIsStale(status)
  useEffect(() => {
    if (!stale || started.current) return
    started.current = true
    onReload()
  }, [stale, onReload])

  if (!status?.configured) return null
  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <div>
          <Text fw={600} size="sm">Rig sheet</Text>
          <Text size="xs" c="dimmed">
            {status.fetched_at
              ? `${status.system_count} systems, loaded ${ageText(status.age_hours)}`
              : 'Not loaded yet'}
            . Used for a category whose structure&apos;s system is listed (source &quot;from rig sheet&quot;).
          </Text>
          {status.duplicates.length > 0 && (
            <Text size="xs" c="dimmed">Listed more than once (first row used): {status.duplicates.join(', ')}</Text>
          )}
          {status.error && (
            <Text size="xs" c="danger">
              Last reload failed: {status.error}
              {status.fetched_at ? ' The previous copy is still in use.' : ''}
            </Text>
          )}
        </div>
        <Button size="xs" variant="default" loading={reloading} onClick={onReload}>Reload rig sheet</Button>
      </Group>
    </Card>
  )
}
