import type { StockTarget } from '../../api/types'

// Everything a mode change, a bulk action, the switch or a recalculation can move: the table itself
// plus the plan and market views that read the effective targets (docs/DYNAMIC_STOCK_TARGETS_PLAN.md P-36).
export const DYNAMIC_KEYS = [
  ['production', 'stock-targets'],
  ['production', 'plan'],
  ['production', 'asset-plan'],
  ['production', 'market-status'],
  ['production', 'stock-value'],
  ['production', 'dynamic-settings'],
]

export const DYNAMIC_SETTINGS_KEY = ['production', 'dynamic-settings']

/** Rows "All to dynamic" can switch: the ones with a home target (backup-only and Jita-only rows have nothing to scale). */
export function bulkCandidates(targets: StockTarget[]): { applicable: number; skipped: number } {
  const applicable = targets.filter((t) => (t.home_market_stock ?? 0) > 0).length
  return { applicable, skipped: targets.length - applicable }
}
