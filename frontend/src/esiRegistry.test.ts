import { describe, expect, it } from 'vitest'

import { CHARACTER_KINDS, KIND_SECTIONS, OWNED_DATA_KINDS, kindByKey, toolLabel } from './esiRegistry'

describe('esiRegistry Character Management kinds', () => {
  it('mirrors the backend: five char_info kinds, three of them live-only', () => {
    for (const key of ['standings', 'loyalty', 'location', 'ship', 'online']) {
      const kind = kindByKey(key)!
      expect(kind.consumingTools).toEqual(['char_info'])
      expect(kind.group).toBe(2)
    }
    expect(OWNED_DATA_KINDS.filter((k) => k.liveOnly).map((k) => k.key).sort())
      .toEqual(['location', 'online', 'ship'])
  })

  it('lets Character Info read the wallet balance through the merged Wallet row', () => {
    const wallet = kindByKey('wallet')!
    expect(wallet.consumingTools).toContain('char_info')
    expect(wallet.toolDataKind?.char_info).toBe('wallet_balance')
  })

  it('puts every kind into exactly one known section', () => {
    const sections = new Set(KIND_SECTIONS.map((s) => s.key))
    for (const kind of CHARACTER_KINDS) expect(sections.has(kind.section)).toBe(true)
    const covered = KIND_SECTIONS.flatMap((s) => CHARACTER_KINDS.filter((k) => k.section === s.key))
    expect(covered.length).toBe(CHARACTER_KINDS.length)
  })

  it('labels the new tool', () => {
    expect(toolLabel('char_info')).toBe('Character Info')
  })
})
