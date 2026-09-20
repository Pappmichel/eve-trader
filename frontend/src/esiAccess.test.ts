import { describe, expect, it } from 'vitest'

import {
  buildToolView,
  corporationIdsFrom,
  deriveCellState,
  extraKindsForCharacter,
  pendingKeysFromPreview,
} from './esiAccess'
import type { EsiCapabilityRow, EsiFreshnessRow, EsiSharingRow } from './api/types'

const sharing: EsiSharingRow[] = [
  { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'production' },
  { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'doctrine' },
  { owner_type: 'corporation', owner_id: 99, data_kind: 'wallet', tool_key: 'trading' },
]

const freshness: EsiFreshnessRow[] = [
  {
    owner_type: 'character', owner_id: 1, data_kind: 'assets',
    last_success_at: null, last_attempt_at: 't', last_error: 'ESI 500',
  },
]

const capabilities: EsiCapabilityRow[] = [
  { character_id: 1, capability_key: 'structure_market_book' },
]

describe('deriveCellState', () => {
  it('is not_shared when nothing is ticked', () => {
    const cell = deriveCellState({
      ownerType: 'character', ownerId: 1, dataKind: 'skills',
      sharing, freshness: [], pendingKinds: new Set(),
    })
    expect(cell.kind).toBe('not_shared')
    expect(cell.sharedCount).toBe(0)
    expect(cell.capableCount).toBe(2)
  })

  it('uses a 2/4 badge when some consuming tools are shared', () => {
    const cell = deriveCellState({
      ownerType: 'character', ownerId: 1, dataKind: 'assets',
      sharing, freshness: [], pendingKinds: new Set(),
    })
    expect(cell.kind).toBe('some')
    expect(cell.sharedCount).toBe(2)
    expect(cell.capableCount).toBe(4)
  })

  it('is all when every consuming tool is shared', () => {
    const allAssets: EsiSharingRow[] = [
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'production' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'doctrine' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'sorting' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'trading' },
    ]
    const cell = deriveCellState({
      ownerType: 'character', ownerId: 1, dataKind: 'assets',
      sharing: allAssets, freshness: [], pendingKinds: new Set(),
    })
    expect(cell.kind).toBe('all')
  })

  it('prefers error over the some/all badge while a fetch is failing', () => {
    const cell = deriveCellState({
      ownerType: 'character', ownerId: 1, dataKind: 'assets',
      sharing, freshness, pendingKinds: new Set(),
    })
    expect(cell.kind).toBe('error')
    expect(cell.lastError).toBe('ESI 500')
  })

  it('marks a ticked kind pending when its scopes are not on any token', () => {
    const cell = deriveCellState({
      ownerType: 'character', ownerId: 1, dataKind: 'assets',
      sharing, freshness: [], pendingKinds: new Set(['assets']),
    })
    expect(cell.kind).toBe('pending')
  })
})

describe('tool view / extra kinds', () => {
  it('flags Sorting as having no Assets source after a conservative share', () => {
    const view = buildToolView(sharing, (type, id) => `${type}:${id}`)
    const sorting = view.find((row) => row.toolKey === 'sorting')
    expect(sorting?.missing.map((m) => m.kindKey)).toContain('assets')
    const production = view.find((row) => row.toolKey === 'production')
    expect(production?.receives.find((r) => r.kindKey === 'assets')?.owners).toEqual([
      { ownerType: 'character', ownerId: 1, label: 'character:1' },
    ])
  })

  it('collects ticked kinds and capabilities for a re-auth extra_kinds list', () => {
    expect(extraKindsForCharacter(1, sharing, capabilities).sort()).toEqual([
      'assets', 'structure_market_book',
    ])
  })

  it('reads pending keys from the access-preview added flags', () => {
    const pending = pendingKeysFromPreview({
      title: 'Confirm ESI access',
      items: [
        { key: 'assets', label: 'Assets', group: 1, added: false },
        { key: 'wallet', label: 'Wallet', group: 1, added: true },
      ],
    })
    expect([...pending]).toEqual(['wallet'])
  })

  it('lists corporation ids from sharing and freshness, not from a frontend count of prefixes', () => {
    expect(corporationIdsFrom(sharing, [])).toEqual([99])
  })
})
