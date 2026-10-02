// Mirrors eve_trader.esi_data.registry (docs/ESI_ACCESS_PLAN.md Phase 0).
// Kept in sync by hand, same as AdminPage's ALL_TOOL_KEYS copy. There is no
// /api/characters/registry endpoint — do not infer consuming tools from
// sharing rows.

// Column groups on the Characters page's sharing table (CHARACTER_MANAGEMENT_
// PLAN.md R6): the table outgrew one flat row of columns once Character
// Management added kinds.
export type KindSection = 'industry' | 'character'

export const KIND_SECTIONS: readonly { key: KindSection; label: string }[] = [
  { key: 'industry', label: 'Industry & Trading' },
  { key: 'character', label: 'Character' },
]

export interface OwnedDataKind {
  key: string
  label: string
  group: 1 | 2
  section: KindSection
  // Mirrors the registry's `live_only`: read straight from ESI when the tool
  // page is open, never stored or synced (so there is nothing to re-sync and
  // the freshness column never applies to it).
  liveOnly?: boolean
  // Popover note for a live-only kind; falls back to the plain "never stored"
  // wording. Mail overrides it: it can be archived if the user opts in.
  liveNote?: string
  consumingTools: readonly string[]
  corpRoles: readonly string[]
  // Only set where a tool in consumingTools is actually backed by a
  // *different* data_kind under the hood than this row's own `key` (2026-09-27:
  // "Wallet" and the former standalone "Wallet Balance" row merged into one
  // visual row - Trading's own wallet sync (transactions+journal) and
  // Portfolio's wallet-balance sync are still two separate, independently-
  // scheduled ESI fetches server-side, same OAuth scope but very different
  // cost/shape, so the sharing *data* stays split; only the UI presentation
  // merged). Falls back to `key` for every tool not listed here.
  toolDataKind?: Readonly<Record<string, string>>
}

export interface AccessCapability {
  key: string
  label: string
  corpRoles: readonly string[]
}

export const OWNED_DATA_KINDS: readonly OwnedDataKind[] = [
  {
    key: 'assets', section: 'industry', label: 'Assets', group: 1,
    consumingTools: ['production', 'doctrine', 'sorting', 'trading', 'portfolio'],
    corpRoles: ['Director'],
  },
  {
    key: 'industry_jobs', section: 'industry', label: 'Industry Jobs', group: 1,
    consumingTools: ['production'],
    corpRoles: ['Director'],
  },
  {
    key: 'blueprints', section: 'industry', label: 'Blueprints', group: 1,
    consumingTools: ['production', 'portfolio'],
    corpRoles: ['Director'],
  },
  {
    key: 'market_orders', section: 'industry', label: 'Market Orders', group: 1,
    consumingTools: ['trading', 'production', 'station_trading'],
    corpRoles: ['Accountant', 'Trader'],
  },
  {
    key: 'contracts', section: 'industry', label: 'Contracts', group: 1,
    consumingTools: ['doctrine'],
    corpRoles: [],
  },
  {
    key: 'wallet', section: 'industry', label: 'Wallet', group: 1,
    consumingTools: ['trading', 'portfolio', 'char_info'],
    corpRoles: ['Accountant', 'Junior_Accountant'],
    toolDataKind: { portfolio: 'wallet_balance', char_info: 'wallet_balance' },
  },
  {
    // Moved to the Character section with phase 2: the per-skill rows,
    // attributes and SP totals are character data first, and Skills (char_skills)
    // joined Production and Station Trading as a consumer.
    key: 'skills', section: 'character', label: 'Skills', group: 2,
    consumingTools: ['production', 'station_trading', 'char_skills', 'char_skill_plans'],
    corpRoles: [],
  },
  {
    key: 'skillqueue', section: 'character', label: 'Skill Queue', group: 2,
    consumingTools: ['char_skills', 'char_alerts'], corpRoles: [],
  },
  // Character Management (docs/CHARACTER_MANAGEMENT_PLAN.md phase 1).
  {
    key: 'standings', section: 'character', label: 'Standings', group: 2,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'loyalty', section: 'character', label: 'Loyalty Points', group: 2,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'clones', section: 'character', label: 'Clones', group: 2,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'implants', section: 'character', label: 'Implants', group: 2,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'fatigue', section: 'character', label: 'Jump Fatigue', group: 2, liveOnly: true,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'contacts', section: 'character', label: 'Contacts', group: 2, liveOnly: true,
    consumingTools: ['char_contacts'], corpRoles: [],
  },
  {
    key: 'calendar', section: 'character', label: 'Calendar', group: 2, liveOnly: true,
    consumingTools: ['char_contacts'], corpRoles: [],
  },
  {
    key: 'notifications', section: 'character', label: 'Notifications', group: 2,
    consumingTools: ['char_notifications'], corpRoles: [],
  },
  {
    key: 'location', section: 'character', label: 'Location', group: 2, liveOnly: true,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'ship', section: 'character', label: 'Current Ship', group: 2, liveOnly: true,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'online', section: 'character', label: 'Online Status', group: 2, liveOnly: true,
    consumingTools: ['char_info'], corpRoles: [],
  },
  {
    key: 'mail', section: 'character', label: 'Mail', group: 2, liveOnly: true,
    liveNote: 'Read live from ESI in Mail and not stored - unless you turn on "Archive mail" for this '
      + 'character in Mail settings, which keeps a copy in this app\'s database until you delete it.',
    consumingTools: ['char_mail', 'char_alerts'], corpRoles: [],
  },
]

