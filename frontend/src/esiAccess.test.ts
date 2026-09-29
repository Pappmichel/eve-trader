import { describe, expect, it } from 'vitest'

import {
  buildToolView,
  characterRemovalImpact,
  corporationIdsFrom,
  deriveCellState,
  extraKindsForCharacter,
  pendingKeysFromPreview,
  sharedToolsFor,
} from './esiAccess'
import { kindByKey } from './esiRegistry'
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
    expect(cell.capableCount).toBe(5)
  })

  it('is all when every consuming tool is shared', () => {
    const allAssets: EsiSharingRow[] = [
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'production' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'doctrine' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'sorting' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'trading' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'portfolio' },
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

describe('merged Wallet row (2026-09-27: Wallet Balance folded into Wallet)', () => {
  // Trading's own wallet sync and Portfolio's wallet-balance sync are still
  // two separate data_kind rows server-side (different ESI fetch, same
  // scope) - only the UI presentation merged into one row/cell, via
  // esiRegistry.ts's toolDataKind override on 'wallet'.
  const walletSharing: EsiSharingRow[] = [
    { owner_type: 'character', owner_id: 5, data_kind: 'wallet', tool_key: 'trading' },
    { owner_type: 'character', owner_id: 5, data_kind: 'wallet_balance', tool_key: 'portfolio' },
    // Character Management phase 1: Character Info reads the same balance
    // snapshot Portfolio does, under its own opt-in row.
    { owner_type: 'character', owner_id: 5, data_kind: 'wallet_balance', tool_key: 'char_info' },
  ]

  it('resolves the Portfolio and Character Info toggles to wallet_balance, not wallet', () => {
    const kind = kindByKey('wallet')!
    const shared = sharedToolsFor(walletSharing, 'character', 5, kind)
    expect(shared.sort()).toEqual(['char_info', 'portfolio', 'trading'])
  })

  it('reports the merged cell as fully shared once both underlying rows exist', () => {
    const cell = deriveCellState({
      ownerType: 'character', ownerId: 5, dataKind: 'wallet',
      sharing: walletSharing, freshness: [], pendingKinds: new Set(),
    })
    expect(cell.kind).toBe('all')
    expect(cell.sharedCount).toBe(3)
    expect(cell.capableCount).toBe(3)
  })

  it('does not count a wallet_balance row as shared under a different owner', () => {
    const kind = kindByKey('wallet')!
    const shared = sharedToolsFor(walletSharing, 'character', 999, kind)
    expect(shared).toEqual([])
  })

  it('surfaces a wallet_balance-only fetch failure on the merged cell', () => {
    const freshnessWithBalanceError: EsiFreshnessRow[] = [
      {
        owner_type: 'character', owner_id: 5, data_kind: 'wallet_balance',
        last_success_at: null, last_attempt_at: 't', last_error: 'Market access denied',
      },
    ]
    const cell = deriveCellState({
      ownerType: 'character', ownerId: 5, dataKind: 'wallet',
      sharing: walletSharing, freshness: freshnessWithBalanceError, pendingKinds: new Set(),
    })
    expect(cell.kind).toBe('error')
    expect(cell.lastError).toBe('Market access denied')
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

    it('summarizes the tools and access capabilities a character currently holds', () => {
    expect(characterRemovalImpact(1, sharing, capabilities)).toEqual({
      tools: ['production', 'doctrine'],
      capabilityKeys: ['structure_market_book'],
    })
    // Corporation sharing is not this character's; a missing id is empty.
    expect(characterRemovalImpact(99, sharing, capabilities)).toEqual({
      tools: [],
      capabilityKeys: [],
    })
  })
})
