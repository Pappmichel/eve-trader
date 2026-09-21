import type { AccessPreview } from './api/types'
import type { EsiCapabilityRow, EsiFreshnessRow, EsiSharingRow } from './api/types'
import { CONSUMING_TOOL_KEYS, OWNED_DATA_KINDS, kindByKey, toolLabel } from './esiRegistry'

export type CellKind = 'not_shared' | 'all' | 'some' | 'pending' | 'error'

export interface CellState {
  kind: CellKind
  sharedCount: number
  capableCount: number
  lastError: string | null
}

export function sharedToolsFor(
  sharing: EsiSharingRow[],
  ownerType: string,
  ownerId: number,
  dataKind: string,
): string[] {
  const capable = new Set(kindByKey(dataKind)?.consumingTools ?? [])
  const tools = new Set<string>()
  for (const row of sharing) {
    if (row.owner_type === ownerType && row.owner_id === ownerId && row.data_kind === dataKind && capable.has(row.tool_key)) {
      tools.add(row.tool_key)
    }
  }
  return [...tools]
}

export function freshnessError(
  freshness: EsiFreshnessRow[],
  ownerType: string,
  ownerId: number,
  dataKind: string,
): string | null {
  for (const row of freshness) {
    if (row.owner_type === ownerType && row.owner_id === ownerId && row.data_kind === dataKind) {
      return row.last_error
    }
  }
  return null
}

export function deriveCellState(args: {
  ownerType: string
  ownerId: number
  dataKind: string
  sharing: EsiSharingRow[]
  freshness: EsiFreshnessRow[]
  pendingKinds: ReadonlySet<string>
}): CellState {
  const capableCount = kindByKey(args.dataKind)?.consumingTools.length ?? 0
  const shared = sharedToolsFor(args.sharing, args.ownerType, args.ownerId, args.dataKind)
  const sharedCount = shared.length
  const lastError = freshnessError(args.freshness, args.ownerType, args.ownerId, args.dataKind)
  if (sharedCount > 0 && lastError) {
    return { kind: 'error', sharedCount, capableCount, lastError }
  }
  if (sharedCount > 0 && args.pendingKinds.has(args.dataKind)) {
    return { kind: 'pending', sharedCount, capableCount, lastError: null }
  }
  if (sharedCount === 0) {
    return { kind: 'not_shared', sharedCount, capableCount, lastError: null }
  }
  if (capableCount > 0 && sharedCount >= capableCount) {
    return { kind: 'all', sharedCount, capableCount, lastError: null }
  }
  return { kind: 'some', sharedCount, capableCount, lastError: null }
}

export function characterRemovalImpact(
  characterId: number,
  sharing: EsiSharingRow[],
  capabilities: EsiCapabilityRow[],
): { tools: string[]; capabilityKeys: string[] } {
  const tools = new Set<string>()
  for (const row of sharing) {
    if (row.owner_type === 'character' && row.owner_id === characterId) {
      tools.add(row.tool_key)
    }
  }
  const known = CONSUMING_TOOL_KEYS.filter((key) => tools.has(key))
  const unknown = [...tools].filter((key) => !(CONSUMING_TOOL_KEYS as readonly string[]).includes(key)).sort()
  const capabilityKeys = [...new Set(
    capabilities.filter((row) => row.character_id === characterId).map((row) => row.capability_key),
  )].sort()
  return { tools: [...known, ...unknown], capabilityKeys }
}


export function extraKindsForCharacter(
  characterId: number,
  sharing: EsiSharingRow[],
  capabilities: EsiCapabilityRow[],
): string[] {
  const keys = new Set<string>()
  for (const row of sharing) {
    if (row.owner_type === 'character' && row.owner_id === characterId) {
      keys.add(row.data_kind)
    }
  }
  for (const row of capabilities) {
    if (row.character_id === characterId) keys.add(row.capability_key)
  }
  return [...keys]
}

export function pendingKeysFromPreview(preview: AccessPreview | undefined): Set<string> {
  const out = new Set<string>()
  if (!preview) return out
  for (const item of preview.items) {
    if (item.added) out.add(item.key)
  }
  return out
}

export interface ToolViewKind {
  kindKey: string
  kindLabel: string
  owners: { ownerType: string; ownerId: number; label: string }[]
}

export interface ToolViewRow {
  toolKey: string
  toolLabel: string
  receives: ToolViewKind[]
  missing: ToolViewKind[]
}

export function buildToolView(
  sharing: EsiSharingRow[],
  ownerLabel: (ownerType: string, ownerId: number) => string,
): ToolViewRow[] {
  return CONSUMING_TOOL_KEYS.map((toolKey) => {
    const receives: ToolViewKind[] = []
    const missing: ToolViewKind[] = []
    for (const kind of OWNED_DATA_KINDS) {
      if (!kind.consumingTools.includes(toolKey)) continue
      const owners = sharing
        .filter((r) => r.tool_key === toolKey && r.data_kind === kind.key)
        .map((r) => ({
          ownerType: r.owner_type,
          ownerId: r.owner_id,
          label: ownerLabel(r.owner_type, r.owner_id),
        }))
      const unique = [...new Map(owners.map((o) => [`${o.ownerType}:${o.ownerId}`, o])).values()]
      const entry = { kindKey: kind.key, kindLabel: kind.label, owners: unique }
      if (unique.length === 0) missing.push(entry)
      else receives.push(entry)
    }
    return { toolKey, toolLabel: toolLabel(toolKey), receives, missing }
  })
}

export function corporationIdsFrom(
  sharing: EsiSharingRow[],
  freshness: EsiFreshnessRow[],
): number[] {
  const ids = new Set<number>()
  for (const row of sharing) {
    if (row.owner_type === 'corporation') ids.add(row.owner_id)
  }
  for (const row of freshness) {
    if (row.owner_type === 'corporation') ids.add(row.owner_id)
  }
  return [...ids].sort((a, b) => a - b)
}
