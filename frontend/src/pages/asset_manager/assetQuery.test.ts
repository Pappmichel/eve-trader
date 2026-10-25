import { describe, expect, it } from 'vitest'
import { assetQueryString } from '../../api/client'
import { DEFAULT_QUERY, filterPart, paramsFromQuery, queryFromParams } from './assetQuery'

describe('asset manager URL state', () => {
  it('round-trips filters and drops defaults', () => {
    const q = { ...DEFAULT_QUERY, q: 'trit', owners: ['character:1', 'corporation:2'], type_ids: [34], stock_only: true, page: 3 }
    const params = paramsFromQuery(q)
    expect(params.has('group_by')).toBe(false)
    expect(params.getAll('owners')).toEqual(['character:1', 'corporation:2'])
    expect(queryFromParams(params)).toEqual(q)
  })

  it('ignores an unknown grouping from a hand-edited URL', () => {
    expect(queryFromParams(new URLSearchParams('group_by=bogus')).group_by).toBe('type_location')
  })

  it('leaves the page out of saved views and repeats list params for the API', () => {
    expect(filterPart({ ...DEFAULT_QUERY, page: 4 }).page).toBeUndefined()
    expect(assetQueryString({ owners: ['a', 'b'], q: '', location_id: null, desc: false }))
      .toBe('owners=a&owners=b&desc=false')
  })
})
