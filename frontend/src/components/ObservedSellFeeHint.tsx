import type { ObservedSellFees } from '../api/types'
import { ObservedRateHint } from './ObservedRateHint'

const pct = (v: number) => `${(v * 100).toFixed(2)} %`

// Same rounding the server uses for `suggested` (4 decimals): a field that
// already holds the value does not offer to "apply" it again.
const sameValue = (a: number, b: number) => Math.abs(a - b) < 0.00005

// The wallet's actual rate, shown under a sell-fee setting. A corporation
// wallet mixes other members' sales into the figure, which is why it is never
// automatic (see ObservedRateHint for the click behaviour).
export function ObservedSellFeeHint({ fees, current, onApply, applying }: {
  fees: ObservedSellFees | undefined
  current: number
  onApply: (value: number) => void
  applying?: boolean
}) {
  if (!fees) return null
  const hasValue = fees.suggested != null && fees.quote != null
  const keptShare = fees.setting === 'structure_sell_haircut'
  return (
    <ObservedRateHint
      testId="observed-sell-fees"
      label={`Actual last ${fees.window_days} days`}
      value={hasValue ? fees.suggested : null}
      current={current}
      format={pct}
      same={sameValue}
      sample={`${fees.sales} sales`}
      headlineExtra={keptShare && fees.quote != null ? ` - fees ${pct(fees.quote)}` : ''}
      detail={`Sales tax ${pct(fees.sales_tax_rate ?? 0)} + broker fee ${pct(fees.broker_fee_rate ?? 0)} of gross. `
        + `In a shared corporation wallet this includes other members' sales. ${fees.relist_note}`}
      emptyText={`${fees.sales} of ${fees.min_sales} sales needed`}
      emptyMessage={fees.message}
      onApply={onApply}
      applying={applying}
    />
  )
}
