import { describe, expect, it } from 'vitest'

import type { RealizedDay } from '../../../api/types'
import { bucketWeekly, compactIsk, profitSeries } from './chart'

function day(date: string, profit: number): RealizedDay {
  return { date, revenue: 0, cost: 0, profit }
}

describe('profitSeries', () => {
  it('keeps daily bars and a running total of the API profit', () => {
    const points = profitSeries([
      day('2026-10-01', 10),
      day('2026-10-03', 5),
      day('2026-10-02', -2),
    ], false)
    expect(points.map((row) => row.date)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03'])
    expect(points.map((row) => row.profit)).toEqual([10, -2, 5])
    expect(points.map((row) => row.cumulative)).toEqual([10, 8, 13])
  })

  it('rolls days into Monday weeks without inventing profit', () => {
    // 2026-10-05 is a Monday. 2026-10-04 is the previous Sunday.
    const weeks = bucketWeekly([
      day('2026-10-04', 4),
      day('2026-10-05', 10),
      day('2026-10-06', 1),
    ])
    expect(weeks).toEqual([
      { date: '2026-09-28', profit: 4 },
      { date: '2026-10-05', profit: 11 },
    ])
    const series = profitSeries([
      day('2026-10-05', 10),
      day('2026-10-06', 1),
    ], true)
    expect(series).toEqual([{ date: '2026-10-05', profit: 11, cumulative: 11 }])
  })
})

describe('compactIsk', () => {
  it('shortens axis labels and keeps the sign', () => {
    expect(compactIsk(4_500_000_000)).toBe('4.5B')
    expect(compactIsk(-320_000_000)).toBe('-320M')
    expect(compactIsk(1_000_000)).toBe('1M')
    expect(compactIsk(12_000)).toBe('12k')
    expect(compactIsk(950)).toBe('950')
    expect(compactIsk(0)).toBe('0')
  })
})
