import { describe, expect, it } from 'vitest'

import type { RealizedTrade } from '../../api/types'
import { aggregateByItem } from './RealizedTrades'

function trade(partial: Partial<RealizedTrade>): RealizedTrade {
  return {
    type_id: 34, item: 'Tritanium', buy_date: '', buy_qty: 0, buy_unit_price: 0,
    sell_date: '', sell_qty: 0, sell_unit_price: 0, matched_qty: 0, realized_profit: 0, margin: 0,
    ...partial,
  }
}

describe('aggregateByItem', () => {
  it('quantity-weights avgBuyPrice/avgSellPrice instead of averaging per-trade prices', () => {
    // T3-07 (business-logic audit follow-up, 2026-09-26): a tiny 1-unit
    // trade at a wildly different price must not pull the average as hard
    // as a 10,000-unit trade at the item's normal price.
    const rows = aggregateByItem([
      trade({ matched_qty: 10_000, buy_unit_price: 5.0, sell_unit_price: 6.0, realized_profit: 9_000.0 }),
      trade({ matched_qty: 1, buy_unit_price: 500.0, sell_unit_price: 600.0, realized_profit: 100.0 }),
    ])

    expect(rows).toHaveLength(1)
    // weighted: (10000*5 + 1*500) / 10001 = 5.0495...
    expect(rows[0].avgBuyPrice).toBeCloseTo((10_000 * 5.0 + 1 * 500.0) / 10_001, 5)
    // a plain (unweighted) mean would have given (5.0 + 500.0) / 2 = 252.5 - very different
    expect(rows[0].avgBuyPrice).toBeLessThan(10)
  })

  it('derives avgMargin from weighted totals (totalProfit / total buy cost), not a ratio average', () => {
    const rows = aggregateByItem([
      trade({ matched_qty: 10_000, buy_unit_price: 5.0, realized_profit: 9_000.0 }),
      trade({ matched_qty: 1, buy_unit_price: 500.0, realized_profit: 100.0 }),
    ])

    const totalBuyCost = 10_000 * 5.0 + 1 * 500.0
    const totalProfit = 9_000.0 + 100.0
    expect(rows[0].avgMargin).toBeCloseTo(totalProfit / totalBuyCost, 6)
  })

  it('sums matchedQty/totalProfit and groups by type_id', () => {
    const rows = aggregateByItem([
      trade({ type_id: 34, matched_qty: 100, realized_profit: 10.0 }),
      trade({ type_id: 34, matched_qty: 200, realized_profit: 20.0 }),
      trade({ type_id: 35, item: 'Pyerite', matched_qty: 50, realized_profit: 5.0 }),
    ])

    expect(rows).toHaveLength(2)
    const tritanium = rows.find((r) => r.type_id === 34)!
    expect(tritanium.trades).toBe(2)
    expect(tritanium.matchedQty).toBe(300)
    expect(tritanium.totalProfit).toBeCloseTo(30.0)
  })

  it('sorts rows by totalProfit descending', () => {
    const rows = aggregateByItem([
      trade({ type_id: 1, item: 'A', realized_profit: 5.0 }),
      trade({ type_id: 2, item: 'B', realized_profit: 50.0 }),
      trade({ type_id: 3, item: 'C', realized_profit: 20.0 }),
    ])

    expect(rows.map((r) => r.item)).toEqual(['B', 'C', 'A'])
  })

  it('returns an empty list for no trades', () => {
    expect(aggregateByItem([])).toEqual([])
  })
})
