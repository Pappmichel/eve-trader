import { Stack, Text } from '@mantine/core'

import { DetailRow } from '../../components/RowDetailDrawer'
import { isk, qty } from '../../format'

function signedIsk(value: number): string {
  return value > 0 ? `+${isk(value)}` : isk(value)
}

interface SellSideRow {
  home_sell_price?: number | null
  profit_per_unit?: number | null
  quantity: number
  recipe_source: string | null
  job_cost?: number | null
}

// "If sold at home" section of the Build List row drawers (both lists).
// Production sells only at home, so there is no Jita figure here. An alchemy
// row's quantity counts the Unrefined intermediate, not the item the profit is
// for, so it shows no total.
export function SellSideDetail({ row, showJobCost = false }: { row: SellSideRow; showJobCost?: boolean }) {
  const profit = row.profit_per_unit ?? null
  const profitColor = profit == null ? undefined : profit > 0 ? 'accent' : 'danger'
  const profitValue = (v: number) => <Text span size="sm" ff="monospace" c={profitColor}>{signedIsk(v)}</Text>
  return (
    <Stack gap={6}>
      <Text size="sm" fw={600}>If sold at home</Text>
      <DetailRow label="Home sell price" value={isk(row.home_sell_price)} muted={row.home_sell_price == null} />
      <DetailRow label="Profit / unit (after fees)" value={profit == null ? '–' : profitValue(profit)} muted={profit == null} />
      {row.recipe_source !== 'alchemy' && (
        <DetailRow
          label={`Profit (${qty(row.quantity)} units)`}
          value={profit == null ? '–' : profitValue(profit * row.quantity)}
          muted={profit == null}
        />
      )}
      {showJobCost && <DetailRow label="Job fee / unit" value={isk(row.job_cost)} muted={row.job_cost == null} />}
    </Stack>
  )
}
