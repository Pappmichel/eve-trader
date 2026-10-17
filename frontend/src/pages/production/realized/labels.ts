import type { EsiTokenCharacter } from '../../../api/types'

const ACTIVITY_LABEL: Record<number, string> = {
  1: 'Manufacturing',
  3: 'Research TE',
  4: 'Research ME',
  5: 'Copying',
  8: 'Invention',
  9: 'Reaction',
  11: 'Reaction',
}

export const INVENTION_ACTIVITY_ID = 8

export function activityLabel(activityId: number): string {
  return ACTIVITY_LABEL[activityId] ?? `Activity ${activityId}`
}

/** Job rows only: null is "unknown", true is "no", false is "applied". */
export function structureBonusText(value: boolean | null | undefined): string {
  if (value === true) return 'no'
  if (value === false) return 'applied'
  return 'unknown'
}

const ORIGIN_ORDER = ['bought', 'produced', 'opening', 'unknown']

/** Whole quantities use the app's qty formatter. A fractional drift stays visible (never rounded to 0). */
export function displayQty(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '–'
  const rounded = Math.round(value)
  if (Math.abs(value - rounded) < 1e-6) return rounded.toLocaleString('en-US')
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

export function originMixLabel(mix: Record<string, number>, formatQty: (n: number) => string = displayQty): string {
  const extras = Object.keys(mix).filter((key) => !ORIGIN_ORDER.includes(key)).sort()
  const keys = [...ORIGIN_ORDER.filter((key) => key in mix), ...extras]
  if (keys.length === 0) return '–'
  return keys.map((key) => `${key} ${formatQty(mix[key])}`).join(' · ')
}

export function locationLabel(
  id: number | null | undefined,
  names: Record<string, string | null> | undefined,
): string {
  if (id == null) return '–'
  const name = names?.[String(id)]
  return name ? `${name} (${id})` : String(id)
}

export function ownerLabel(
  ownerType: string,
  ownerId: number,
  characters: EsiTokenCharacter[] | undefined,
): string {
  if (ownerType === 'character') {
    const match = characters?.find((row) => row.character_id === ownerId)
    return match?.character_name || String(ownerId)
  }
  if (ownerType === 'corporation') {
    const match = characters?.find((row) => row.corporation_id === ownerId)
    return match?.corporation_name || String(ownerId)
  }
  return String(ownerId)
}

const WARNING_TITLES: Record<string, string> = {
  inventory_drift: 'Inventory drift',
  trading_overlap: 'Trading overlap',
  archive_gap: 'Archive gaps',
  outside_home_structure: 'Sales outside the home structure',
  unresolved_region: 'Unresolved regions',
  unmatched_broker_fee: 'Unmatched broker fees',
  assumed_me: 'Assumed ME',
  assumed_decryptor: 'Assumed decryptor',
  negative_opening: 'Negative opening quantities',
}

/** Known kinds get a title; anything else keeps the API kind so the list stays open. */
export function warningTitle(kind: string): string {
  return WARNING_TITLES[kind] ?? kind
}

export type WarningFocus =
  | { tab: 'products' | 'inventory'; typeId: number }
  | { tab: 'jobs'; jobRef: string }

const INVENTORY_KINDS = new Set(['inventory_drift', 'negative_opening'])

export function warningTarget(kind: string, item: Record<string, unknown>): WarningFocus | null {
  if (typeof item.job_ref === 'string' && item.job_ref) {
    return { tab: 'jobs', jobRef: item.job_ref }
  }
  if (typeof item.job_id === 'number') {
    return { tab: 'jobs', jobRef: String(item.job_id) }
  }
  if (typeof item.type_id === 'number') {
    return { tab: INVENTORY_KINDS.has(kind) ? 'inventory' : 'products', typeId: item.type_id }
  }
  return null
}

const TAB_KEY = 'production-realized-tab'
export const REALIZED_TABS = ['overview', 'products', 'jobs', 'inventory', 'warnings', 'ledger'] as const
export type RealizedTabId = (typeof REALIZED_TABS)[number]

export function readRealizedTab(): RealizedTabId {
  try {
    const raw = localStorage.getItem(TAB_KEY)
    if (raw && (REALIZED_TABS as readonly string[]).includes(raw)) return raw as RealizedTabId
  } catch {
    // Storage unavailable (private mode) — fall back to Overview.
  }
  return 'overview'
}

export function writeRealizedTab(tab: RealizedTabId): void {
  try {
    localStorage.setItem(TAB_KEY, tab)
  } catch {
    // Ignore quota / private-mode failures; the tab still changes for this view.
  }
}
