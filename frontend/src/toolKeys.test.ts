import { describe, expect, it } from 'vitest'

import { OWNED_DATA_KINDS } from './esiRegistry'
import {
  ALL_TOOL_KEYS, CHARACTER_MANAGEMENT_TOOL_KEYS, ESI_CONSUMING_TOOLS, hasAnyToolGrant,
} from './toolKeys'

describe('toolKeys drift guards', () => {
  it('ESI_CONSUMING_TOOLS is exactly the union of esiRegistry consumingTools', () => {
    const union = new Set(OWNED_DATA_KINDS.flatMap((k) => [...k.consumingTools]))
    expect([...ESI_CONSUMING_TOOLS].sort()).toEqual([...union].sort())
  })

  it('every consuming tool and hub sub-tool is a known tool key', () => {
    const all = new Set<string>(ALL_TOOL_KEYS)
    for (const k of [...ESI_CONSUMING_TOOLS, ...CHARACTER_MANAGEMENT_TOOL_KEYS]) {
      expect(all.has(k)).toBe(true)
    }
  })
})

describe('hasAnyToolGrant', () => {
  it('treats undefined as "not loaded, show everything"', () => {
    expect(hasAnyToolGrant(undefined, ['characters'])).toBe(true)
  })
  it('is true when any key is held, false when none', () => {
    expect(hasAnyToolGrant(['trading', 'characters'], ['a', 'characters'])).toBe(true)
    expect(hasAnyToolGrant(['trading'], ['characters'])).toBe(false)
    expect(hasAnyToolGrant([], ['characters'])).toBe(false)
  })
})
