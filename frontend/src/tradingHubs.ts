// The trade hubs come from the backend (GET /api/hubs, `hubs.HUBS`), loaded by
// `useTradeHubs()`. Every tool has its own hub (GitHub issue #222):
// `TradingConfig.jita_region_id` is the Trading tool's buy hub (picked on the
// Shortlist page; the field keeps its historical name), the other tools use
// their own `hub_region_id` via `HubSelect`. Labels and picker data take the
// loaded list as a parameter; while it is still loading (`undefined`) a stored
// region shows as "Region n" instead of a wrong or empty name.
export interface TradeHub {
  region_id: number
  name: string
  // "Jita (The Forge)"
  label: string
  solar_system_id: number
  station_id: number
}

// Hub setting value for "price every item at its best hub" (hubs.ALL_HUBS).
export const ALL_HUBS = 0

export function hubLabel(hubs: readonly TradeHub[] | undefined, regionId: number | undefined | null): string {
  if (regionId === ALL_HUBS) return 'Best hub'
  const hub = hubs?.find((h) => h.region_id === regionId)
  return hub ? hub.name : `Region ${regionId ?? '?'}`
}

// Select options for a hub picker; a stored region id outside the hub list
// (or while the list loads) stays selectable instead of being dropped.
export function hubSelectData(
  hubs: readonly TradeHub[] | undefined, regionId: number | undefined | null, allowAll = false,
): { value: string; label: string }[] {
  const known = (hubs ?? []).map((h) => ({ value: String(h.region_id), label: h.label }))
  if (allowAll) known.push({ value: String(ALL_HUBS), label: 'All hubs (best per item)' })
  if (regionId == null || (allowAll && regionId === ALL_HUBS) || (hubs ?? []).some((h) => h.region_id === regionId)) return known
  return [...known, { value: String(regionId), label: hubs ? `Custom (region ${regionId})` : `Region ${regionId}` }]
}
