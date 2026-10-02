import { describe, expect, it } from 'vitest'

import { CHARACTER_KINDS, KIND_SECTIONS, OWNED_DATA_KINDS, kindByKey, toolLabel } from './esiRegistry'

describe('esiRegistry Character Management kinds', () => {
  it('mirrors the backend: seven char_info kinds, three of them live-only', () => {
    for (const key of ['standings', 'loyalty', 'clones', 'implants', 'location', 'ship', 'online']) {
      const kind = kindByKey(key)!
      expect(kind.consumingTools).toEqual(['char_info'])
      expect(kind.group).toBe(2)
    }
    expect(OWNED_DATA_KINDS.filter((k) => k.liveOnly).map((k) => k.key).sort())
      .toEqual(['calendar', 'contacts', 'fatigue', 'location', 'mail', 'online', 'ship'])
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

  it('mirrors phase 2: Skills is a Character-section kind with three consumers, plus the skill queue', () => {
    const skills = kindByKey('skills')!
    expect(skills.section).toBe('character')
    expect(skills.consumingTools).toEqual(['production', 'station_trading', 'char_skills', 'char_skill_plans'])
    const queue = kindByKey('skillqueue')!
    expect(queue.consumingTools).toEqual(['char_skills', 'char_alerts'])
    expect(queue.liveOnly).toBeUndefined()      // a synced snapshot, not a live read
    expect(toolLabel('char_skills')).toBe('Skills')
  })

  it('mirrors phase 3: Mail is a live-only kind with its own note about the opt-in archive', () => {
    const mail = kindByKey('mail')!
    expect(mail.consumingTools).toEqual(['char_mail', 'char_alerts'])
    expect(mail.liveOnly).toBe(true)
    expect(mail.liveNote).toMatch(/Archive mail/)
    expect(toolLabel('char_mail')).toBe('Mail')
    // the other live kinds keep the default wording (no override)
    expect(kindByKey('location')!.liveNote).toBeUndefined()
  })

  it('mirrors phase 6: Notifications is a synced snapshot kind for its own tool', () => {
    const n = kindByKey('notifications')!
    expect(n.consumingTools).toEqual(['char_notifications'])
    expect(n.liveOnly).toBeUndefined()
    expect(toolLabel('char_notifications')).toBe('Notifications')
  })
})
