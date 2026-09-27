import type { AccessPreview } from './api/types'
import type { EsiCapabilityRow, EsiFreshnessRow, EsiSharingRow } from './api/types'
import { CONSUMING_TOOL_KEYS, OWNED_DATA_KINDS, dataKindForTool, kindByKey, toolLabel } from './esiRegistry'
import type { OwnedDataKind } from './esiRegistry'

export type CellKind = 'not_shared' | 'all' | 'some' | 'pending' | 'error'

export interface CellState {
  kind: CellKind
  sharedCount: number
  capableCount: number
  lastError: string | null
}

// `kind` (not just its `key`) so a per-tool `toolDataKind` override (see
// esiRegistry.ts, e.g. the merged Wallet row's Portfolio toggle actually
// living under 'wallet_balance') resolves correctly instead of every tool
// being checked against the row's own nominal key.
export function sharedToolsFor(
  sharing: EsiSharingRow[],
  ownerType: string,
  ownerId: number,
  kind: OwnedDataKind,
): string[] {
  const tools = new Set<string>()
  for (const toolKey of kind.consumingTools) {
    const dk = dataKindForTool(kind, toolKey)
    const isShared = sharing.some(
      (row) => row.owner_type === ownerType && row.owner_id === ownerId
        && row.data_kind === dk && row.tool_key === toolKey,
    )
    if (isShared) tools.add(toolKey)
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
  const kind = kindByKey(args.dataKind)
  const capableCount = kind?.consumingTools.length ?? 0
  const shared = kind ? sharedToolsFor(args.sharing, args.ownerType, args.ownerId, kind) : []
  const sharedCount = shared.length
  // Every real data_kind this row's tools might actually live under (usually
  // just the row's own key; the merged Wallet row also covers 'wallet_balance')
  // - a failure on *either* underlying fetch should surface on this one cell.
  const underlyingKinds = new Set(
    kind ? kind.consumingTools.map((toolKey) => dataKindForTool(kind, toolKey)) : [args.dataKind],
  )
  const lastError = [...underlyingKinds]
    .map((dk) => freshnessError(args.freshness, args.ownerType, args.ownerId, dk))
    .find((err) => err != null) ?? null
  const pending = [...underlyingKinds].some((dk) => args.pendingKinds.has(dk))
  if (sharedCount > 0 && lastError) {
    return { kind: 'error', sharedCount, capableCount, lastError }
  }
  if (sharedCount > 0 && pending) {
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
      const dk = dataKindForTool(kind, toolKey)
      const owners = sharing
        .filter((r) => r.tool_key === toolKey && r.data_kind === dk)
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