export const GROUP_1_KINDS = OWNED_DATA_KINDS.filter((k) => k.group === 1)
export const CHARACTER_KINDS = OWNED_DATA_KINDS

export const ACCESS_CAPABILITIES: readonly AccessCapability[] = [
  {
    key: 'structure_name_resolution',
    label: 'Structure name resolution',
    corpRoles: ['Station_Manager'],
  },
  {
    key: 'structure_market_book',
    label: 'Structure market book',
    corpRoles: [],
  },
  {
    key: 'corporation_roles',
    label: 'Corporation roles',
    corpRoles: [],
  },
  // Mail write actions (Character Management phase 4): consent for this app to
  // act for the character in the game. Not data kinds - no sharing, no sync.
  { key: 'mail_send', label: 'Send mail', corpRoles: [] },
  { key: 'mail_organize', label: 'Organize mail', corpRoles: [] },
]

export const TOOL_LABELS: Record<string, string> = {
  trading: 'Trading',
  production: 'Production',
  doctrine: 'Doctrine',
  station_trading: 'Station Trading',
  sorting: 'Sorting',
  portfolio: 'Portfolio',
  char_info: 'Character Info',
  char_skills: 'Skills',
  char_mail: 'Mail',
  char_notifications: 'Notifications',
  char_contacts: 'Contacts & Calendar',
  char_skill_plans: 'Skill Plans',
  char_alerts: 'Discord Alerts',
}

export const CONSUMING_TOOL_KEYS = [
  'trading', 'production', 'doctrine', 'station_trading', 'sorting', 'portfolio', 'char_info', 'char_skills', 'char_mail',
  'char_notifications', 'char_contacts', 'char_skill_plans', 'char_alerts',
] as const

export function kindByKey(key: string): OwnedDataKind | undefined {
  return OWNED_DATA_KINDS.find((k) => k.key === key)
}

// The real data_kind a given tool's sharing row lives under for this row -
// `kind.key` unless `toolDataKind` overrides it for that specific tool (see
// that field's own docstring).
export function dataKindForTool(kind: OwnedDataKind, toolKey: string): string {
  return kind.toolDataKind?.[toolKey] ?? kind.key
}

export function capabilityByKey(key: string): AccessCapability | undefined {
  return ACCESS_CAPABILITIES.find((c) => c.key === key)
}

export function toolLabel(toolKey: string): string {
  return TOOL_LABELS[toolKey] ?? toolKey
}

export function formatCorpRoles(roles: readonly string[]): string {
  if (roles.length === 0) return ''
  if (roles.length === 1) return roles[0]
  if (roles.length === 2) return `${roles[0]} or ${roles[1]}`
  return `${roles.slice(0, -1).join(', ')}, or ${roles[roles.length - 1]}`
}
