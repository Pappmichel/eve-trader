// One label for a place on every Asset Manager view: its name, or why it has
// none (location registry status, docs/LOCATION_RESOLUTION_PLAN.md).
const LOOKUP_HINT: Record<string, string> = {
  forbidden: 'no access', not_found: 'not found', transient: 'lookup failed, will retry',
  pending: 'not looked up yet',
}
export const STRUCTURE_ID_MIN = 1_000_000_000_000

export interface LabelledLocation {
  location_id?: number | null
  location_name?: string | null
  location_status?: string | null
}

export function locationLabel(row: LabelledLocation, empty = 'No location'): string {
  if (row.location_name) return row.location_name
  if (!row.location_id) return empty
  const hint = row.location_status ? LOOKUP_HINT[row.location_status] : undefined
  if (!hint) return `Location ${row.location_id}`
  return `${row.location_id >= STRUCTURE_ID_MIN ? 'Structure' : 'Location'} ${row.location_id} (${hint})`
}
