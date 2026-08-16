// Thin typed fetch wrapper - one function per backend route (see
// eve_trader/api/routers/*.py). Relative /api/... paths work both in dev
// (Vite proxies to localhost:8000, see vite.config.ts) and in the built
// "single process" mode (FastAPI serves the built frontend on the same
// origin as the API - see api/app.py's StaticFiles mount).
import type * as T from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// FastAPI's default 422 (request body/query failed Pydantic validation)
// sends `detail` as a *list* of {loc, msg, type} objects, not a string -
// every other error path (ActionError -> HTTP 400 via routers' _wrap) sends
// a plain string. Confirmed real bug: `new Error(arrayOfObjects)` stringifies
// to "[object Object],[object Object]", an unreadable toast, for any request
// that fails validation (e.g. a malformed number in a POST body) - exactly
// the case where the message is most useful to see.
function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (item && typeof item === 'object' && 'msg' in item) {
        const loc = Array.isArray(item.loc) ? item.loc.filter((p: unknown) => p !== 'body').join('.') : null
        return loc ? `${loc}: ${item.msg}` : String(item.msg)
      }
      return typeof item === 'string' ? item : JSON.stringify(item)
    })
    return messages.join('; ') || fallback
  }
  return fallback
}

async function request<TResp>(path: string, init?: RequestInit): Promise<TResp> {
  const resp = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      detail = formatErrorDetail(body.detail, detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail)
  }
  if (resp.status === 204) return undefined as TResp
  return resp.json() as Promise<TResp>
}

const get = <TResp>(path: string) => request<TResp>(path)
const post = <TResp>(path: string, body?: unknown) =>
  request<TResp>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
const del = <TResp>(path: string) => request<TResp>(path, { method: 'DELETE' })

// ------------------------------------------------------------------- auth
export const authApi = {
  start: (rolePrefix: string) => get<{ url: string }>(`/api/auth/${rolePrefix}/start`),
  status: () => get<T.AuthStatus>('/api/auth/status'),
}

// ---------------------------------------------------------------- trading
export const tradingApi = {
  kpis: () => get<T.TradingKpis>('/api/trading/kpis'),
  shortlistSnapshot: () => get<T.ShortlistRow[]>('/api/trading/shortlist/snapshot'),
  shortlistItems: () => get<T.ShortlistItem[]>('/api/trading/shortlist/items'),
  shortlistTrends: () => get<Record<number, T.MarginTrend>>('/api/trading/shortlist/trends'),
  candidateUniverse: () => get<T.Candidate[]>('/api/trading/candidates/universe'),
  focusedCandidates: () => get<T.Candidate[]>('/api/trading/candidates/focused'),
  newCandidates: () => get<T.NewCandidateResult[]>('/api/trading/candidates/new'),
  historyTypeIds: () => get<number[]>('/api/trading/history/type-ids'),
  history: (typeId: number) => get<T.PriceHistoryPoint[]>(`/api/trading/history/${typeId}`),
  realizedTrades: () => get<T.RealizedTrade[]>('/api/trading/trades/realized'),
  settings: () => get<T.TradingSettings>('/api/trading/settings'),
  updateSettings: (s: T.TradingSettings) => post<T.TradingSettings>('/api/trading/settings', s),
  esiSyncTime: () => get<{ synced_at: string | null }>('/api/trading/esi/sync-time'),

  buildUniverse: () => post<{ count: number }>('/api/trading/universe/build'),
  buildFocused: () => post<{ count: number }>('/api/trading/universe/focus'),
  findNewCandidates: (safe = true) =>
    post<{ evaluated: number; recommended: number }>(`/api/trading/candidates/find-new?safe=${safe}`),
  addToShortlist: () => post<{ added: number }>('/api/trading/shortlist/add-new'),
  refreshShortlist: () => post<Record<string, unknown>>('/api/trading/shortlist/refresh'),
  refreshAndPruneCandidates: (safe = true) =>
    post<Record<string, unknown>>(`/api/trading/candidates/refresh-and-prune?safe=${safe}`),
  reconcileTrades: () => post<Record<string, unknown>>('/api/trading/trades/reconcile'),
  runPipeline: (safe = true, rebuildUniverse = false) =>
    post<Record<string, unknown>>(
      `/api/trading/pipeline/run?safe=${safe}&rebuild_universe=${rebuildUniverse}`,
    ),
  checkSellerUnlistedStock: () => post<T.UnlistedStockRow[]>('/api/trading/seller/unlisted-stock'),
  checkUndercut: () => post<T.UndercutRow[]>('/api/trading/seller/undercut'),
}

