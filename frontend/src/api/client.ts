// Thin typed fetch wrapper - one function per backend route (see
// eve_trader/api/routers/*.py). Relative /api/... paths work both in dev
// (Vite proxies to localhost:8000, see vite.config.ts) and in the built
// "single process" mode (FastAPI serves the built frontend on the same
// origin as the API - see api/app.py's StaticFiles mount).
import type * as T from './types'

// Captured once at module load (page load), not re-read live on every 401 —
// App.tsx's AuthRedirectHandler clears ?gate= from the URL shortly after
// mount, so a live window.location.search check inside request() could race:
// an in-flight query's 401 landing *after* that cleanup would no longer see
// the gate result and would redirect straight back into EVE SSO. A one-shot
// flag captured at load time survives that cleanup. Covers denied, pending,
// rejected, suspended, and an affiliation error — all of them are "we just
// came back from the gate login, don't immediately start another one".
const _gateResult = new URLSearchParams(window.location.search).get('gate')
const gateLoginFinishedThisLoad = _gateResult === 'denied'
  || _gateResult === 'pending'
  || _gateResult === 'rejected'
  || _gateResult === 'suspended'
  || _gateResult === 'error'

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
// URL already carries a terminal ?gate= result - that means we *just* came
// back from the gate login, and every query on that page (e.g. App.tsx's
// SdeFreshnessChecker, which runs on every route including Landing) would
// otherwise immediately 401 and bounce straight back into another login
// attempt before the user ever sees the message.
async function request<TResp>(path: string, init?: RequestInit): Promise<TResp> {
  const resp = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (resp.status === 401 && !gateLoginFinishedThisLoad) {
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
// Identity-only gate login. Prefix /api/auth/{role_prefix}/start is gone
// (Phase 9) — ESI tokens are re-authorized from the Characters page.
export const authApi = {
  start: () => get<{ url: string }>('/api/auth/gate/start'),
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
  walletDivisionOptions: () => get<{ wallet_division_ids: number[] }>(
    '/api/trading/settings/wallet-division-options',
  ),
  esiSyncTime: () => get<{ synced_at: string | null }>('/api/trading/esi/sync-time'),

  buildUniverse: () => post<{ count: number }>('/api/trading/universe/build'),
  buildFocused: () => post<{ count: number }>('/api/trading/universe/focus'),
  findNewCandidates: (safe = true) =>
    post<{ evaluated: number; recommended: number }>(`/api/trading/candidates/find-new?safe=${safe}`),
  addToShortlist: () => post<{ added: number }>('/api/trading/shortlist/add-new'),
  refreshShortlist: () => post<T.PipelineRunStatus>('/api/trading/shortlist/refresh'),
  recategorizeShortlist: () => post<{ checked: number; recategorized: number }>('/api/trading/shortlist/recategorize'),
  startRefreshAndPrune: (safe = true) =>
    post<T.PipelineRunStatus>(`/api/trading/candidates/refresh-and-prune?safe=${safe}`),
  refreshAndPruneStatus: () =>
    get<T.PipelineRunStatus>('/api/trading/candidates/refresh-and-prune/status'),
  reconcileTrades: () => post<Record<string, unknown>>('/api/trading/trades/reconcile'),
  runPipeline: (safe = true, rebuildUniverse = false) =>
    post<T.PipelineRunStatus>(
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
  walletBalance: (roleKey: string) =>
    get<T.WalletBalance>(`/api/trading/wallet-balance?role_key=${encodeURIComponent(roleKey)}`),
}

// ------------------------------------------------------------- production
export const productionApi = {
  sdeCounts: () => get<Record<string, number>>('/api/production/sde/counts'),
  sdeFreshness: () => get<T.SdeFreshness>('/api/production/sde/freshness'),
  stockTargets: () => get<T.StockTarget[]>('/api/production/stock-targets'),
  manualStock: () => get<Record<string, number>>('/api/production/manual-stock'),
  manualStockEntries: () => get<T.ManualStockEntry[]>('/api/production/manual-stock/entries'),
  manualBuildBuy: () => get<Record<string, string>>('/api/production/manual-build-buy'),
  selectedDecryptors: () => get<Record<string, string>>('/api/production/selected-decryptors'),
  plan: () => get<T.ProductionPlan | null>('/api/production/plan'),
  assetPlan: () => get<T.AssetPlan | null>('/api/production/asset-plan'),
  marketStatus: () => get<T.MarketStatusRow[]>('/api/production/market-status'),
  stockValue: () => get<{ total_value: number; priced_items: number; unpriced_items: number }>('/api/production/stock-value'),
  checkUnlistedStock: () => post<T.ProductionUnlistedStockRow[]>('/api/production/unlisted-stock/check'),
  // Background job (2026-09-13, Phase 2 of the button-transparency pass) -
  // the scan can walk up to ~19,400 SDE items on a cold cache, genuinely
  // slow enough to warrant progress reporting instead of a blocking
  // spinner. Start returns {run_id, status: running} immediately; poll
  // discoverBuildCandidatesStatus, then fetch the real rows via
  // buildCandidates once status flips to succeeded (same "job result is a
  // signal to re-fetch, not the payload" shape as the other background jobs).
  discoverBuildCandidates: (topN = 200) => post<T.PipelineRunStatus>(`/api/production/build-candidates/discover?top_n=${topN}`),
  discoverBuildCandidatesStatus: () => get<T.PipelineRunStatus>('/api/production/build-candidates/discover/status'),
  buildCandidates: (topN = 200) => get<T.BuildCandidate[]>(`/api/production/build-candidates?top_n=${topN}`),
  shipMargins: () => get<T.ShipMarginRow[]>('/api/production/margins'),
  itemMargin: (itemName: string) => post<T.ShipMarginRow>('/api/production/margins/search', { item_name: itemName }),
  materialTree: (typeName: string, quantity: number) =>
    post<T.MaterialTreeNode>('/api/production/material-tree', { type_name: typeName, quantity }),
  searchAssetLocations: (itemName: string) =>
    post<T.AssetLocationSearchResult>('/api/production/asset-locations', { item_name: itemName }),
  jobs: () => get<T.IndustryJobRow[]>('/api/production/jobs'),
  // Manual industry jobs (docs/MANUAL_TRACKING_PLAN.md phase 6) - rows come
  // back mixed into jobs() above (source: 'manual'); these are the write
  // endpoints for that subset. Exactly one of quantity/runs.
  addManualIndustryJob: (req: {
    item_name: string
    quantity: number | null
    runs: number | null
    location_id: number
    ready_at: string | null
  }) => post<{ manual_id: number }>('/api/production/manual-jobs', req),
  updateManualIndustryJob: (manualId: number, req: {
    quantity: number | null
    runs: number | null
    location_id: number | null
    ready_at: string | null
  }) => patch<{ manual_id: number }>(`/api/production/manual-jobs/${manualId}`, req),
  removeManualIndustryJob: (manualId: number) => del(`/api/production/manual-jobs/${manualId}`),
  completeManualIndustryJob: (manualId: number, locationId: number | null) =>
    post<{ manual_id: number; location_id: number }>(
      `/api/production/manual-jobs/${manualId}/complete`, { location_id: locationId },
    ),
  slots: () => get<T.CharacterSlotRow[]>('/api/production/slots'),
  setCharacterSlotExcluded: (characterName: string, excluded: boolean) =>
    put<{ character_name: string; excluded: boolean }>(
      `/api/production/slots/${encodeURIComponent(characterName)}/excluded`, { excluded },
    ),
  producerCharacters: () => get<T.ProducerCharacter[]>('/api/production/producer-characters'),
  ownedBlueprints: () => get<T.OwnedBlueprintRow[]>('/api/production/blueprints'),
  // Manual owned blueprints (docs/MANUAL_TRACKING_PLAN.md phase 5) - rows
  // come back mixed into ownedBlueprints() above (source: 'manual'); these
  // are the write endpoints for that subset.
  addManualOwnedBlueprint: (req: {
    item_name: string
    is_original: boolean
    material_efficiency: number
    time_efficiency: number
    runs: number | null
    quantity: number
    location_id: number
  }) => post<{ manual_id: number }>('/api/production/manual-blueprints', req),
  updateManualOwnedBlueprint: (manualId: number, req: {
    material_efficiency: number
    time_efficiency: number
    runs: number | null
    quantity: number
  }) => patch<{ manual_id: number }>(`/api/production/manual-blueprints/${manualId}`, req),
  removeManualOwnedBlueprint: (manualId: number) =>
    del(`/api/production/manual-blueprints/${manualId}`),
  manualBlueprintCopyCosts: () => get<T.ManualBlueprintCopyCostRow[]>('/api/production/blueprints/manual-copy-costs'),
  addManualBlueprintCopyCost: (itemName: string, purchaseCost: number, runs: number) =>
    post<{ type_id: number; type_name: string; purchase_cost: number; runs: number }>(
      '/api/production/blueprints/manual-copy-costs', { item_name: itemName, purchase_cost: purchaseCost, runs },
    ),
  updateManualBlueprintCopyCost: (typeId: number, purchaseCost: number, runs: number) =>
    put<{ type_id: number; purchase_cost: number; runs: number }>(
      `/api/production/blueprints/manual-copy-costs/${typeId}`, { purchase_cost: purchaseCost, runs },
    ),
  removeManualBlueprintCopyCost: (typeId: number) =>
    del(`/api/production/blueprints/manual-copy-costs/${typeId}`),
  manualBlueprintMeTeOverrides: () => get<T.ManualBlueprintMeTeOverrideRow[]>('/api/production/blueprints/manual-me-te-overrides'),
  addManualBlueprintMeTeOverride: (itemName: string, materialEfficiency: number, timeEfficiency: number) =>
    post<{ type_id: number; type_name: string; material_efficiency: number; time_efficiency: number }>(
      '/api/production/blueprints/manual-me-te-overrides',
      { item_name: itemName, material_efficiency: materialEfficiency, time_efficiency: timeEfficiency },
    ),
  updateManualBlueprintMeTeOverride: (typeId: number, materialEfficiency: number, timeEfficiency: number) =>
    put<{ type_id: number; material_efficiency: number; time_efficiency: number }>(
      `/api/production/blueprints/manual-me-te-overrides/${typeId}`,
      { material_efficiency: materialEfficiency, time_efficiency: timeEfficiency },
    ),
  removeManualBlueprintMeTeOverride: (typeId: number) =>
    del(`/api/production/blueprints/manual-me-te-overrides/${typeId}`),
  settings: () => get<T.ProductionSettings>('/api/production/settings'),
  updateSettings: (s: T.ProductionSettings) => post<T.ProductionSettings>('/api/production/settings', s),
  structureOptions: () => get<{ structure_types: string[]; rig_tiers: string[]; hangar_division_flags: string[] }>(
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
  categoryCostIndexOverrides: () => get<Record<string, number>>('/api/production/logistics/cost-index-overrides'),
  setCategoryCostIndexOverride: (category: string, value: number) =>
    post('/api/production/logistics/cost-index-overrides', { category, value }),
  clearCategoryCostIndexOverride: (category: string) =>
    del(`/api/production/logistics/cost-index-overrides/${encodeURIComponent(category)}`),
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
  structureSystemIds: () => get<Record<string, number | null>>('/api/production/logistics/structure-system-ids'),
  resolveStructureName: (locationId: number, force = false) =>
    post<{ location_id: number; name: string | null; cached: boolean }>(
      '/api/production/logistics/resolve-structure-name', { location_id: locationId, force },
    ),

  // LocationPicker (docs/MANUAL_TRACKING_PLAN.md phase 2) - type-ahead
  // across NPC stations, this tenant's own resolved structures and its own
  // manual names.
  searchLocations: (query: string) =>
    get<T.LocationSearchRow[]>(`/api/production/locations/search?q=${encodeURIComponent(query)}`),
  setManualLocationName: (locationId: number, name: string) =>
    post<{ location_id: number; name: string }>(
      '/api/production/locations/manual-names', { location_id: locationId, name },
    ),
  removeManualLocationName: (locationId: number) =>
    del(`/api/production/locations/manual-names/${locationId}`),

  // No previewSde() here, deliberately - moved to adminApi below (GitHub
  // issue #34): the SDE cache is global/shared, not per-tenant, so
  // triggering a refresh is a cross-tenant-impacting action.
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
  setManualStock: (typeId: number, count: number, locationId = 0) =>
    post('/api/production/manual-stock', { type_id: typeId, count, location_id: locationId }),
  // Manual stock table (docs/MANUAL_TRACKING_PLAN.md phase 3, decision 9) -
  // separate per-(item, location) entries, as opposed to setManualStock's
  // own single per-type total above.
  addManualStockEntry: (itemName: string, count: number, locationId: number) =>
    post<T.ManualStockEntry>('/api/production/manual-stock/entries', {
      item_name: itemName, count, location_id: locationId,
    }),
  removeManualStockEntry: (typeId: number, locationId: number) =>
    del(`/api/production/manual-stock/entries/${typeId}/${locationId}`),
  // Manual listed stock (docs/MANUAL_TRACKING_PLAN.md phase 7).
  manualListedStock: () => get<T.ManualListedStockEntry[]>('/api/production/manual-listed-stock'),
  setManualListedStock: (typeId: number, market: 'home' | 'jita', quantity: number) =>
    post<{ type_id: number; market: string; quantity: number }>(
      '/api/production/manual-listed-stock', { type_id: typeId, market, quantity },
    ),
  clearManualListedStock: (typeId: number, market: 'home' | 'jita') =>
    del(`/api/production/manual-listed-stock/${typeId}/${market}`),
  // Asset paste (docs/MANUAL_TRACKING_PLAN.md phase 4) - commit re-parses
  // the same text server-side, never takes preview's own rows back.
  previewAssetPaste: (text: string, locationId: number, mode: 'replace' | 'merge') =>
    post<T.AssetPastePreviewResult>('/api/production/manual-stock/paste/preview', {
      text, location_id: locationId, mode,
    }),
  commitAssetPaste: (text: string, locationId: number, mode: 'replace' | 'merge') =>
    post<T.AssetPasteCommitResult>('/api/production/manual-stock/paste/commit', {
      text, location_id: locationId, mode,
    }),
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

  specialOrders: (status?: 'open' | 'done') =>
    get<T.SpecialOrder[]>(`/api/production/special-orders${status ? `?status=${status}` : ''}`),
  createSpecialOrder: (req: { items: { type_id?: number; name?: string; quantity: number }[]; note?: string | null; net_against_stock?: boolean }) =>
    post<{ order_id: string; item_count: number }>('/api/production/special-orders', req),
  getSpecialOrder: (orderId: string) => get<T.SpecialOrderDetail>(`/api/production/special-orders/${orderId}`),
  updateSpecialOrder: (orderId: string, updates: { status?: string | null; note?: string | null; net_against_stock?: boolean | null }) =>
    patch<T.SpecialOrderDetail>(`/api/production/special-orders/${orderId}`, updates),
  removeSpecialOrder: (orderId: string) => del(`/api/production/special-orders/${orderId}`),
  setSpecialOrderItem: (orderId: string, typeId: number, quantity: number, recompute = false) =>
    put<T.SpecialOrderDetail | T.SpecialOrderPreviewResult>(
      `/api/production/special-orders/${orderId}/items${recompute ? '?recompute=true' : ''}`,
      { type_id: typeId, quantity },
    ),
  removeSpecialOrderItem: (orderId: string, typeId: number, recompute = false) =>
    del<T.SpecialOrderDetail | T.SpecialOrderPreviewResult>(
      `/api/production/special-orders/${orderId}/items/${typeId}${recompute ? '?recompute=true' : ''}`,
    ),
  computeSpecialOrder: (orderId: string) =>
    post<T.SpecialOrderComputeResult>(`/api/production/special-orders/${orderId}/compute`),
  computeCombinedSpecialOrders: (orderIds: string[], netAgainstStock: boolean) =>
    post<T.SpecialOrderComputeResult>('/api/production/special-orders/combine/compute',
      { order_ids: orderIds, net_against_stock: netAgainstStock }),
  auditSpecialOrders: () => get<{ ok: boolean; issues: T.SpecialOrderAuditIssue[] }>('/api/production/special-orders/audit'),
  specialOrderEvents: (orderId?: string) =>
    get<{ rows: T.SpecialOrderEventRow[] }>(
      orderId ? `/api/production/special-orders/${orderId}/events` : '/api/production/special-orders/events',
    ),
}

// ------------------------------------------------------------- portfolio
export const portfolioApi = {
  overview: () => get<T.PortfolioOverview>('/api/portfolio/overview'),
  history: (days?: number) =>
    get<T.PortfolioSnapshotRow[]>(days == null ? '/api/portfolio/history' : `/api/portfolio/history?days=${days}`),
  wealth: () => get<T.TotalWealth>('/api/portfolio/wealth'),
  manualPrices: () => get<T.ManualItemPriceRow[]>('/api/portfolio/manual-prices'),
  setManualPrice: (itemName: string, price: number) =>
    post<{ type_id: number; type_name: string; price: number }>(
      '/api/portfolio/manual-prices', { item_name: itemName, price },
    ),
  removeManualPrice: (typeId: number) => del(`/api/portfolio/manual-prices/${typeId}`),
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

  syncContracts: () => post<T.PipelineRunStatus>('/api/doctrine/sync'),
  syncContractsStatus: () => get<T.PipelineRunStatus>('/api/doctrine/sync/status'),
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
  removeCharacter: (roleKey: string) => del(`/api/doctrine/characters/${roleKey}`),

  // Separate character group - see doctrine/esi_sync.py's module docstring.
  // Add/re-auth lives on the Characters page, not a second SSO start here.
  assetCharacters: () => get<T.DoctrineCharacter[]>('/api/doctrine/asset-characters'),
  removeAssetCharacter: (roleKey: string) => del(`/api/doctrine/asset-characters/${roleKey}`),

  settings: () => get<T.DoctrineSettings>('/api/doctrine/settings'),
  updateSettings: (s: T.DoctrineSettings) => post<T.DoctrineSettings>('/api/doctrine/settings', s),
  hangarDivisionOptions: () => get<{ hangar_division_flags: string[] }>(
    '/api/doctrine/settings/hangar-division-options',
  ),
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
  // global/shared across every tenant, so triggering a preview/apply is a
  // cross-tenant-impacting action, not a per-tenant Production one.
  previewSde: () => post<T.PipelineRunStatus>('/api/admin/sde/preview'),
  previewSdeStatus: () => get<T.PipelineRunStatus>('/api/admin/sde/preview/status'),
  applySde: () => post<T.SdeApplyResult>('/api/admin/sde/apply'),
  // Same cross-tenant-cache reasoning as previewSde above - see
  // production/jita_price_cache.py's own docstring.
  refreshJitaPriceCache: () =>
    post<{ cached_type_ids: number; updated_at: string | null }>('/api/admin/jita-price-cache/refresh'),
  // GitHub issue #88 - cross-tenant, same reasoning as everything else here.
  errors: (limit = 200) => get<T.ErrorLogRow[]>(`/api/admin/errors?limit=${limit}`),
  // Moved from portfolioApi (confirmed real misplacement 2026-09-21, see
  // admin.do_create_backup's own docstring) - one pg_dump already covers
  // every tenant's data in one shot, same cross-tenant-impacting reasoning
  // as everything else here.
  backups: () => get<T.BackupInfo[]>('/api/admin/backups'),
  createBackup: () => post<T.BackupInfo>('/api/admin/backups'),
  // docs/MANUAL_TRACKING_PLAN.md phase 2, question 1 - Default-Tenant-only
  // operator switch (bulk admin resolution and its own UI section are
  // phase 8, not part of this).
  structureResolutionFallback: () =>
    get<{ global_structure_resolution_fallback: boolean }>('/api/admin/structures/fallback'),
  setStructureResolutionFallback: (enabled: boolean) =>
    put<{ global_structure_resolution_fallback: boolean }>('/api/admin/structures/fallback', { enabled }),
  // docs/MANUAL_TRACKING_PLAN.md phase 8 - bulk structure-name resolution,
  // same "starts a background job, poll status separately" shape as
  // previewSde/previewSdeStatus above.
  startStructureNameResolve: (force: boolean) =>
    post<T.PipelineRunStatus>('/api/admin/structures/resolve', { force }),
  structureResolveStatus: () => get<T.PipelineRunStatus>('/api/admin/structures/resolve/status'),
  allowlist: () => get<T.AllowlistEntry[]>('/api/admin/allowlist'),
  searchAllowlist: (q: string) =>
    get<T.AllowlistCandidate[]>(`/api/admin/allowlist/search?q=${encodeURIComponent(q)}`),
  allowlistImpact: (entryType: string, entryId: number, action: 'add' | 'remove') =>
    get<T.AllowlistImpact>(
      `/api/admin/allowlist/impact?entry_type=${encodeURIComponent(entryType)}&entry_id=${entryId}&action=${action}`,
    ),
  addAllowlistEntry: (entryType: string, entryId: number) =>
    post<T.AllowlistEntry>('/api/admin/allowlist', { entry_type: entryType, entry_id: entryId }),
  removeAllowlistEntry: (entryType: string, entryId: number) =>
    del(`/api/admin/allowlist/${encodeURIComponent(entryType)}/${entryId}`),
  accessRequests: (status?: string) =>
    get<T.AccessRequestRow[]>(
      status ? `/api/admin/access-requests?status=${encodeURIComponent(status)}` : '/api/admin/access-requests',
    ),
  approveAccessRequest: (characterId: number, toolKeys: string[]) =>
    post<{ character_id: number; tenant_id: string; tool_keys: string[] }>(
      `/api/admin/access-requests/${characterId}/approve`, { tool_keys: toolKeys },
    ),
  rejectAccessRequest: (characterId: number) =>
    post<{ character_id: number; status: string }>(`/api/admin/access-requests/${characterId}/reject`),
  deleteAccessRequest: (characterId: number) =>
    del(`/api/admin/access-requests/${characterId}`),
  refreshAffiliations: () => post<{ updated: number }>('/api/admin/users/refresh-affiliations'),
}

// -------------------------------------------------------------- characters
export const charactersApi = {
  owners: () => get<T.EsiTokenCharacter[]>('/api/characters/owners'),
  removeCharacter: (characterId: number) =>
    del<T.EsiRemovedCharacter>(`/api/characters/owners/${characterId}`),
  sharing: (toolKey?: string) =>
    get<T.EsiSharingRow[]>(toolKey ? `/api/characters/sharing?tool_key=${encodeURIComponent(toolKey)}` : '/api/characters/sharing'),
  freshness: () => get<T.EsiFreshnessRow[]>('/api/characters/freshness'),
  capabilities: () => get<T.EsiCapabilityRow[]>('/api/characters/capabilities'),
  setSharing: (body: {
    owner_type: string
    owner_id: number
    data_kind: string
    tool_key: string
    enabled: boolean
  }) => post<T.EsiSharingRow & { enabled: boolean }>('/api/characters/sharing', body),
  setCapability: (body: {
    character_id: number
    capability_key: string
    enabled: boolean
  }) => post<T.EsiCapabilityRow & { enabled: boolean }>('/api/characters/capabilities', body),
  accessPreview: (characterId: number, extraKinds: string[] = []) =>
    get<T.AccessPreview>(
      `/api/characters/access-preview?character_id=${characterId}`
      + (extraKinds.length ? `&extra_kinds=${encodeURIComponent(extraKinds.join(','))}` : ''),
    ),
  reauthStart: (characterId: number, extraKinds: string[] = []) =>
    get<{ url: string }>(
      `/api/characters/reauth/start?character_id=${characterId}`
      + (extraKinds.length ? `&extra_kinds=${encodeURIComponent(extraKinds.join(','))}` : ''),
    ),
  addStart: () => get<{ url: string }>('/api/characters/add/start'),
  sync: (toolKey?: string) =>
    post<Record<string, unknown>>(
      toolKey ? `/api/characters/sync?tool_key=${encodeURIComponent(toolKey)}` : '/api/characters/sync',
    ),
  checkCorporationRoles: () =>
    post<T.CorporationRoleCheckResult>('/api/characters/corporation-roles/check'),
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

// ---------------------------------------------------------------- sorting
export const sortingApi = {
  sortingList: () => get<T.SortingList>('/api/sorting/sorting-list'),
  intakeSources: () => get<T.SortingIntakeSourceList>('/api/sorting/intake-sources'),
  addIntakeSource: (body: {
    source_kind: string
    hangar_flag: string
    owner_name?: string | null
    label?: string | null
  }) => post<T.SortingIntakeSource>('/api/sorting/intake-sources', body),
  removeIntakeSource: (id: number) => del<{ removed: number }>(`/api/sorting/intake-sources/${id}`),
  availableCharacters: () => get<{ characters: string[] }>('/api/sorting/available-characters'),
  availableCorps: () => get<{ corps: string[] }>('/api/sorting/available-corps'),
  hangarDivisionOptions: () => get<{ hangar_division_flags: string[] }>(
    '/api/sorting/hangar-division-options',
  ),
}
