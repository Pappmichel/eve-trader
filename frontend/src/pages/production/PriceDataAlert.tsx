import { Alert, Stack } from '@mantine/core'

export type PriceDataFlags = {
  adjusted_prices_available?: boolean
  cost_indices_available?: boolean
  prices_available?: boolean
  price_data_stale_hours?: number | null
}

// Strict === false so an older cached plan without the new fields shows
// no warning (undefined is treated as fine).
export function PriceDataAlert({ flags }: { flags?: PriceDataFlags | null }) {
  if (!flags) return null
  const adjustedMissing = flags.adjusted_prices_available === false
  const indicesMissing = flags.cost_indices_available === false
  const pricesMissing = flags.prices_available === false
  const staleHours = flags.price_data_stale_hours
  const stale = staleHours != null && staleHours > 0
  if (!adjustedMissing && !indicesMissing && !pricesMissing && !stale) return null

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
      {stale && (
        <Alert color="warn" variant="light" title="Using older ESI industry data">
          ESI is not reachable right now. Job Cost figures below use values from about{' '}
          {Math.round(staleHours!)} hour{Math.round(staleHours!) === 1 ? '' : 's'} ago.
        </Alert>
      )}
    </Stack>
  )
}
