// Mirrors eve_trader.esi_data.registry (docs/ESI_ACCESS_PLAN.md Phase 0).
// Kept in sync by hand, same as AdminPage's ALL_TOOL_KEYS copy. There is no
// /api/characters/registry endpoint — do not infer consuming tools from
// sharing rows.

export interface OwnedDataKind {
  key: string
  label: string
  group: 1 | 2
  consumingTools: readonly string[]
  corpRoles: readonly string[]
}

export interface AccessCapability {
  key: string
  label: string
  corpRoles: readonly string[]
}

export const OWNED_DATA_KINDS: readonly OwnedDataKind[] = [
  {
    key: 'assets', label: 'Assets', group: 1,
    consumingTools: ['production', 'doctrine', 'sorting', 'trading', 'portfolio'],
    corpRoles: ['Director'],
  },
  {
    key: 'industry_jobs', label: 'Industry Jobs', group: 1,
    consumingTools: ['production'],
    corpRoles: ['Director'],
  },
  {
    key: 'blueprints', label: 'Blueprints', group: 1,
    consumingTools: ['production', 'portfolio'],
    corpRoles: ['Director'],
  },
  {
    key: 'market_orders', label: 'Market Orders', group: 1,
    consumingTools: ['trading', 'production', 'station_trading'],
    corpRoles: ['Accountant', 'Trader'],
  },
  {
    key: 'contracts', label: 'Contracts', group: 1,
    consumingTools: ['doctrine'],
    corpRoles: [],
  },
  {
    key: 'wallet', label: 'Wallet', group: 1,
    consumingTools: ['trading'],
    corpRoles: ['Accountant', 'Junior_Accountant'],
  },
  {
    key: 'wallet_balance', label: 'Wallet Balance', group: 1,
    consumingTools: ['portfolio'],
    corpRoles: ['Accountant', 'Junior_Accountant'],
  },
  {
    key: 'skills', label: 'Skills', group: 2,
    consumingTools: ['production', 'station_trading'],
    corpRoles: [],
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
]

export const TOOL_LABELS: Record<string, string> = {
  trading: 'Trading',
  production: 'Production',
  doctrine: 'Doctrine',
  station_trading: 'Station Trading',
  sorting: 'Sorting',
  portfolio: 'Portfolio',
}

export const CONSUMING_TOOL_KEYS = [
  'trading', 'production', 'doctrine', 'station_trading', 'sorting', 'portfolio',
] as const

export function kindByKey(key: string): OwnedDataKind | undefined {
  return OWNED_DATA_KINDS.find((k) => k.key === key)
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
