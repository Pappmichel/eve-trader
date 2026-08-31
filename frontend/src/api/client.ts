// Thin typed fetch wrapper - one function per backend route (see
// eve_trader/api/routers/*.py). Relative /api/... paths work both in dev
// (Vite proxies to localhost:8000, see vite.config.ts) and in the built
// "single process" mode (FastAPI serves the built frontend on the same
// origin as the API - see api/app.py's StaticFiles mount).
import type * as T from './types'

// Captured once at module load (page load), not re-read live on every 401 -
// App.tsx's AuthRedirectHandler clears ?gate=denied from the URL shortly
// after mount (via history.replaceState), so a live window.location.search
// check inside request() could race: an in-flight query's 401 landing
// *after* that cleanup would no longer see gate=denied and would redirect
// straight back into EVE SSO, right back to another denial - a login loop.
// A one-shot flag captured at load time survives that cleanup.
const gateWasDeniedThisLoad = window.location.search.includes('gate=denied')

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

// A 401 only ever means "no valid access-gate session" (see
// api/app.py's AccessGateMiddleware - a no-op, so never actually returned,
// unless AccessConfig.access_gate_enabled is true) - every other failure
// mode in this app is a 400 (ActionError, via routers' _wrap) or a 422
// (Pydantic validation), never a bare 401, so repurposing this one status
// code for "go log in" doesn't collide with anything else. Skipped if the
// URL already carries ?gate=denied - that means we *just* came back from a
// failed gate login (not on the allowlist), and every query on that page
// (e.g. App.tsx's SdeFreshnessChecker, which runs on every route including
// Landing) would otherwise immediately 401 and bounce straight back into
// another login attempt before the user ever sees the "access denied" message.
async function request<TResp>(path: string, init?: RequestInit): Promise<TResp> {
  const resp = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (resp.status === 401 && !gateWasDeniedThisLoad) {
    // Plain fetch, not get()/request() - avoids recursing back into this
    // same function (this endpoint is exempt from the gate middleware, so
    // it can't itself 401, but there's no reason to depend on that).
    const { url } = await fetch('/api/auth/gate/start').then((r) => r.json())
    window.location.href = url
    return new Promise<TResp>(() => {})  // navigating away, never resolves
  }
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
const put = <TResp>(path: string, body?: unknown) =>
  request<TResp>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined })
const patch = <TResp>(path: string, body?: unknown) =>
  request<TResp>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined })
const del = <TResp>(path: string) => request<TResp>(path, { method: 'DELETE' })

// ------------------------------------------------------------------- auth
export const authApi = {
  start: (rolePrefix: string) => get<{ url: string }>(`/api/auth/${rolePrefix}/start`),
  // "gate" isn't valid here - its consent is tracked client-side (see
  // Landing.tsx), never via these two - the backend itself 400s if asked.
  consentStatus: (rolePrefix: string) => get<{ acknowledged: boolean }>(`/api/auth/${rolePrefix}/consent`),
  acknowledgeConsent: (rolePrefix: string) => post<{ acknowledged: boolean }>(`/api/auth/${rolePrefix}/consent`),
}

