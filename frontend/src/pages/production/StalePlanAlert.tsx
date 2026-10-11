import { Alert } from '@mantine/core'

// Shown on a cached Build / Buy / Asset-Optimized list once an integrated
// special order changed after it was computed (the backend's `stale` flag,
// SPECIAL_ORDERS_INTEGRATION_PLAN.md D9). The list stays usable.
export function StalePlanAlert({ stale }: { stale?: boolean }) {
  if (!stale) return null
  return (
    <Alert color="warn" variant="light" title="Special orders changed">
      A special order included in the build lists changed since this list was computed. Recompute to apply it.
    </Alert>
  )
}
