import type { RealizedDay } from '../../../api/types'

export interface ProfitPoint {
  date: string
  profit: number
  cumulative: number
}

function weekStart(isoDate: string): string {
  const day = isoDate.slice(0, 10)
  const parsed = new Date(`${day}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return day
  const delta = (parsed.getUTCDay() + 6) % 7
  parsed.setUTCDate(parsed.getUTCDate() - delta)
  return parsed.toISOString().slice(0, 10)
}

/** Group the API's daily profit into Monday-start weeks. Sums are the API's own daily figures. */
export function bucketWeekly(days: RealizedDay[]): { date: string; profit: number }[] {
  const buckets = new Map<string, number>()
  for (const row of days) {
    const key = weekStart(row.date)
    buckets.set(key, (buckets.get(key) ?? 0) + row.profit)
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, profit]) => ({ date, profit }))
}

export function profitSeries(days: RealizedDay[], weekly: boolean): ProfitPoint[] {
  const rows = weekly
    ? bucketWeekly(days)
    : [...days]
      .map((row) => ({ date: row.date.slice(0, 10), profit: row.profit }))
      .sort((a, b) => a.date.localeCompare(b.date))
  let running = 0
  return rows.map((row) => {
    running += row.profit
    return { date: row.date, profit: row.profit, cumulative: running }
  })
}
