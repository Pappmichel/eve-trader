// The four classic NPC trade hubs, by region id (stable EVE data). Used for
// hub pickers and labels. Every tool has its own hub (GitHub issue #222):
// `TradingConfig.jita_region_id` is the Trading tool's buy hub (picked on the
// Shortlist page; the field keeps its historical name), the other tools use
// their own `hub_region_id` via `HubSelect`. own_orders.py maps each of these
// regions to its hub solar system for the "already covered" asset check; a
// custom region falls back to every NPC station in that region.
export interface TradeHub {
  label: string
  regionId: number
}

export const TRADE_HUBS: readonly TradeHub[] = [
  { label: 'Jita (The Forge)', regionId: 10000002 },
  { label: 'Amarr (Domain)', regionId: 10000043 },
  { label: 'Dodixie (Sinq Laison)', regionId: 10000032 },
  { label: 'Rens (Heimatar)', regionId: 10000030 },
]

// Hub setting value for "price every item at its best hub" (hubs.ALL_HUBS).
export const ALL_HUBS = 0

export function hubLabel(regionId: number | undefined | null): string {
  if (regionId === ALL_HUBS) return 'Best hub'
  const hub = TRADE_HUBS.find((h) => h.regionId === regionId)
  return hub ? hub.label.split(' (')[0] : `Region ${regionId ?? '?'}`
}

// Select options for a hub picker; a stored region id outside the four hubs
// stays selectable as "Custom" instead of being silently dropped.
export function hubSelectData(regionId: number | undefined | null, allowAll = false): { value: string; label: string }[] {
  const known = TRADE_HUBS.map((h) => ({ value: String(h.regionId), label: h.label }))
  if (allowAll) known.push({ value: String(ALL_HUBS), label: 'All hubs (best per item)' })
  if (regionId == null || (allowAll && regionId === ALL_HUBS) || TRADE_HUBS.some((h) => h.regionId === regionId)) return known
  return [...known, { value: String(regionId), label: `Custom (region ${regionId})` }]
}
