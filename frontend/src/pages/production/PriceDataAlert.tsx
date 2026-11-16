import { Alert, Stack } from '@mantine/core'

import { useItemNameOptions } from '../../hooks/useStaticOptions'

export type PriceDataFlags = {
  adjusted_prices_available?: boolean
  cost_indices_available?: boolean
  prices_available?: boolean
  price_data_stale_hours?: number | null
  adjusted_prices_missing?: number[]
}

// Strict === false so an older cached plan without the new fields shows
// no warning (undefined is treated as fine).
const MAX_NAMES = 10

export function PriceDataAlert({ flags }: { flags?: PriceDataFlags | null }) {
  const names = useItemNameOptions()
  if (!flags) return null
  const missingIds = flags.adjusted_prices_missing ?? []
  const missingCount = missingIds.length
  const adjustedMissing = flags.adjusted_prices_available === false
  const indicesMissing = flags.cost_indices_available === false
  const pricesMissing = flags.prices_available === false
  const staleHours = flags.price_data_stale_hours
  const stale = staleHours != null && staleHours > 0
  if (!adjustedMissing && !indicesMissing && !pricesMissing && !stale && missingCount === 0) return null
  const nameById = new Map((names.data ?? []).map((o) => [o.type_id, o.type_name]))
  const shown = missingIds.slice(0, MAX_NAMES).map((id) => nameById.get(id) ?? `#${id}`)

  return (
    <Stack gap="xs">
      {pricesMissing && (
        <Alert color="danger" variant="light" title="Market quotes are unavailable">
          Home and hub market quotes could not be fetched for this computation - the ISK totals
          below do not add up. Recompute once market data is reachable again before trusting
          these numbers.
        </Alert>
      )}
      {adjustedMissing && (
        <Alert color="danger" variant="light" title="Job Cost figures are currently unavailable">
          ESI&apos;s adjusted-price data could not be fetched for this computation - every Job Cost/Unit
          below (and the Total Job Cost/Gesamt totals) reads 0 rather than its real value. Recompute once
          ESI is reachable again before trusting these numbers.
        </Alert>
      )}
      {indicesMissing && (
        <Alert color="danger" variant="light" title="System cost indices unavailable">
          Job Cost is using a flat rate instead of this structure&apos;s system cost index. Recompute once
          ESI industry indices are reachable again before trusting these numbers.
        </Alert>
      )}
      {!adjustedMissing && missingCount > 0 && (
        <Alert color="warn" variant="light" title="Job cost understated">
          ESI has no adjusted price for {missingCount} material{missingCount === 1 ? '' : 's'} used by
          these recipes ({shown.join(', ')}
          {missingCount > MAX_NAMES ? `, and ${missingCount - MAX_NAMES} more` : ''}), so their share of
          the Job Cost reads 0 and the job cost is understated.
        </Alert>
      )}
      {stale && (
        <Alert color="warn" variant="light" title="Using older ESI industry data">
          ESI is not reachable right now. Job Cost figures below use values from about{' '}
          {Math.round(staleHours!)} hour{Math.round(staleHours!) === 1 ? '' : 's'} ago.
        </Alert>
      )}
    </Stack>
  )
}
