import type { SortingRow } from '../../api/types'

export const UNCLAIMED = 'unclaimed'

export function filterSortingRows(rows: SortingRow[], selTools: string[], selSources: string[]): SortingRow[] {
  return rows.filter((row) => {
    if (selTools.length > 0) {
      const tools = row.unclaimed ? [UNCLAIMED] : row.wanted_by_tool.map((w) => w.tool)
      if (!selTools.some((t) => tools.includes(t))) return false
    }
    if (selSources.length > 0) {
      if (!row.by_source.some((s) => selSources.includes(s.source_label))) return false
    }
    return true
  })
}
