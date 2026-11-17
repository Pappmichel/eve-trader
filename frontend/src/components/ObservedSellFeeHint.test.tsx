import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it, vi } from 'vitest'

import type { ObservedSellFees } from '../api/types'
import { ObservedSellFeeHint } from './ObservedSellFeeHint'

const FEES: ObservedSellFees = {
  setting: 'market_fees', window_days: 30, min_sales: 20, sales: 25, quote: 0.0537, suggested: 0.0537,
  sales_tax_rate: 0.036, broker_fee_rate: 0.0177, upper_bound_quote: 0.0737, upper_bound_value: 0.0737,
  order_fees_without_sale: 500, reason: null, message: null,
  relist_note: 'Relist and order-modification fees are included only as far as they are attributed to a sale.',
}

function renderHint(fees: ObservedSellFees | undefined, current: number, onApply = vi.fn()) {
  render(
    <MantineProvider>
      <ObservedSellFeeHint fees={fees} current={current} onApply={onApply} />
    </MantineProvider>,
  )
  return onApply
}

describe('ObservedSellFeeHint', () => {
  it('shows the actual rate with its sale count and the relist caveat', () => {
    renderHint(FEES, 0.04)
    expect(screen.getByText(/Actual last 30 days: 5\.37 % \(25 sales\)/)).toBeInTheDocument()
    expect(screen.getByText(/Sales tax 3\.60 % \+ broker fee 1\.77 %/)).toBeInTheDocument()
    expect(screen.getByText(/Relist and order-modification fees/)).toBeInTheDocument()
  })

  it('applies the suggested value only on click', async () => {
    const onApply = renderHint(FEES, 0.04)
    expect(onApply).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Use this value' }))
    expect(onApply).toHaveBeenCalledExactlyOnceWith(0.0537)
  })

  it('does not offer a value the field already holds', () => {
    renderHint(FEES, 0.0537)
    expect(screen.getByRole('button', { name: 'Use this value' })).toBeDisabled()
  })

  it('shows the kept share for Trading and the fee beside it', () => {
    renderHint({ ...FEES, setting: 'structure_sell_haircut', suggested: 0.9463 }, 0.9)
    expect(screen.getByText(/Actual last 30 days: 94\.63 % \(25 sales\) - fees 5\.37 %/)).toBeInTheDocument()
  })

  it('shows the upper bound as a second line without its own button', () => {
    renderHint(FEES, 0.04)
    expect(screen.getByTestId('observed-sell-fees-secondary')).toHaveTextContent(
      /Upper bound 7\.37 %: includes order fees of days without a sale \(relists, unsold new orders, buy orders\)/)
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })

  it('shows the Trading bound as the kept share with the fee beside it', () => {
    renderHint({ ...FEES, setting: 'structure_sell_haircut', suggested: 0.9463, upper_bound_value: 0.9263 }, 0.9)
    expect(screen.getByTestId('observed-sell-fees-secondary')).toHaveTextContent(
      /Lower bound 92\.63 % - fees up to 7\.37 %: includes order fees of days without a sale/)
  })

  it('omits the second line when the upper bound has no value', () => {
    renderHint({ ...FEES, upper_bound_quote: null, upper_bound_value: null }, 0.04)
    expect(screen.queryByTestId('observed-sell-fees-secondary')).not.toBeInTheDocument()
  })

  it('shows the reason and no button when there is no value', () => {
    renderHint({
      ...FEES, sales: 7, quote: null, suggested: null, sales_tax_rate: null, broker_fee_rate: null,
      upper_bound_quote: null, upper_bound_value: null, order_fees_without_sale: null,
      reason: 'too_few_sales', message: 'Fewer than 20 market sales with a known sales tax in the last 30 days.',
    }, 0.04)
    expect(screen.getByText(/no value \(7 of 20 sales needed\)/)).toBeInTheDocument()
    expect(screen.getByText(/Fewer than 20 market sales/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use this value' })).not.toBeInTheDocument()
  })

  it('renders nothing while the figure is loading', () => {
    renderHint(undefined, 0.04)
    expect(screen.queryByTestId('observed-sell-fees')).not.toBeInTheDocument()
  })
})
