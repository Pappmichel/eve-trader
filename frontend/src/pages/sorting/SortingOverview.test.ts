import { describe, expect, it } from 'vitest'

import type { SortingRow } from '../../api/types'
import { filterSortingRows } from './sortingFilters'

function row(partial: Partial<SortingRow> & Pick<SortingRow, 'type_id' | 'type_name'>): SortingRow {
  return {
    intake_qty: 1,
    by_source: [],
    wanted_by_tool: [],
    unclaimed: true,
    ...partial,
  }
}

describe('filterSortingRows', () => {
  const mexallon = row({
    type_id: 36, type_name: 'Mexallon', intake_qty: 500,
    by_source: [{ source_label: 'Intake', qty: 500 }],
    wanted_by_tool: [{ tool: 'material', wanted_qty: 21100000 }],
    unclaimed: false,
  })
  const trit = row({
    type_id: 34, type_name: 'Tritanium', intake_qty: 100,
    by_source: [{ source_label: 'Alice (Hangar)', qty: 100 }],
    wanted_by_tool: [],
    unclaimed: true,
  })
  const plex = row({
    type_id: 1, type_name: 'PLEX', intake_qty: 10,
    by_source: [{ source_label: 'Intake', qty: 10 }],
    wanted_by_tool: [{ tool: 'trading', wanted_qty: 4 }, { tool: 'doctrine', wanted_qty: 2 }],
    unclaimed: false,
  })
  const rows = [mexallon, trit, plex]

  it('returns every row when no filters are selected', () => {
    expect(filterSortingRows(rows, [], [])).toEqual(rows)
  })

  it('keeps rows wanted by the selected tool', () => {
    expect(filterSortingRows(rows, ['material'], []).map((r) => r.type_id)).toEqual([36])
  })

  it('keeps unclaimed rows when Nobody is selected', () => {
    expect(filterSortingRows(rows, ['unclaimed'], []).map((r) => r.type_id)).toEqual([34])
  })

  it('keeps a row if any of its sources match', () => {
    expect(filterSortingRows(rows, [], ['Intake']).map((r) => r.type_id)).toEqual([36, 1])
  })

  it('combines tool and source filters', () => {
    expect(filterSortingRows(rows, ['trading'], ['Intake']).map((r) => r.type_id)).toEqual([1])
  })
})