// -------------------------------------------------------------- access gate
export const gateApi = {
  status: () => get<T.GateStatus>('/api/gate/status'),
  logout: () => post<{ ok: boolean }>('/api/gate/logout'),
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
  historyTypeIds: () => get<T.HistoryTypeIdOption[]>('/api/trading/history/type-ids'),
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
  recategorizeShortlist: () => post<{ checked: number; recategorized: number }>('/api/trading/shortlist/recategorize'),
  refreshAndPruneCandidates: (safe = true) =>
    post<Record<string, unknown>>(`/api/trading/candidates/refresh-and-prune?safe=${safe}`),
  reconcileTrades: () => post<Record<string, unknown>>('/api/trading/trades/reconcile'),
  runPipeline: (safe = true, rebuildUniverse = false) =>
    post<Record<string, unknown>>(
      `/api/trading/pipeline/run?safe=${safe}&rebuild_universe=${rebuildUniverse}`,
    ),
  checkSellerUnlistedStock: () => post<T.UnlistedStockRow[]>('/api/trading/seller/unlisted-stock'),
  checkUndercut: () => post<T.UndercutRow[]>('/api/trading/seller/undercut'),

  // GitHub issue #46: buyer/seller are multi-character now, same pattern as
  // productionApi.producerCharacters/removeCharacter below.
  buyerCharacters: () => get<T.TradingCharacter[]>('/api/trading/buyer-characters'),
  sellerCharacters: () => get<T.TradingCharacter[]>('/api/trading/seller-characters'),
  removeCharacter: (roleKey: string) => del(`/api/trading/auth/character/${roleKey}`),

  transactionCharacters: () => get<T.TradingCharacter[]>('/api/trading/transaction-characters'),
  walletTransactions: (roleKey: string, lookbackDays?: number) =>
    get<T.WalletTransaction[]>(
      `/api/trading/wallet-transactions?role_key=${encodeURIComponent(roleKey)}` +
        (lookbackDays ? `&lookback_days=${lookbackDays}` : ''),
    ),
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
  shipMargins: () => get<T.ShipMarginRow[]>('/api/production/margins'),
  itemMargin: (itemName: string) => post<T.ShipMarginRow>('/api/production/margins/search', { item_name: itemName }),
  materialTree: (typeName: string, quantity: number) =>
    post<T.MaterialTreeNode>('/api/production/material-tree', { type_name: typeName, quantity }),
  searchAssetLocations: (itemName: string) =>
    post<T.AssetLocationSearchResult>('/api/production/asset-locations', { item_name: itemName }),
  jobs: () => get<T.IndustryJobRow[]>('/api/production/jobs'),
  slots: () => get<T.CharacterSlotRow[]>('/api/production/slots'),
  setCharacterSlotExcluded: (characterName: string, excluded: boolean) =>
    put<{ character_name: string; excluded: boolean }>(
      `/api/production/slots/${encodeURIComponent(characterName)}/excluded`, { excluded },
    ),
  producerCharacters: () => get<T.ProducerCharacter[]>('/api/production/producer-characters'),
  ownedBlueprints: () => get<T.OwnedBlueprintRow[]>('/api/production/blueprints'),
  manualBlueprintCopyCosts: () => get<T.ManualBlueprintCopyCostRow[]>('/api/production/blueprints/manual-copy-costs'),
  addManualBlueprintCopyCost: (itemName: string, purchaseCost: number, runs: number) =>
    post<{ type_id: number; type_name: string; purchase_cost: number; runs: number }>(
      '/api/production/blueprints/manual-copy-costs', { item_name: itemName, purchase_cost: purchaseCost, runs },
    ),
  removeManualBlueprintCopyCost: (typeId: number) =>
    del(`/api/production/blueprints/manual-copy-costs/${typeId}`),
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
  setSystem: (profile: string, systemId: number, systemName: string) =>
    post('/api/production/settings/systems', { profile, system_id: systemId, system_name: systemName }),
  allSolarSystems: () => get<T.SolarSystemOption[]>('/api/production/systems'),
  systemCostIndices: () => get<T.SystemCostIndices>('/api/production/settings/system-cost-indices'),
  itemNameOptions: () => get<T.SdeItemNameOption[]>('/api/production/sde/item-names'),
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
  distributionRecommendations: () => get<T.DistributionRow[]>('/api/production/logistics/distribution'),
  inventionLogistics: () => get<T.LogisticsRow[]>('/api/production/logistics/invention'),
  t1BpcInventionNeeds: () => get<T.T1BpcInventionNeedRow[]>('/api/production/invention/t1-bpc-needs'),
  structureNames: () => get<Record<string, string | null>>('/api/production/logistics/structure-names'),
  resolveStructureName: (locationId: number, force = false) =>
    post<{ location_id: number; name: string | null; cached: boolean }>(
      '/api/production/logistics/resolve-structure-name', { location_id: locationId, force },
    ),

  // No refreshSde() here, deliberately - moved to adminApi below (GitHub
  // issue #34): the SDE cache is global/shared, not per-tenant, so
  // triggering a refresh is a cross-tenant-impacting action.
  // No addCharacter() here, deliberately - Add Character uses the same
  // redirect-based EVE SSO flow buyer/seller login does (see
  // ProductionLayout.tsx: authApi.start('producer')), not a POST action.
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
  updateStockTarget: (typeId: number, updates: {
    backup_stock?: number | null
    home_market_stock?: number | null
    jita_market_stock?: number | null
  }) => patch<T.StockTarget>(`/api/production/stock-targets/${typeId}`, updates),
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

// -------------------------------------------------------------- doctrine
export const doctrineApi = {
  listDoctrines: () => get<T.DoctrineStatus[]>('/api/doctrine/doctrines'),
  createDoctrine: (name: string, description?: string | null) =>
    post<T.Doctrine>('/api/doctrine/doctrines', { name, description: description ?? null }),
  updateDoctrine: (doctrineId: string, req: { name?: string; description?: string | null; active?: boolean }) =>
    request<T.Doctrine>(`/api/doctrine/doctrines/${doctrineId}`, { method: 'PATCH', body: JSON.stringify(req) }),
  deleteDoctrine: (doctrineId: string) => del(`/api/doctrine/doctrines/${doctrineId}`),

  parseFitting: (rawEft: string) => post<T.ParsedFittingPreview>('/api/doctrine/fittings/parse', { raw_eft: rawEft }),
  addFitting: (doctrineId: string, req: {
    raw_eft: string
    name?: string | null
    variant_label?: string | null
    contract_target?: number
    stockpile_target?: number
    cargo_tolerance_pct?: number | null
    fuel_bay_text?: string | null
    ship_maintenance_bay_text?: string | null
  }) => post<{ fitting: T.Fitting; issues: T.DoctrineParseIssue[] }>(`/api/doctrine/doctrines/${doctrineId}/fittings`, req),
  updateFitting: (fittingId: string, req: {
    raw_eft?: string | null
    name?: string | null
    variant_label?: string | null
    contract_target?: number | null
    stockpile_target?: number | null
    cargo_tolerance_pct?: number | null
    active?: boolean | null
    fuel_bay_text?: string | null
    ship_maintenance_bay_text?: string | null
  }) =>
    request<{ fitting: T.Fitting; issues: T.DoctrineParseIssue[] }>(`/api/doctrine/fittings/${fittingId}`, {
      method: 'PATCH', body: JSON.stringify(req),
    }),
  deleteFitting: (fittingId: string) => del(`/api/doctrine/fittings/${fittingId}`),
  fittingDetail: (fittingId: string) => get<T.FittingDetail>(`/api/doctrine/fittings/${fittingId}`),

  syncContracts: () => post<T.DoctrineSyncReport>('/api/doctrine/sync'),
  validateContracts: () => post<{ revalidated: number }>('/api/doctrine/validate'),
  syncTime: () => get<{ synced_at: string | null }>('/api/doctrine/sync-time'),

  syncAssets: () => post<{ characters: Record<string, unknown>; corporations: Record<string, unknown> }>('/api/doctrine/assets/sync'),
  assetSyncTime: () => get<{ synced_at: string | null }>('/api/doctrine/assets/sync-time'),

  status: (doctrineId?: string) =>
    get<{ doctrines: T.DoctrineStatus[] }>(`/api/doctrine/status${doctrineId ? `?doctrine_id=${doctrineId}` : ''}`),
  contracts: (fittingId?: string, status?: string) => {
    const params = new URLSearchParams()
    if (fittingId) params.set('fitting_id', fittingId)
    if (status) params.set('status', status)
    const qs = params.toString()
    return get<T.DoctrineContractRow[]>(`/api/doctrine/contracts${qs ? `?${qs}` : ''}`)
  },
  contractHistory: () => get<T.ContractHistoryRow[]>('/api/doctrine/contracts/history'),
  stockpile: (doctrineId?: string) =>
    get<{ rows: T.StockpileRow[]; aggregated_rows: T.AggregatedStockpileRow[]; assets_available: boolean }>(
      `/api/doctrine/stockpile${doctrineId ? `?doctrine_id=${doctrineId}` : ''}`,
    ),
  shoppingList: (doctrineId?: string) =>
    get<{ rows: T.ShoppingListRow[] }>(`/api/doctrine/shopping-list${doctrineId ? `?doctrine_id=${doctrineId}` : ''}`),

  characters: () => get<T.DoctrineCharacter[]>('/api/doctrine/characters'),
  // No addCharacter() here, deliberately - see DoctrineLayout.tsx's
  // CharacterGroup, which uses the same redirect-based EVE SSO flow
  // buyer/seller login does (authApi.start('doctrine')), not a POST action.
  removeCharacter: (roleKey: string) => del(`/api/doctrine/characters/${roleKey}`),

  // Separate character group, authed for esi-assets scopes rather than
  // esi-contracts ones - see doctrine/esi_sync.py's own module docstring.
  // No addAssetCharacter() here either, same reasoning as above
  // (authApi.start('doctrine-assets')).
  assetCharacters: () => get<T.DoctrineCharacter[]>('/api/doctrine/asset-characters'),
  removeAssetCharacter: (roleKey: string) => del(`/api/doctrine/asset-characters/${roleKey}`),

  settings: () => get<T.DoctrineSettings>('/api/doctrine/settings'),
  updateSettings: (s: T.DoctrineSettings) => post<T.DoctrineSettings>('/api/doctrine/settings', s),
}

// ------------------------------------------------------------- ore & minerals
export const refiningApi = {
  shortlistSnapshot: () => get<T.OreShortlistRow[]>('/api/refining/shortlist/snapshot'),
  shortlistItems: () => get<T.OreShortlistItem[]>('/api/refining/shortlist/items'),
  addCandidates: () => post<{ added: number; already_tracked: number }>('/api/refining/shortlist/add-candidates'),
  refreshShortlist: () => post<Record<string, unknown>>('/api/refining/shortlist/refresh'),
  deactivateShortlistItems: (itemIds: number[]) =>
    post<{ deactivated: number }>('/api/refining/shortlist/deactivate', { item_ids: itemIds }),
  activateShortlistItems: (itemIds: number[]) =>
    post<{ activated: number }>('/api/refining/shortlist/activate', { item_ids: itemIds }),
  settings: () => get<T.RefiningSettings>('/api/refining/settings'),
  updateSettings: (s: T.RefiningSettings) => post<T.RefiningSettings>('/api/refining/settings', s),
  settingsOptions: () => get<{ structure_types: string[]; rig_tiers: string[]; implants: string[] }>(
    '/api/refining/settings/options',
  ),
  esiSyncTime: () => get<{ synced_at: string | null }>('/api/refining/esi/sync-time'),
  quoteReprocessing: (paste: string) =>
    post<T.ReprocessingQuoteResult>('/api/refining/reprocessing/quote', { paste }),
  // Mineral Shopping List (GitHub issue #93)
  refinableMinerals: () => get<T.RefinableMineral[]>('/api/refining/shopping-list/minerals'),
  mineralRequirements: () => get<T.MineralRequirement[]>('/api/refining/shopping-list/requirements'),
  saveMineralRequirements: (requirements: T.MineralRequirement[]) =>
    post<{ saved: number }>('/api/refining/shopping-list/requirements', { requirements }),
  optimizeShoppingList: (requirements?: T.MineralRequirement[]) =>
    post<T.ShoppingListPlan>('/api/refining/shopping-list/optimize', { requirements: requirements ?? null }),
}

// -------------------------------------------------------------- station trading
export const stationTradingApi = {
  shortlist: () => get<T.StationTradingShortlistRow[]>('/api/station-trading/shortlist'),
  refreshShortlist: () =>
    post<{ discovered: number; rows: T.StationTradingShortlistRow[] }>('/api/station-trading/shortlist/refresh'),
  deactivateShortlistItems: (typeIds: number[]) =>
    post<{ deactivated: number }>('/api/station-trading/shortlist/deactivate', { type_ids: typeIds }),
  activateShortlistItems: (typeIds: number[]) =>
    post<{ activated: number }>('/api/station-trading/shortlist/activate', { type_ids: typeIds }),
  checkUndercut: () => post<T.StationTradingUndercutCheckResult>('/api/station-trading/undercut/check'),
  skills: () => get<T.SkillSummary[]>('/api/station-trading/skills'),
  settings: () => get<T.StationTradingSettings>('/api/station-trading/settings'),
  updateSettings: (s: T.StationTradingSettings) =>
    post<T.StationTradingSettings>('/api/station-trading/settings', s),
  esiSyncTime: () => get<{ synced_at: string | null }>('/api/station-trading/esi/sync-time'),

  // No addCharacter() here, deliberately - Add Character uses the same
  // redirect-based EVE SSO flow every other role does (authApi.start('trader')).
  traderCharacters: () => get<T.ProducerCharacter[]>('/api/station-trading/trader-characters'),
  removeCharacter: (roleKey: string) => del(`/api/station-trading/auth/character/${roleKey}`),
}

// ------------------------------------------------------------------- admin
export const adminApi = {
  tenants: () => get<T.AdminTenant[]>('/api/admin/tenants'),
  users: () => get<T.AdminUser[]>('/api/admin/users'),
  addUser: (characterName: string) =>
    post<{ character_id: number; character_name: string; tenant_id: string }>(
      '/api/admin/users', { character_name: characterName },
    ),
  removeUser: (characterId: number) => del(`/api/admin/users/${characterId}`),
  setToolGrants: (characterId: number, toolKeys: string[]) =>
    put<{ character_id: number; tool_keys: string[] }>(
      `/api/admin/users/${characterId}/tools`, { tool_keys: toolKeys },
    ),
  // GitHub issue #34: moved here from productionApi - the SDE cache is
  // global/shared across every tenant, so triggering a refresh is a
  // cross-tenant-impacting action, not a per-tenant Production one.
  refreshSde: () => post<Record<string, number>>('/api/admin/sde/refresh'),
  // Same cross-tenant-cache reasoning as refreshSde above - see
  // production/jita_price_cache.py's own docstring.
  refreshJitaPriceCache: () =>
    post<{ cached_type_ids: number; updated_at: string | null }>('/api/admin/jita-price-cache/refresh'),
  // GitHub issue #88 - cross-tenant, same reasoning as everything else here.
  errors: (limit = 200) => get<T.ErrorLogRow[]>(`/api/admin/errors?limit=${limit}`),
}

// ------------------------------------------------------------------ errors
// GitHub issue #88 - self-hosted error tracking. Deliberately its own tiny
// export (not folded into adminApi above) since report() is called from
// ErrorBoundary.tsx/main.tsx's global error listeners on every page, for
// every tenant - unlike adminApi.errors (the list view), it needs no
// "admin" tool grant (see api/routers/errors.py's own docstring).
export const errorsApi = {
  report: (source: string, message: string, detail?: string, path?: string) =>
    post<{ recorded: boolean }>('/api/errors', { source, message, detail, path }),
}
