import { describe, expect, it } from 'vitest'
import { changedFields } from './changedFields'

describe('changedFields', () => {
  const loaded = { a: 1, b: 'x', list: [1, 2], nullable: null as number | null }

  it('returns nothing when the form is unchanged', () => {
    expect(changedFields(loaded, { ...loaded, list: [1, 2] })).toEqual({})
  })

  it('returns only the keys that differ, including arrays and null', () => {
    expect(changedFields(loaded, { a: 2, b: 'x', list: [1, 2, 3], nullable: 5 }))
      .toEqual({ a: 2, list: [1, 2, 3], nullable: 5 })
    expect(changedFields({ ...loaded, nullable: 7 }, { ...loaded, nullable: null })).toEqual({ nullable: null })
  })

  it('returns every key when there is no loaded reference', () => {
    expect(changedFields(undefined, loaded)).toEqual(loaded)
  })
})
