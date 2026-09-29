import { notifications } from '@mantine/notifications'

import type { CharInfoSyncResult } from '../api/types'
import { useAction } from './useAction'

// Refresh button for a Character Management page. `in_flight` (another sync
// pass already running for an owner) is neither success nor failure
// (docs/CHARACTER_MANAGEMENT_PLAN.md R10) - it gets its own neutral notice.
export function useCharacterSync(
  label: string,
  sync: () => Promise<CharInfoSyncResult>,
  invalidateKeys: string[][],
  effect: string,
) {
  return useAction(
    label,
    async () => {
      const result = await sync()
      if (result.in_flight.length > 0) {
        notifications.show({
          title: 'Sync already running',
          message: `${result.in_flight.length} character(s) are already being synced by another run. Their data updates when it finishes.`,
          color: 'info',
        })
      }
      for (const f of result.failed) {
        notifications.show({
          title: `Sync failed for ${f.name ?? f.owner_id}`,
          message: f.error ?? 'Unknown error',
          color: 'danger',
        })
      }
      return { ok: result.ok, in_flight: result.in_flight.length, failed: result.failed.length }
    },
    invalidateKeys,
    { tier: 'live', effect },
  )
}
