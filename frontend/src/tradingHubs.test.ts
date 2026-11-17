import { describe, expect, it } from 'vitest'

import { TEST_HUBS } from './testHubs'
import { ALL_HUBS, hubLabel, hubSelectData } from './tradingHubs'

describe('hubLabel', () => {
  it('uses the hub name, "Best hub" for All hubs and "Region n" for unknown or unloaded', () => {
    expect(hubLabel(TEST_HUBS, 10000043)).toBe('Amarr')
    expect(hubLabel(TEST_HUBS, ALL_HUBS)).toBe('Best hub')
    expect(hubLabel(TEST_HUBS, 10000016)).toBe('Region 10000016')
    expect(hubLabel(undefined, 10000002)).toBe('Region 10000002')
  })
})

describe('hubSelectData', () => {
  it('lists the loaded hubs, optionally All hubs, and keeps a stored custom region', () => {
    expect(hubSelectData(TEST_HUBS, 10000002).map((o) => o.label)).toEqual(
      ['Jita (The Forge)', 'Amarr (Domain)', 'Dodixie (Sinq Laison)', 'Rens (Heimatar)'])
    expect(hubSelectData(TEST_HUBS, ALL_HUBS, true).at(-1)).toEqual({ value: '0', label: 'All hubs (best per item)' })
    expect(hubSelectData(TEST_HUBS, 10000016).at(-1)).toEqual({ value: '10000016', label: 'Custom (region 10000016)' })
  })

  it('shows only the stored value while the list loads', () => {
    expect(hubSelectData(undefined, 10000016)).toEqual([{ value: '10000016', label: 'Region 10000016' }])
    expect(hubSelectData(undefined, 10000002, true)).toEqual([
      { value: '0', label: 'All hubs (best per item)' }, { value: '10000002', label: 'Region 10000002' }])
  })
})
