// The four classic NPC trade hubs, by their region id (stable EVE-universe
// data - these ids never change). User feedback, 2026-10-01: "Possible to
// add other market hubs?" - `TradingConfig.jita_region_id` was already a
// plain settings field (any positive region id passed CI's own range check),
// but the Settings page exposed it as a bare number input and every other
// Trading page hardcoded the word "Jita" in its own UI text, so picking
// anything else required knowing a region id by heart and then reading a
// mislabeled column. This is purely a label/options lookup for the
// Settings page's dropdown and the Shortlist column headers - it does not
// change what `jita_region_id` *is* (still a single int field, still named
// `jita_region_id` end to end, see its own field-rename caveat below) or how
// `esi_client.region_order_stats*` uses it (region-scoped, already hub-
// agnostic).
//
// Known real limitation (confirmed in code review before shipping this,
// not fixed here - flagged to the user): `own_orders.py`'s own
// `JITA_SOLAR_SYSTEM_ID` (30000142, Jita IV - Moon 4) is a SEPARATE hardcoded
// constant used to recognize a character's own assets sitting physically in
// Jita for shortlist "already covered" bookkeeping - it does not follow this
// setting. Picking Amarr/Dodixie/Rens here correctly repoints every
// region-order-book read (`cfg.jita_region_id`, already dynamic everywhere
// it's read: Trading, Production, Doctrine, Ore & Minerals, Station
// Trading, Module Reprocessing all consume the same `TRADING_CONFIG`
// value), but assets physically sitting in the new hub will not be detected
// as "already covered" by that one check - a real gap for a non-Jita hub,
// not a cosmetic one. Don't extend this list or treat the hub as fully
// interchangeable without fixing that too.
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

export function hubLabel(regionId: number | undefined | null): string {
  const hub = TRADE_HUBS.find((h) => h.regionId === regionId)
  return hub ? hub.label.split(' (')[0] : `Region ${regionId ?? '?'}`
}
