export interface CompareRow {
  date: string
  [key: string]: number | string | null
}

// One line per item, indexed to 100 at the first date every item has a price
// for; the real price rides along as `p<id>` for the tooltip. Dates before
// that common date are dropped (no base to index against).
export function normalizeCompare(
  items: { id: string; points: { date: string; avg_price: number }[] }[],
): CompareRow[] {
  if (items.length === 0 || items.some((i) => i.points.length === 0)) return []
  const maps = items.map((i) => new Map(i.points.map((p) => [p.date, p.avg_price])))
  const dates = [...new Set(items.flatMap((i) => i.points.map((p) => p.date)))].sort()
  const base = dates.find((d) => maps.every((m) => (m.get(d) ?? 0) > 0))
  if (!base) return []
  const baseVals = maps.map((m) => m.get(base) as number)
  return dates.filter((d) => d >= base).map((date) => {
    const row: CompareRow = { date }
    items.forEach((it, i) => {
      const v = maps[i].get(date)
      row[`i${it.id}`] = v == null ? null : (v / baseVals[i]) * 100
      row[`p${it.id}`] = v ?? null
    })
    return row
  })
}
