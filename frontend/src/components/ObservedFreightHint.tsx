import { useQuery } from '@tanstack/react-query'
import { Text } from '@mantine/core'

import { hubsApi } from '../api/client'
import { ALL_HUBS } from '../tradingHubs'
import { ObservedRateHint } from './ObservedRateHint'

const isk = (v: number) => `${Math.round(v).toLocaleString('en-US')} ISK/m³`

// The server rounds `suggested` to whole ISK: a field holding the same figure
// offers no button.
const sameRate = (a: number, b: number) => Math.abs(a - b) < 0.5

// What finished courier contracts say the freight from a hub to the home
// structure actually costs, shown under a freight setting. The tools keep five
// separate freight fields on purpose, so each page passes its own hub and
// `current`; the click goes through that page's normal save (`onApply`).
// `compact` drops the explanation (used per row of the hub freight table).
export function ObservedFreightHint({ regionId, current, onApply, applying, compact = false }: {
  regionId: number | undefined | null
  current: number
  onApply: (value: number) => void
  applying?: boolean
  compact?: boolean
}) {
  const { data } = useQuery({
    queryKey: ['hubs', 'observed-freight'], queryFn: hubsApi.observedFreight, staleTime: 60_000,
  })
  if (regionId === ALL_HUBS) {
    return (
      <Text size="xs" c="dimmed" data-testid="observed-freight">
        All hubs: the actual rate of each hub is shown in the freight table below.
      </Text>
    )
  }
  const row = data?.find((r) => r.region_id === regionId)
  if (!row) return null
  return (
    <ObservedRateHint
      testId="observed-freight"
      label={`Actual last ${row.window_days} days`}
      value={row.suggested}
      current={current}
      format={isk}
      same={sameRate}
      sample={`${row.shipments} shipments`}
      detail={compact ? undefined
        : `Reward / volume of finished courier contracts from ${row.hub} to your home structure. `
          + `In a shared corporation's contracts this includes other members' shipments.`}
      emptyText={`${row.shipments} of ${row.min_shipments} shipments needed`}
      emptyMessage={row.message}
      onApply={onApply}
      applying={applying}
    />
  )
}
