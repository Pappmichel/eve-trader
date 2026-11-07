import { Button, Group, Stack, Text } from '@mantine/core'

import type { ObservedSellFees } from '../api/types'

const pct = (v: number) => `${(v * 100).toFixed(2)} %`

// Same rounding the server uses for `suggested` (4 decimals): a field that
// already holds the value does not offer to "apply" it again.
const sameValue = (a: number, b: number) => Math.abs(a - b) < 0.00005

// The wallet's actual rate, shown under a sell-fee setting with a button that
// copies it into that setting. Nothing here changes a setting on its own: the
// click goes through the page's normal save (`onApply`), so validation and
// the cached-plan invalidation stay in one place. A corporation wallet mixes
// other members' sales into the figure, which is why it is never automatic.
export function ObservedSellFeeHint({ fees, current, onApply, applying }: {
  fees: ObservedSellFees | undefined
  current: number
  onApply: (value: number) => void
  applying?: boolean
}) {
  if (!fees) return null
  const label = `Actual last ${fees.window_days} days`
  if (fees.suggested == null || fees.quote == null) {
    return (
      <Stack gap={2} data-testid="observed-sell-fees">
        <Text size="xs" c="dimmed">
          {label}: no value ({fees.sales} of {fees.min_sales} sales needed). {fees.message}
        </Text>
      </Stack>
    )
  }
  const keptShare = fees.setting === 'structure_sell_haircut'
  return (
    <Stack gap={2} data-testid="observed-sell-fees">
      <Group gap="xs" wrap="nowrap">
        <Text size="xs">
          {label}: {pct(fees.suggested)} ({fees.sales} sales)
          {keptShare ? ` - fees ${pct(fees.quote)}` : ''}
        </Text>
        <Button size="compact-xs" variant="light" loading={applying}
          disabled={sameValue(fees.suggested, current)}
          onClick={() => onApply(fees.suggested as number)}>
          Use this value
        </Button>
      </Group>
      <Text size="xs" c="dimmed">
        Sales tax {pct(fees.sales_tax_rate ?? 0)} + broker fee {pct(fees.broker_fee_rate ?? 0)} of gross.
        In a shared corporation wallet this includes other members&apos; sales. {fees.relist_note}
      </Text>
    </Stack>
  )
}