// ------------------------------------------------------------- production
export const productionApi = {
  sdeCounts: () => get<Record<string, number>>('/api/production/sde/counts'),
  sdeFreshness: () => get<T.SdeFreshness>('/api/production/sde/freshness'),
  stockTargets: () => get<T.StockTarget[]>('/api/production/stock-targets'),
  manualStock: () => get<Record<string, number>>('/api/production/manual-stock'),
  manualBuildBuy: () => get<Record<string, string>>('/api/production/manual-build-buy'),
  selectedDecryptors: () => get<Record<string, string>>('/api/production/selected-decryptors'),
  plan: () => get<T.ProductionPlan | null>('/api/production/plan'),
  assetPlan: () => get<T.AssetPlan | null>('/api/production/asset-plan'),
  marketStatus: () => get<T.MarketStatusRow[]>('/api/production/market-status'),
  stockValue: () => get<{ total_value: number; priced_items: number; unpriced_items: number }>('/api/production/stock-value'),
  checkUnlistedStock: () => post<T.ProductionUnlistedStockRow[]>('/api/production/unlisted-stock/check'),
  discoverBuildCandidates: (topN = 200) => post<T.BuildCandidate[]>(`/api/production/build-candidates/discover?top_n=${topN}`),
  materialTree: (typeName: string, quantity: number) =>
    post<T.MaterialTreeNode>('/api/production/material-tree', { type_name: typeName, quantity }),
  searchAssetLocations: (itemName: string) =>
    post<T.AssetLocationSearchResult>('/api/production/asset-locations', { item_name: itemName }),
  jobs: () => get<T.IndustryJobRow[]>('/api/production/jobs'),
  slots: () => get<T.CharacterSlotRow[]>('/api/production/slots'),
  producerCharacters: () => get<T.ProducerCharacter[]>('/api/production/producer-characters'),
  ownedBlueprints: () => get<T.OwnedBlueprintRow[]>('/api/production/blueprints'),
  settings: () => get<T.ProductionSettings>('/api/production/settings'),
  updateSettings: (s: T.ProductionSettings) => post<T.ProductionSettings>('/api/production/settings', s),
  structureOptions: () => get<{ structure_types: string[]; rig_tiers: string[] }>(
    '/api/production/settings/structure-options',
  ),
  systemSettings: () =>
    get<{
      component_system_name: string | null
      component_system_id: number | null
      manufacturing_system_name: string | null
      manufacturing_system_id: number | null
    }>('/api/production/settings/systems'),
  setSystem: (profile: string, systemName: string) =>
    post('/api/production/settings/systems', { profile, system_name: systemName }),
  decryptors: () => get<string[]>('/api/production/decryptors'),
  jobCategories: () => get<string[]>('/api/production/job-categories'),
  categoryLocations: () => get<Record<string, number>>('/api/production/logistics/locations'),
  setCategoryLocation: (category: string, locationId: number) =>
    post('/api/production/logistics/locations', { category, location_id: locationId }),
  clearCategoryLocation: (category: string) => del(`/api/production/logistics/locations/${encodeURIComponent(category)}`),
  categoryLocationOptions: () => get<Record<string, number[]>>('/api/production/logistics/location-options'),
  addCategoryLocationOption: (category: string, locationId: number) =>
    post('/api/production/logistics/location-options', { category, location_id: locationId }),
  removeCategoryLocationOption: (category: string, locationId: number) =>
    del(`/api/production/logistics/location-options/${encodeURIComponent(category)}/${locationId}`),
  logisticsStatus: () => get<T.LogisticsRow[]>('/api/production/logistics'),
  structureNames: () => get<Record<string, string | null>>('/api/production/logistics/structure-names'),
  resolveStructureName: (locationId: number, force = false) =>
    post<{ location_id: number; name: string | null; cached: boolean }>(
      '/api/production/logistics/resolve-structure-name', { location_id: locationId, force },
    ),

  refreshSde: () => post<Record<string, number>>('/api/production/sde/refresh'),
  addCharacter: () =>
    post<{ character_name: string; character_id: number }>('/api/production/auth/add-character'),
  removeCharacter: (roleKey: string) => del(`/api/production/auth/character/${roleKey}`),
  syncEsi: () => post<Record<string, unknown>>('/api/production/esi/sync'),
  esiSyncTime: () => get<{ synced_at: string | null }>('/api/production/esi/sync-time'),

  addStockTarget: (req: {
    type_name: string
    backup_stock?: number
    home_market_stock?: number | null
    jita_market_stock?: number | null
  }) => post<T.StockTarget>('/api/production/stock-targets', req),
  removeStockTarget: (typeId: number) => del(`/api/production/stock-targets/${typeId}`),
  setManualStock: (typeId: number, count: number) =>
    post('/api/production/manual-stock', { type_id: typeId, count }),
  setManualBuildBuy: (typeId: number, decision: string) =>
    post('/api/production/manual-build-buy', { type_id: typeId, decision }),
  clearManualBuildBuy: (typeId: number) => del(`/api/production/manual-build-buy/${typeId}`),
  setSelectedDecryptor: (typeId: number, decryptor: string) =>
    post('/api/production/selected-decryptors', { type_id: typeId, decryptor }),
  clearSelectedDecryptor: (typeId: number) => del(`/api/production/selected-decryptors/${typeId}`),

  estimateInvention: (productName: string, decryptorName?: string | null) =>
    post<T.InventionResult[]>('/api/production/invention/estimate', {
      product_name: productName,
      decryptor_name: decryptorName ?? null,
    }),

  refreshPlan: () =>
    post<{ stock_targets: number; missing_types: number; buy_entries: number; build_jobs: number }>(
      '/api/production/plan/refresh',
    ),
  refreshAssetPlan: () => post<{ jobs: number }>('/api/production/asset-plan/refresh'),
}

// ------------------------------------------------------------- portfolio
export const portfolioApi = {
  overview: () => get<T.PortfolioOverview>('/api/portfolio/overview'),
  schedulerStatus: () => get<T.SchedulerStatus>('/api/portfolio/scheduler-status'),
  backups: () => get<T.BackupInfo[]>('/api/portfolio/backups'),
  createBackup: () => post<T.BackupInfo>('/api/portfolio/backups'),
}
