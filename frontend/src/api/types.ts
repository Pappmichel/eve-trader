// Mirrors eve_trader/api/schemas.py 1:1 - keep field names/optionality in sync
// with the backend when either side changes.

export interface Candidate {
  item: string
  type_id: number
  volume_m3: number
  category: string
  market_group_path: string
  meta_level: number | null
}

export interface ShortlistItem {
  item: string
  item_id: number
  category: string
  volume_m3: number
  active: boolean
  meta_level: number | null
}

export interface ShortlistRow {
  item: string
  category: string
  landed_cost: number | null
  net_sell: number | null
  sell_volume: number | null
  own_orders_remaining: number
  profit_per_unit: number | null
  margin: number | null
  profit_per_m3: number | null
  decision: string
  active: boolean
  item_id: number
  volume_m3: number
  jita_sell: number | null
  import_cost: number | null
  meta_level: number | null
  days_until_deactivation: number | null
  // Highest hub buy price that still breaks even (GitHub issue #221)
  breakeven_buy_price: number | null
  // Real average daily *market-wide* traded quantity (GitHub issue #100,
  // Goonmetrics region history for C-J's own home region) - what
  // "Profit / Day" is actually computed from, NOT sell_volume (order-book
  // depth) and NOT this trader's own sales. null means Goonmetrics has no
  // history for this item in that region.
  avg_daily_volume: number | null
}

export interface NewCandidateResult {
  item: string
  category: string
  type_id: number
  volume_m3: number
  paired_days: number
  profitable_days: number
  hit_rate: number
  latest_margin: number
  best_margin: number
  avg_profit_m3: number
  avg_sell_movement: number
  score: number
  recommendation: string
  add: boolean
  meta_level: number | null
}

export interface RealizedTrade {
  type_id: number
  item: string
  buy_date: string
  buy_qty: number
  buy_unit_price: number
  sell_date: string
  sell_qty: number
  sell_unit_price: number
  matched_qty: number
  realized_profit: number
  margin: number
}

export interface WalletTransaction {
  transaction_id: number
  date: string
  type_id: number
  item: string
  is_buy: boolean
  quantity: number
  unit_price: number
  total: number
  location_id: number
  location_name: string | null
}

export interface WalletBalance {
  role_key: string
  balance: number
}

export interface UnlistedStockRow {
  type_id: number
  item: string
  asset_quantity: number
  sell_order_remaining: number
  unlisted_quantity: number
  sell_volume: number | null
  margin: number | null
}

export interface UndercutRow {
  type_id: number
  item: string
  my_price: number
  competitor_price: number
  difference: number
}

export interface MarginTrend {
  recent_avg_margin: number
  baseline_avg_margin: number
  trend_pct: number
}

export interface HistoryTypeIdOption {
  type_id: number
  type_name: string
}

export interface SdeItemNameOption {
  type_id: number
  type_name: string
}

// One row of the shared per-hub freight table (#222); null = no entry, the
// tool's own freight value applies.
export interface HubFreightRow {
  region_id: number
  hub: string
  freight_cost_per_m3: number | null
}

export interface RegionOption {
  region_id: number
  region_name: string
}

export interface SolarSystemOption {
  solar_system_id: number
  solar_system_name: string
}

export interface SystemCostIndexPair {
  manufacturing: number
  reaction: number
}

export interface SystemCostIndices {
  component: SystemCostIndexPair | null
  manufacturing: SystemCostIndexPair | null
}

export interface PriceHistoryPoint {
  date: string
  min_price: number
  max_price: number
  avg_price: number
  movement: number
  num_orders: number
}

// Buy hub and reference region as separate series (never mixed into one line).
export interface PriceHistory {
  hub_region_id: number
  reference_region_id: number
  hub: PriceHistoryPoint[]
  reference: PriceHistoryPoint[]
}

/** [date, avg_price] points of the last 30 calendar days, per region. */
export interface SparklineSeries {
  hub: [string, number][]
  ref: [string, number][]
}

export interface TradingSettings {
  import_cost_per_m3: number
  structure_sell_haircut: number
  min_profit_threshold: number
  min_margin_threshold: number
  skip_grace_period_days: number
  enforce_shortlist_cap: boolean
  max_active_shortlist_items: number
  max_shortlist_growth_per_run: number
  min_hit_rate: number
  min_avg_movement: number
  min_paired_days: number
  excluded_path_prefixes: string[]
  safe_mode_max_ids: number
  lookback_days: number
  jita_region_id: number
  reference_region_id: number
  structure_id: number | null
  structure_market_slug: string | null
  buyer_character_name: string | null
  seller_character_name: string | null
  wallet_division_ids: number[]
  esi_frequent_interval_hours: number
  esi_normal_interval_hours: number
  esi_rare_interval_hours: number
  esi_stale_clear_multiples: number
  scheduler_enabled: boolean
}

export interface PipelineRunProgress {
  phase?: string
  batch?: number
  total_batches?: number
  evaluated?: number
  skipped?: number
  refreshed?: number
  message?: string
}

export interface PipelineRunStatus {
  run_id: string | null
  job_name?: string
  tool?: string
  status: 'idle' | 'running' | 'succeeded' | 'failed' | 'degraded'
  started_at?: string | null
  updated_at?: string | null
  finished_at?: string | null
  progress?: PipelineRunProgress | null
  result?: Record<string, unknown> | null
  error?: string | null
}

export interface TradingKpis {
  shortlist_count: number
  import_candidates: number
  own_sell_orders: number
  new_recommendations: number
}

export interface ProductionKpis {
  stock_targets: number
  active_jobs: number
  open_special_orders: number
}

// ------------------------------------------------------------ production
// LocationPicker (docs/MANUAL_TRACKING_PLAN.md phase 2) - a search hit from
// GET /production/locations/search, same three kinds storage.search_locations
// combines (the global structure cache is deliberately not searchable, see
// that function's own docstring).
export interface LocationSearchRow {
  location_id: number
  name: string
  kind: 'station' | 'structure' | 'manual'
}

// Manual stock table (docs/MANUAL_TRACKING_PLAN.md phase 3, decision 9) -
// one row per (type_id, location_id).
// docs/MANUAL_TRACKING_PLAN.md phase 7.
export interface ManualListedStockEntry {
  type_id: number
  market: 'home' | 'jita'
  quantity: number
  updated_at: string
}

export interface ManualStockEntry {
  type_id: number
  type_name: string
  location_id: number
  count: number
}

// Asset paste (docs/MANUAL_TRACKING_PLAN.md phase 4).
export interface AssetPastePreviewRow {
  type_id: number
  name: string
  old: number
  new: number
  status: 'new' | 'changed' | 'unchanged' | 'removed'
}
export interface AssetPasteUnresolvedLine {
  line: string
  suggestion: string | null
}
export interface AssetPasteError {
  line: string
  error: string
}
export interface AssetPastePreviewResult {
  rows: AssetPastePreviewRow[]
  skipped_blueprints: string[]
  unresolved: AssetPasteUnresolvedLine[]
  errors: AssetPasteError[]
}
export interface AssetPasteCommitResult {
  applied: number
  skipped_blueprints: string[]
  unresolved: AssetPasteUnresolvedLine[]
  errors: AssetPasteError[]
}

export interface StockTarget {
  type_id: number
  type_name: string
  backup_stock: number
  home_market_stock: number | null
  jita_market_stock: number | null
}

export interface InventoryRow {
  type_id: number
  type_name: string
  activity: string
  backup_stock: number
  current_stock: number
  total_missing: number
}

export interface BuyListEntry {
  type_id: number
  type_name: string
  quantity: number
  unit_price: number | null
  total_price: number | null
  on_hand_pct: number
  buy_from: string | null
  hub_region_id?: number | null
  hub_name?: string | null
  category: string | null
}

export interface BuildJobEntry {
  type_id: number
  type_name: string
  blueprint_type_id: number
  activity: string
  quantity: number
  job_runs: number
  job_time_seconds: number
  unit_build_cost: number | null
  decryptor: string | null
  tech_level: string | null
  job_category: string | null
  job_cost: number | null
  margin: number | null
  recipe_source: string | null
}

export interface LogisticsRow {
  category: string
  location_id: number
  type_id: number
  type_name: string
  needed: number
  available: number
  missing: number
  pull_from_location_id: number | null
  pull_from_available: number | null
  volume_m3: number
}

export interface DistributionRow {
  type_id: number
  type_name: string
  from_location_id: number
  to_category: string
  to_location_id: number
  quantity: number
  volume_m3: number
}

export interface MarketRestockRow {
  type_id: number
  type_name: string
  from_location_id: number
  from_location_name: string | null
  quantity: number
  volume_m3: number
  home_target: number
  home_listed: number
  home_unlisted: number
  home_short: number
}

export interface MarketStatusRow {
  type_id: number
  type_name: string
  backup_target: number
  backup_current: number
  home_target: number | null
  home_listed: number
  jita_target: number | null
  jita_listed: number
}

export interface InventionResult {
  t1_blueprint_type_id: number
  t1_blueprint_name: string
  product_type_id: number
  product_name: string
  decryptor: string
  probability: number
  output_runs: number
  datacore_cost: number
  decryptor_cost: number
  relic_cost: number
  total_attempt_cost: number
  expected_cost_per_success: number | null
  expected_cost_per_run: number | null
  me: number
  te: number
  // T3-05 (2026-09-26): null when reducible_material_cost couldn't price
  // every material, rather than a silent (understated) 0.
  material_savings_per_run: number | null
  net_cost_per_run: number | null
}

export interface AssetPlanBlocker {
  type_id: number
  type_name: string
  needed: number
  covered: number
}

export interface AssetPlanJob {
  type_id: number
  type_name: string
  blueprint_type_id: number
  activity: string
  quantity: number
  job_runs: number
  runs_ready_now: number
  job_time_seconds: number
  unit_build_cost: number | null
  decryptor: string | null
  tech_level: string | null
  job_category: string | null
  margin: number | null
  stock_coverage: number | null
  unlock_time_seconds: number
  recommended_slots: number | null
  days_to_complete_at_recommended_slots: number | null
  recipe_source: string | null
  blockers: AssetPlanBlocker[]
}

export interface AssetPlan {
  jobs: AssetPlanJob[]
}

export interface SpecialOrder {
  order_id: string
  note: string | null
  net_against_stock: boolean
  status: string // "open" | "done"
  created_at: string | null
  item_count: number
}

export interface SpecialOrderLineItem {
  type_id: number
  type_name: string
  quantity: number
}

export interface StockOverlapWarningRow {
  type_id: number
  type_name: string
  current_stock: number
}

export interface SpecialOrderComputeResult {
  line_items: SpecialOrderLineItem[]
  buy_list: BuyListEntry[]
  build_list: BuildJobEntry[]
  invention_list: InventionNeedRow[]
  stock_overlap_warning: StockOverlapWarningRow[]
  // T2-02 (business-logic audit, 2026-09-25/26): false means ESI's
  // adjusted-price fetch failed this run, so every build_list row's
  // job_cost - and this page's own Total Job Cost/Gesamt sum - silently
  // reads 0 rather than its real value.
  adjusted_prices_available: boolean
}

export interface SpecialOrderDetail {
  order: SpecialOrder
  items: SpecialOrderLineItem[]
}

export interface SpecialOrderPreviewResult extends SpecialOrderDetail {
  plan: SpecialOrderComputeResult
}

export interface SpecialOrderAuditIssue {
  kind: string
  order_id: string
  detail: string
}

export interface SpecialOrderEventRow {
  event_id: string
  order_id: string
  event: string
  detail: string | null
  at: string | null
}

export interface InventionNeedRow {
  type_id: number
  type_name: string
  t1_blueprint_type_id: number
  t1_blueprint_name: string
  decryptor: string
  probability: number
  output_runs: number
  runs_needed: number
  bpcs_needed: number
  recommended_invention_runs: number
  t2_bpc_owned: number
  t2_bpc_in_progress: number
  stockpile_pct: number
  bpc_target_runs: number
  t1_bpc_target_runs: number
}

export interface T1BpcInventionNeedRow {
  type_id: number
  name: string
  needed: number
  available: number
  missing: number
  bpo_present: boolean
  stockpile_pct: number
}

export interface IndustryJobRow {
  job_id: number
  type_name: string
  activity: string
  runs: number
  quantity: number | null
  status: string
  start_date: string | null
  end_date: string | null
  remaining_seconds: number | null
  installer_name: string
  output_value: number | null
  // docs/MANUAL_TRACKING_PLAN.md phase 6.
  source: 'esi' | 'manual'
  manual_id: number | null
}

export interface CharacterSlotRow {
  character_name: string
  job_type: string
  total_slots: number
  used_slots: number
  free_slots: number
  excluded_from_planning: boolean
}

export interface OwnedBlueprintRow {
  type_id: number
  type_name: string
  is_original: boolean
  quantity: number
  material_efficiency: number
  time_efficiency: number
  runs: number | null
  // docs/MANUAL_TRACKING_PLAN.md phase 5.
  source: 'esi' | 'manual'
  manual_id: number | null
  location_id: number | null
}

export interface ManualBlueprintCopyCostRow {
  type_id: number
  type_name: string
  purchase_cost: number
  runs: number
  cost_per_run: number
}

export interface ManualBlueprintMeTeOverrideRow {
  type_id: number
  type_name: string
  material_efficiency: number
  time_efficiency: number
}

export interface AlchemyComparison {
  product_type_id: number
  product_type_name: string
  normal_isk_per_hour: number | null
  alchemy_isk_per_hour: number | null
  alchemy_unrefined_type_id: number
  alchemy_unrefined_type_name: string
  scrapmetal_yield_pct: number
}

export interface BuildCandidate {
  type_id: number
  type_name: string
  activity: string
  build_cost: number
  margin: number
  daily_movement: number
  potential_daily_profit: number
  meta_level: number | null
  alchemy_comparison?: AlchemyComparison | null
}

export interface ShipMarginRow {
  type_id: number
  type_name: string
  activity: string
  home_price: number | null
  jita_price: number | null
  build_cost: number | null
  margin_home: number | null
  margin_jita: number | null
  meta_level: number | null
}

export interface MaterialTreeNode {
  type_id: number
  type_name: string
  quantity: number
  activity: string
  decryptor: string | null
  children: MaterialTreeNode[]
}

export interface AssetLocationRow {
  location_id: number
  location_name: string | null
  owner_name: string
  quantity: number
}

export interface AssetLocationSearchResult {
  type_id: number
  type_name: string
  locations: AssetLocationRow[]
}

export interface ProductionUnlistedStockRow {
  type_id: number
  type_name: string
  stock_quantity: number
  sell_volume: number | null
  margin: number | null
}

export interface PortfolioOverview {
  trading_realized_profit: number
  trading_average_margin: number
  trading_daily_profit_volatility: number | null
  trading_trade_count: number
  production_stock_value: number
  production_stock_targets_configured: boolean
  combined_value: number
}

export interface PortfolioSnapshotRow {
  snapshot_date: string
  trading_realized_profit: number
  trading_average_margin: number
  trading_daily_profit_volatility: number | null
  trading_trade_count: number
  production_stock_value: number
  production_stock_targets_configured: boolean
  combined_value: number
  total_wealth: number | null
  wealth_assets_value: number | null
  wealth_wallet_balance: number | null
}

export interface CharacterMissingWalletScope {
  character_id: number
  character_name: string
}

export interface TotalWealth {
  total_wealth: number | null
  wealth_assets_value: number | null
  wealth_blueprints_value: number | null
  wealth_wallet_balance: number | null
  wealth_priced_items: number
  wealth_unpriced_items: number
  characters_missing_wallet_scope: CharacterMissingWalletScope[]
}

export interface ManualItemPriceRow {
  type_id: number
  type_name: string
  price: number
  updated_at: string
}

export interface ProductionPlan {
  inventory: InventoryRow[]
  buy_list: BuyListEntry[]
  build_list: BuildJobEntry[]
  invention_list: InventionNeedRow[]
  // T2-02 (business-logic audit, 2026-09-25/26): false means ESI's
  // adjusted-price fetch failed this run, so every BuildJobEntry.job_cost
  // above silently reads 0 rather than its real value.
  adjusted_prices_available: boolean
}

export interface ProductionSettings {
  component_overbuild: number
  bpc_inventory: number
  market_fees: number
  // jita_buy_broker_fee removed 2026-09-26 (T3-04) - TradingConfig's own
  // copy is the single source of truth now, edited from Trading Settings.
  min_margin: number
  min_daily_profit: number
  haul_cost_per_m3: number
  hub_region_id: number
  facility_tax_rate: number
  home_market: string | null
  home_location_id: number | null
  distribution_source_location_id: number | null
  invention_location_id: number | null
  stock_hangar_flags: string[]
  reaction_structure_type: string
  reaction_rig_tier: string
  component_structure_type: string
  component_rig_tier: string
  supercapital_structure_type: string
  supercapital_rig_tier: string
  manufacturing_structure_type: string
  manufacturing_rig_tier: string
  encryption_skill_level: number
  datacore_skill_1_level: number
  datacore_skill_2_level: number
  reaction_cost_index_override: number | null
  component_cost_index_override: number | null
  manufacturing_cost_index_override: number | null
  asset_plan_slot_days_target: number | null
  alchemy_reactions_enabled: boolean
}

export interface ProducerCharacter {
  role_key: string
  character_id: number
  character_name: string
}

export interface SdeFreshness {
  local_refreshed_at: string | null
  remote_check_succeeded: boolean
  newer_sde_available: boolean
  trading_universe_stale: boolean
  trading_universe_built_at: string | null
}

// GitHub issue #46: buyer/seller are multi-character now (see
// ProducerCharacter above, same shape) - AuthStatus's old single
// buyer/seller-name-or-null shape is gone.
export type TradingCharacter = ProducerCharacter

export interface GateStatus {
  enabled: boolean
  logged_in: boolean
  character_name: string | null
  tools: string[]
  suspended: boolean
  /** Pending access-request count. Null unless this session holds admin. */
  pending_access_requests: number | null
}

export interface AllowlistEntry {
  entry_type: 'corporation' | 'alliance'
  entry_id: number
  name: string
  added_at: string | null
  added_by_character_id: number | null
}

export interface AllowlistCandidate {
  type: 'corporation' | 'alliance'
  id: number
  name: string
}

export interface AllowlistImpactUser {
  character_id: number
  character_name: string | null
  corporation_id: number | null
  alliance_id: number | null
}

export interface AllowlistImpact {
  would_suspend: AllowlistImpactUser[]
  activates_recheck: boolean
  disables_recheck: boolean
  actor_exempt_but_affected: boolean
}

export interface AccessRequestRow {
  character_id: number
  character_name: string
  corporation_id: number
  corporation_name: string | null
  alliance_id: number | null
  alliance_name: string | null
  status: string
  requested_at: string | null
  last_login_at: string | null
  decided_at: string | null
  decided_by_character_id: number | null
  no_longer_allowlisted: boolean
}

export interface AccessPreviewItem {
  key: string
  label: string
  group: number
  added: boolean
}

export interface AccessPreview {
  title: string
  items: AccessPreviewItem[]
}

export interface EsiSharingRow {
  owner_type: string
  owner_id: number
  data_kind: string
  tool_key: string
}

export interface EsiFreshnessRow {
  owner_type: string
  owner_id: number
  data_kind: string
  last_success_at: string | null
  last_attempt_at: string | null
  last_error: string | null
}

export interface EsiCapabilityRow {
  character_id: number
  capability_key: string
}

export interface EsiTokenCharacter {
  character_id: number
  character_name: string
  write_role: string
  character_has_token_pool: boolean
  roles: string[]
  /** Known gap 2 (docs/ESI_ACCESS_PLAN.md) - null if the live lookup failed. */
  corporation_id: number | null
  /** Resolved display name for corporation_id above - null if the live lookup failed. */
  corporation_name: string | null
}

/** DELETE /api/characters/owners/{character_id} — tokens dropped; sharing/capabilities kept. */
export interface EsiRemovedCharacter {
  removed: number
  character_name: string
  roles: string[]
  shared_tools: string[]
  capabilities: string[]
  mail_archive_deleted: { headers: number; messages: number } | null
}

/** Known gap 2's role warning - POST /api/characters/corporation-roles/check. */
export interface CorporationRoleCheck {
  corporation_id: number
  checked_characters: string[]
  unchecked_characters: string[]
  data_kinds: Record<string, { required_roles: string[]; has_role: boolean | null }>
}

export interface CorporationRoleCheckResult {
  corporations: CorporationRoleCheck[]
}

export interface AdminTenant {
  tenant_id: string
  name: string
  created_at: string | null
}

export interface AdminUser {
  character_id: number
  character_name: string | null
  tenant_id: string
  tenant_name: string
  tool_keys: string[]
  corporation_id: number | null
  corporation_name: string | null
  alliance_id: number | null
  alliance_name: string | null
  affiliation_checked_at: string | null
  access_suspended: boolean
}

export interface SdeDiffItem {
  /** Present on item rows. Other tables use `key` (a string, composite keys joined with ":"). */
  type_id?: number
  key?: string
  name: string
}

export interface SdeChangedItem {
  type_id?: number
  key?: string
  name: string
  changes: Record<string, [unknown, unknown]>
}

export interface SdeBlueprintMaterialChange {
  material_type_id: number
  name: string
  old_qty: number
  new_qty: number
}

export interface SdeQtyChange {
  old_qty: number
  new_qty: number
}

export interface SdeValueChange {
  old: number | null
  new: number | null
}

export interface SdeChangedBlueprint {
  blueprint_type_id: number
  product_type_id: number
  product_name: string
  materials: SdeBlueprintMaterialChange[]
  products: SdeQtyChange | null
  time: SdeValueChange | null
  invention_probability: SdeValueChange | null
}

export interface SdeTableRowDiff {
  new: SdeDiffItem[]
  removed: SdeDiffItem[]
  changed: SdeChangedItem[]
  // Entries per list that were cut off (only sde_pi_planets caps its lists).
  truncated?: { new: number; removed: number; changed: number }
}

export interface SdeDiff {
  new_items: SdeDiffItem[]
  removed_items: SdeDiffItem[]
  changed_items: SdeChangedItem[]
  changed_blueprints: SdeChangedBlueprint[]
  other_tables: Record<string, SdeTableRowDiff>
}

export type SdeApplyResult = Record<string, number>

export interface ErrorLogRow {
  id: number
  tenant_id: string | null
  source: string
  message: string
  detail: string | null
  path: string | null
  created_at: string | null
}

export interface BackupInfo {
  name: string
  created_at: string
  size_bytes: number
}

// ------------------------------------------------------------------ doctrine
export interface Doctrine {
  doctrine_id: string
  name: string
  description: string | null
  active: boolean
  created_at: string | null
}

export interface Fitting {
  fitting_id: string
  doctrine_id: string
  name: string
  hull_type_id: number
  raw_eft: string
  variant_label: string | null
  contract_target: number
  stockpile_target: number
  cargo_tolerance_pct: number | null
  active: boolean
  created_at: string | null
  updated_at: string | null
  fuel_bay_text: string | null
  ship_maintenance_bay_text: string | null
}

export interface DoctrineFittingItem {
  line_no: number
  slot_section: string
  type_id: number
  type_name: string
  quantity: number
  is_offline: boolean
}

export interface DoctrineParseIssue {
  line_no: number
  raw_line: string
  issue_kind: string
  message: string
}

export interface ParsedFittingPreview {
  hull_type_id: number
  hull_name: string
  fit_name: string
  items: DoctrineFittingItem[]
  issues: DoctrineParseIssue[]
}

export interface DoctrineDeviation {
  contract_id: number
  type_id: number
  type_name: string
  kind: string
  severity: string
  expected_qty: number
  actual_qty: number
}

export interface DoctrineContractRow {
  contract_id: number
  source_role: string
  for_corporation: boolean
  status: string
  validation_status: string
  issuer_id: number | null
  start_location_id: number | null
  title: string | null
  price: number | null
  date_expired: string | null
  matched_fitting_id: string | null
  match_score: number | null
  synced_at: string | null
  source_character_name: string | null
  hull_type_id: number | null
  hull_name: string | null
}

export interface DoctrineContractWithDeviations extends DoctrineContractRow {
  deviations: DoctrineDeviation[]
}

export interface ContractHistoryRow {
  contract_id: number
  source_role: string
  fitting_id: string | null
  fitting_name: string | null
  hull_type_id: number | null
  hull_name: string | null
  title: string | null
  price: number | null
  acceptor_id: number | null
  acceptor_name: string | null
  date_issued: string | null
  date_completed: string | null
  source_character_name: string | null
}

export interface StockpileRow {
  fitting_id: string
  fitting_name: string
  doctrine_id: string
  doctrine_name: string
  type_id: number
  type_name: string
  slot_section: string
  required_total: number
  available: number
  shortfall: number
  severity: string | null
}

export interface AggregatedStockpileRow {
  type_id: number
  type_name: string
  required_total: number
  available: number
  shortfall: number
  severity: string | null
  fitting_count: number
}

export interface ShoppingListRow {
  type_id: number
  type_name: string
  shortfall: number
  build_cost: number | null
  cj_price: number | null
  jita_landed_price: number | null
  recommended_source: 'Build' | 'C-J' | 'Jita' | null
  total_cost: number | null
  hub_region_id: number | null
  hub_name: string | null
}

export interface FittingStatus {
  fitting_id: string
  fitting_name: string
  doctrine_id: string
  contract_status: 'green' | 'yellow' | 'red' | 'gray'
  valid_contracts: number
  tolerable_contracts: number
  contract_target: number
  stockpile_status: 'green' | 'yellow' | 'red' | 'gray'
  stockpile_target: number
  worst_stockpile_shortfall_pct: number
  last_synced_at: string | null
  assets_available: boolean
  hull_type_id: number
  hull_name: string
  multibuy_cost: number | null
}

export interface DoctrineStatus {
  doctrine_id: string
  doctrine_name: string
  overall: 'green' | 'yellow' | 'red' | 'gray'
  contract_rollup: 'green' | 'yellow' | 'red' | 'gray'
  stockpile_rollup: 'green' | 'yellow' | 'red' | 'gray'
  fittings: FittingStatus[]
}

export interface FittingDetail {
  fitting: Fitting
  items: DoctrineFittingItem[]
  issues: DoctrineParseIssue[]
  contracts: DoctrineContractWithDeviations[]
  status: FittingStatus
}

export interface DoctrineSyncReport {
  characters: Record<string, unknown>
  contracts_synced: number
  contracts_dropped_this_run: number
  corp_errors: Record<string, string>
  item_fetch_errors: Record<string, string>
}

export interface DoctrineSettings {
  doctrine_structure_id: number | null
  stockpile_location_id: number | null
  cargo_tolerance_pct: number
  strict_extras: boolean
  import_cost_per_m3: number
  hub_region_id: number
  stockpile_hangar_flags: string[]
}

export interface DoctrineCharacter {
  role_key: string
  character_id: number
  character_name: string
}

// --------------------------------------------------------- ore & minerals
export interface OreShortlistItem {
  item_id: number
  item: string
  family: string
  is_ice: boolean
  active: boolean
}

export interface OreShortlistRow {
  item_id: number
  item: string
  family: string
  is_ice: boolean
  active: boolean
  volume_m3: number | null
  landed_cost: number | null
  yield_pct: number | null
  mineral_value: number | null
  refining_tax: number | null
  net_sell: number | null
  sell_listed_qty: number | null
  profit_per_unit: number | null
  margin: number | null
  profit_per_m3: number | null
  decision: string
  // Hub the ore was priced at; the winning hub when the tool is set to "All hubs" (GitHub issue #222).
  hub_region_id: number | null
  hub_name: string | null
  // Highest hub buy price per unit that still breaks even after broker fee, freight, structure sale haircut and refining tax.
  breakeven_buy_price: number | null
}

export interface RefinableMineral {
  type_id: number
  name: string
}

export interface MineralRequirement {
  type_id: number
  name: string | null
  required_qty: number
}

export interface OrePurchase {
  type_id: number
  item: string
  family: string
  is_ice: boolean
  portions: number
  units: number
  volume_m3: number
  landed_cost_per_unit: number
  total_cost: number
  hub_region_id: number | null
  hub_name: string | null
}

export interface DirectMineralPurchase {
  type_id: number
  name: string
  quantity: number
  landed_cost_per_unit: number
  total_cost: number
  // "Home" or the name of the trade hub it is bought at (e.g. "Jita", "Amarr").
  source: string | null
  hub_region_id: number | null
}

export interface MineralCoverage {
  type_id: number
  name: string
  required: number
  from_ore: number
  from_direct: number
  delivered: number
  surplus: number
}

export interface ShoppingListPlan {
  ore_purchases: OrePurchase[]
  direct_purchases: DirectMineralPurchase[]
  coverage: MineralCoverage[]
  ore_cost: number
  direct_cost: number
  total_cost: number
  lp_cost: number
  all_direct_cost: number | null
  savings_vs_all_direct: number | null
  total_volume_m3: number
}

export interface RefiningSettings {
  hub_region_id: number
  structure_type: string
  rig_tier: string
  security_status: number
  implant: string
  reprocessing_skill_level: number
  reprocessing_efficiency_skill_level: number
  ore_family_skill_levels: Record<string, number>
  scrapmetal_processing_skill_level: number
  refining_tax_rate: number
}

export interface ReprocessingQuoteRow {
  name: string
  quantity: number
  type_id: number | null
  category: string
  sell_as_is_value: number | null
  refined_value: number | null
  mineral_value: number | null
  refining_tax: number | null
  decision: string
  error: string | null
}

export interface ReprocessingQuoteTotals {
  reprocess_count: number
  total_mineral_value: number
  total_refined_value: number
  total_sell_as_is_value: number
  total_batch_value_optimal: number
}

export interface ReprocessingMineralTotal {
  type_id: number
  name: string
  quantity: number
  unit_sell_price: number | null
  value: number | null
}

export interface ReprocessingQuoteResult {
  rows: ReprocessingQuoteRow[]
  totals: ReprocessingQuoteTotals
  mineral_totals: ReprocessingMineralTotal[]
  priced_via_fallback: boolean
}

// ------------------------------------------------------------ station trading
export interface StationTradingSettings {
  station_id: number
  hub_region_id: number
  broker_fee_rate: number
  sales_tax_rate: number
  min_spread_threshold: number
  min_daily_volume: number
  enforce_shortlist_cap: boolean
  max_active_shortlist_items: number
}

export interface StationTradingShortlistRow {
  type_id: number
  name: string
  category: string
  spread_pct: number
  avg_daily_volume: number
  discovered_at: string
  active: boolean
  live_buy: number | null
  live_sell: number | null
  profit_per_unit: number | null
  margin: number | null
  profit_per_day: number | null
}

export interface StationTradingUndercutRow {
  type_id: number
  name: string
  my_price: number
  competitor_price: number
  difference: number
}

export interface StationTradingUndercutCheckResult {
  sell: StationTradingUndercutRow[]
  buy: StationTradingUndercutRow[]
}

export interface SkillSummary {
  character_name: string
  levels: Record<string, number> | null
  order_slots: number | null
  error: string | null
}

// ---------------------------------------------------------------- sorting
export interface ToolDemandRow {
  tool: string
  wanted_qty: number
}

export interface SortingSourceQty {
  source_label: string
  qty: number
}

export interface SortingRow {
  type_id: number
  type_name: string
  intake_qty: number
  by_source: SortingSourceQty[]
  wanted_by_tool: ToolDemandRow[]
  unclaimed: boolean
}

export interface SortingList {
  rows: SortingRow[]
}

export interface SortingIntakeSource {
  id: number
  source_kind: string
  owner_name: string | null
  hangar_flag: string
  label: string | null
}

export interface SortingIntakeSourceList {
  sources: SortingIntakeSource[]
}

// ---------------------------------------------------- module reprocessing
export interface ModuleShortlistItem {
  item_id: number
  item: string
  active: boolean
}

export interface ModuleShortlistRow {
  item_id: number
  item: string
  active: boolean
  volume_m3: number | null
  landed_cost: number | null
  yield_pct: number | null
  mineral_value: number | null
  refining_tax: number | null
  net_sell: number | null
  sell_listed_qty: number | null
  profit_per_unit: number | null
  margin: number | null
  profit_per_m3: number | null
  decision: string
}

// Module Reprocessing's Mineral Shopping List - reuses MineralRequirement,
// DirectMineralPurchase and RefinableMineral above as-is.
export interface ReprocessPurchase {
  type_id: number
  item: string
  category: 'ore' | 'module'
  family: string | null
  is_ice: boolean
  portions: number
  units: number
  volume_m3: number
  landed_cost_per_unit: number
  total_cost: number
  hub_region_id: number | null
  hub_name: string | null
}

export interface ReprocessMineralCoverage {
  type_id: number
  name: string
  required: number
  from_reprocessing: number
  from_direct: number
  delivered: number
  surplus: number
}

export interface ModuleShoppingListPlan {
  reprocess_purchases: ReprocessPurchase[]
  direct_purchases: DirectMineralPurchase[]
  coverage: ReprocessMineralCoverage[]
  reprocess_cost: number
  direct_cost: number
  total_cost: number
  lp_cost: number
  all_direct_cost: number | null
  savings_vs_all_direct: number | null
  total_volume_m3: number
}

export interface ModuleReprocessingSettings {
  scrapmetal_processing_skill_level: number
  refining_tax_rate: number
  freight_cost_per_m3: number
  min_profit_threshold: number
  min_margin_threshold: number
  ignore_thresholds: boolean
  purchase_region_id: number
  purchase_structure_id: number | null
  input_hub_region_id: number
  enforce_shortlist_cap: boolean
  max_active_shortlist_items: number
}


// ---------------------------------------------- Character Info (Character Management)
// Mirrors eve_trader/character_management/info_actions.py. Every optional
// field is a `CharInfoField`: the `state` says why `value` is missing.
export type CharInfoFieldState = 'ok' | 'not_shared' | 'reauth_needed' | 'not_synced' | 'error'

export interface CharInfoField<T> {
  state: CharInfoFieldState
  value: T | null
  detail?: string
  /** Snapshot fields only: when the last successful sync ran. */
  synced_at?: string
}

export interface CharInfoLocation {
  solar_system_id: number | null
  solar_system_name: string | null
  location_id: number | null
  location_kind: 'structure' | 'station' | 'space'
  location_name: string | null
}

export interface CharInfoShip {
  ship_type_id: number | null
  ship_type_name: string | null
  ship_name: string | null
}

export interface CharInfoOnline {
  online: boolean
  last_login?: string
  last_logout?: string
  logins?: number
}

export interface CharInfoStandingRow { from_id: number; name: string; standing: number }
export interface CharInfoStandings {
  faction: CharInfoStandingRow[]
  npc_corp: CharInfoStandingRow[]
  agent: CharInfoStandingRow[]
}
export interface CharInfoFatigue {
  jump_fatigue_expire_date: string | null
  last_jump_date: string | null
  last_update_date: string | null
}
export interface CharInfoLoyaltyRow { corporation_id: number; corporation_name: string; loyalty_points: number }
export interface CharInfoImplant { type_id: number; name: string }
export interface CharInfoJumpClone {
  jump_clone_id: number
  name: string | null
  location_id: number | null
  location_type: string | null
  location_name: string | null
  implants: CharInfoImplant[]
}
export interface CharInfoClones {
  home: { location_id: number; location_type: string | null; location_name: string | null } | null
  jump_clones: CharInfoJumpClone[]
  last_clone_jump_date: string | null
  last_station_change_date: string | null
  /** last_clone_jump_date + 24 h (the base cooldown; skills can shorten it) */
  clone_jump_available_at: string | null
}
export interface CharInfoCorpHistoryRow { corporation_id: number; corporation_name: string; start_date: string | null }

export interface CharInfoCharacter {
  character_id: number
  character_name: string | null
  corporation_id: number | null
  corporation_name: string | null
  alliance_id: number | null
  alliance_name: string | null
  security_status: number | null
  birthday: string | null
  wallet_balance: CharInfoField<number>
  location: CharInfoField<CharInfoLocation>
  ship: CharInfoField<CharInfoShip>
  online: CharInfoField<CharInfoOnline>
  fatigue?: CharInfoField<CharInfoFatigue>
  freshness: Record<string, { last_success_at: string | null; last_attempt_at: string | null; last_error: string | null }>
  // Detail only:
  standings?: CharInfoField<CharInfoStandings>
  loyalty_points?: CharInfoField<CharInfoLoyaltyRow[]>
  clones?: CharInfoField<CharInfoClones>
  implants?: CharInfoField<CharInfoImplant[]>
  corporation_history?: CharInfoCorpHistoryRow[]
}

export interface CharInfoOverview { characters: CharInfoCharacter[] }

export interface CharInfoSyncResult {
  ok: boolean
  characters: Record<string, unknown>
  /** Owners another sync pass was already running for (not an error). */
  in_flight: number[]
  failed: { owner_id: number; name: string | null; error: string | null }[]
}


// ------------------------------------------------- Skills (Character Management)
// Mirrors eve_trader/character_management/skills_actions.py. Fields reuse the
// same CharInfoField state wrapper as Character Info.
export interface SkillAttributes {
  charisma: number
  intelligence: number
  memory: number
  perception: number
  willpower: number
  bonus_remaps: number | null
  last_remap_date: string | null
  accrued_remap_cooldown_date: string | null
}

export interface SkillsSummary {
  total_sp: number | null
  unallocated_sp: number | null
  /** Upper bound: extractors' worth of SP above the 5,000,000 SP floor. */
  extractable_estimate: number | null
  attributes: SkillAttributes | null
}

export interface SkillQueueEntry {
  queue_position: number
  skill_id: number
  name: string
  finished_level: number
  start_date: string | null
  finish_date: string | null
  training_start_sp: number | null
  level_start_sp: number | null
  level_end_sp: number | null
}

export interface SkillQueue {
  entries: SkillQueueEntry[]
  length: number
  empty: boolean
  /** Entries exist but none carries a finish date. */
  paused: boolean
  current: SkillQueueEntry | null
  ends_at: string | null
  /** The queue guard (phase 5a): why this queue needs attention, if it does. */
  warning: SkillQueueWarning | null
}

export type SkillQueueWarningKind = 'empty' | 'paused' | 'ended' | 'ends_soon'

export interface SkillQueueWarning {
  kind: SkillQueueWarningKind
  hours_left: number | null
}

export interface SkillsWarnings {
  count: number
  characters: (SkillQueueWarning & { character_id: number; character_name: string })[]
  queue_warning_hours: number
}

export interface SkillRow {
  skill_id: number
  name: string
  /** null until an SDE refresh has filled sde_skill_meta. */
  rank: number | null
  active_level: number
  trained_level: number
  skillpoints: number
  sp_to_level_v: number | null
}

export interface SkillGroup {
  group_id: number | null
  group_name: string
  skills: SkillRow[]
  total_sp: number
  maxed: number
}

export interface SkillsOverviewRow {
  character_id: number
  character_name: string
  summary: CharInfoField<SkillsSummary>
  queue: CharInfoField<SkillQueue>
  freshness: Record<string, { last_success_at: string | null; last_attempt_at: string | null; last_error: string | null }>
}

export interface SkillsOverview { characters: SkillsOverviewRow[] }

export interface CharacterSkills {
  character_id: number
  character_name: string
  summary: CharInfoField<SkillsSummary>
  skills: CharInfoField<SkillGroup[]>
  queue: CharInfoField<SkillQueue>
  freshness: SkillsOverviewRow['freshness']
}

export interface SkillMatrixSkill {
  skill_id: number
  name: string
  /** keyed by str(character_id) */
  levels: Record<string, { active: number; trained: number }>
}

export interface SkillMatrix {
  characters: { character_id: number; character_name: string }[]
  hidden_characters: { character_id: number; character_name: string }[]
  reauth_needed: number[]
  groups: { group_id: number | null; group_name: string; skills: SkillMatrixSkill[] }[]
}


// Mirrors eve_trader/character_management/skill_check.py.
export interface DoctrineMissingSkill {
  skill_id: number
  name: string
  needed: number
  have: number
  sp_remaining: number | null
}

export interface DoctrineCheckCharacter {
  character_id: number
  can_fly: boolean
  missing: DoctrineMissingSkill[]
  /** estimate from current attributes; null when unknown */
  train_seconds: number | null
}

export interface DoctrineCheckFitting {
  fitting_id: string
  name: string
  variant_label: string | null
  doctrine_id: string
  doctrine_name: string | null
  hull_type_id: number
  hull_name: string | null
  required_skills: number
  characters: DoctrineCheckCharacter[]
}

export interface DoctrineCheck {
  sde_ready: boolean
  fittings: DoctrineCheckFitting[]
  characters: { character_id: number; character_name: string }[]
  hidden_characters: { character_id: number; character_name: string }[]
}


// Mirrors eve_trader/character_management/skill_plan_actions.py.
export interface SkillPlanSummary {
  plan_id: number
  name: string
  description: string
  created_at: string | null
  updated_at: string | null
  step_count: number
}
export interface SkillPlanStep {
  position: number
  skill_id: number
  level: number
  level_label: string
  name: string
  group_name: string | null
  rank: number | null
}
export interface SkillPlan {
  plan_id: number
  name: string
  description: string
  created_at: string | null
  updated_at: string | null
  steps: SkillPlanStep[]
  sde_ready: boolean
  added?: number
  removed?: number
  unresolved?: string[]
  steps_added_for_prerequisites?: number
}
export interface SkillPlanProgressStep { skill_id: number; level: number; level_label: string; name: string; sp_remaining: number | null }
export interface SkillPlanProgressRow {
  character_id: number
  character_name: string
  synced: boolean
  steps_total?: number
  steps_done?: number
  sp_remaining?: number | null
  train_seconds?: number | null
  next_steps?: SkillPlanProgressStep[]
}
export interface SkillPlanProgress {
  plan_id: number
  characters: SkillPlanProgressRow[]
  hidden_characters: { character_id: number; character_name: string }[]
}

// Mirrors eve_trader/character_management/contacts_actions.py.
export interface ContactRow {
  contact_id: number
  name: string
  contact_type: string
  standing: number
  is_blocked: boolean
  is_watched: boolean
  labels: string[]
}
export interface ContactsValue { contacts: ContactRow[]; labels: string[] }
export interface CalendarEvent {
  event_id: number
  title: string
  event_date: string | null
  importance: number | null
  response: string | null
}
export interface CalendarEventDetail {
  event_id: number
  title: string | null
  date: string | null
  duration: number | null
  importance: number | null
  owner_name: string | null
  owner_type: string | null
  response: string | null
  text: string | null
}
export interface ContactsCharacter {
  character_id: number
  character_name: string
  contacts: CharInfoField<null>
  calendar: CharInfoField<null>
}

export interface WalletJournalValue {
  window_days: number
  entries: { id: number | null; date: string; ref_type: string | null; amount: number; balance: number | null; description: string | null }[]
  total_entries: number
  truncated: boolean
  income: number
  expense: number
  by_type: { ref_type: string; total: number }[]
}

// Mirrors eve_trader/character_management/notification_actions.py.
export interface NotificationItem {
  character_id: number
  character_name: string | null
  notification_id: number
  type: string
  category: string
  sent_at: string | null
  summary: string
  read: boolean
  read_in_game: boolean
  sender_id: number | null
  sender_type: string | null
}

export interface NotificationsList {
  items: NotificationItem[]
  total: number
  unread_total: number
  types: { type: string; label: string; count: number }[]
  categories: { category: string; label: string; count: number }[]
  characters: { character_id: number; character_name: string; synced_at: string | null; last_error: string | null }[]
  hidden_characters: { character_id: number; character_name: string }[]
}

export interface NotificationDetail {
  character_id: number
  notification_id: number
  type: string
  category: string
  sent_at: string | null
  summary: string
  details: { key: string; value: string }[]
  parsed: boolean
  read: boolean
}

// ---------------------------------------------------- Mail (Character Management)
// Mirrors eve_trader/character_management/mail_actions.py.
export interface MailRecipient {
  recipient_id: number
  recipient_type: string   // character | corporation | alliance | mailing_list
  name: string | null
}

export interface MailReceivedBy {
  character_id: number
  character_name: string
  is_read: boolean
  labels: number[]
  /** true: read from the local archive; false: read live from ESI. */
  archived: boolean
}

/** One message, grouped over every selected character that received it. */
export interface MailRow {
  mail_id: number
  from_id: number | null
  from_name: string | null
  subject: string
  timestamp: string | null
  is_read: boolean
  recipients: MailRecipient[]
  received_by: MailReceivedBy[]
}

export interface MailCharacterStatus {
  character_id: number
  character_name: string
  archived: boolean
  state: 'ok' | 'reauth_needed' | 'error'
  detail?: string
}

export interface MailPage {
  mails: MailRow[]
  /** character_id -> last_mail_id to continue from; absent = that character is exhausted. */
  next_cursors: Record<string, number>
  characters: MailCharacterStatus[]
}

export interface MailFolderLabel {
  label_id: number
  name: string
  color: string | null
  unread_count: number
  system: boolean
}

/** ready = ticked on the Characters page AND a token holds the scope. */
export type MailCapabilityState = 'ready' | 'not_enabled' | 'reauth_needed'

export interface MailFolders {
  characters: (MailCharacterStatus & {
    labels: MailFolderLabel[]
    lists: { list_id: number; name: string }[]
    total_unread: number
    capabilities: { send: MailCapabilityState; organize: MailCapabilityState }
  })[]
  /** system label id -> unread summed over every character */
  unread: Record<string, number>
}

export interface MailOpened extends MailRow {
  body: string
  character_id: number
  archived: boolean
}

export interface MailSearchResult {
  mails: MailRow[]
  searched: MailCharacterStatus[]
  unsearchable: MailCharacterStatus[]
}

export type MailBackfillState = 'off' | 'idle' | 'running' | 'done' | 'error' | 'interrupted'

export interface MailArchiveRow {
  character_id: number
  character_name: string
  shared: boolean
  archive_enabled: boolean
  reauth_needed: boolean
  backfill_state: MailBackfillState
  headers_complete: boolean
  error: string | null
  last_refresh_at: string | null
  counts: { headers: number; bodies: number }
}

// Mail write actions (phase 4) - mirrors character_management/mail_write.py.
export interface MailDraftRecipient {
  type?: string
  id?: number
  name?: string
}

export interface MailSendRequest {
  from_character_id: number
  recipients: MailDraftRecipient[]
  subject: string
  body: string
  approved_cost?: number
}

export type MailSendResult =
  | { sent: true; mail_id: number; recipients: { recipient_id: number; recipient_type: string; name: string | null }[] }
  | { sent: false; needs_approval: true; cost: number }

export interface MailRecipientHit {
  type: 'character' | 'corporation' | 'alliance' | 'mailing_list'
  id: number
  name: string
}

// ------------------------------------------------------------- discord alerts
export type AlertType = 'skillqueue_empty' | 'mail_new' | 'pi_extractor_expiry' | 'pi_pad_full' | 'pi_inputs_empty'

export interface AlertSubscription {
  shared: boolean
  enabled: boolean
  include_content: boolean
  lead_hours: number
}

export interface AlertSettings {
  bot_configured: boolean
  link_configured: boolean
  linked: boolean
  characters: Array<{
    character_id: number
    character_name: string
    // The PI alert types are newer; an older backend may not send them.
  alerts: Partial<Record<AlertType, AlertSubscription>>
  }>
}

// ------------------------------------------------- Planetary Industry (pi)
export type PiZone = 'highsec' | 'lowsec' | 'nullsec' | 'wormhole'

export interface PiMeta {
  // Player structures PI can price at (the C-J home structure of Trading/Production).
  price_structures?: Array<{ structure_id: number; name: string }>
  products: Array<{ type_id: number; name: string; tier: number; chains: string[] }>
  planet_types: Array<{
    type_id: number; name: string; resources: Array<{ type_id: number; name: string }>
    high_tech: boolean; median_radius_km: number | null
  }>
  chains: string[]
  zones: PiZone[]
  cc_levels: Array<{ level: number; cpu: number; power: number; upgrade_isk: number }>
}

export interface PiTrend {
  days: number
  change: number | null
  volatility: number | null
  avg_daily_volume: number | null
}

export interface PiProfitRow {
  product_type_id: number
  product_name: string
  tier: number
  chain: string
  planet_type_id: number
  planet_type: string | null
  radius_km: number
  factories: number
  heads: number
  launchpads: number
  storages: number
  output_per_day: number
  profit_per_day: number
  revenue_per_day: number
  costs_per_day: { inputs: number; export_tax: number; import_tax: number; freight: number; setup: number }
  worth_it: boolean
  reason: string | null
  interactions_per_week: number
  isk_per_interaction: number | null
  isk_per_m3: number | null
  haul_m3_per_week: number
  market_share: number | null
  trend: PiTrend | null
  buffer_hours: number | null
}

export interface PiProfitAssumptions {
  zone: PiZone
  yield_per_head: number
  effective_yield_per_head: number
  program_hours: number
  interval_hours: number
  tax_rate: number
  freight_per_m3: number
  valuation: string
  hub_region_id: number
  price_structure_id?: number
  market_label?: string
  price_note?: string | null
  npc_tax_rate?: number
  owner_tax_rate?: number
  default_zone?: PiZone
}

export interface PiProfitability {
  zone: PiZone
  cc_level: number
  assumptions: PiProfitAssumptions
  rows: PiProfitRow[]
}

export interface PiDesign {
  chain: string
  product_type_id: number
  planet_type_id: number
  cc_level: number
  factories: Array<[number, number]>
  ecus: Array<[number, number]>
  launchpads: number
  storages: number
}

export interface PiNamedRate { type_id: number; name: string; tier: number | null; per_hour: number }

export interface PiEvaluation {
  design: PiDesign & {
    factories_named: Array<{ type_id: number; name: string; tier: number | null; count: number; utilization?: number }>
    ecus_named: Array<{ type_id: number; name: string; heads: number }>
  }
  fits: boolean
  cpu_used: number; cpu_capacity: number
  power_used: number; power_capacity: number
  links: { count: number; km: number; level: number; cpu: number; power: number }
  product_per_hour: number
  effective_product_per_hour: number
  effective_factor: number
  buffer_hours: number | null
  import_m3_per_hour: number
  export_m3_per_hour: number
  extracted: PiNamedRate[]
  produced: PiNamedRate[]
  imports: PiNamedRate[]
  exports: PiNamedRate[]
  idle_factories: number
  setup_isk: number
  notes: string[]
}

export interface PiEconomics {
  revenue_per_day: number
  input_cost_per_day: number
  export_tax_per_day: number
  import_tax_per_day: number
  freight_per_day: number
  setup_per_day: number
  profit_per_day: number
  output_units_per_day: number
  haul_m3_per_week: number
  interactions_per_week: number
  isk_per_interaction: number | null
  isk_per_m3: number | null
  market_share: number | null
  worth_it: boolean
  reason: string | null
  missing_prices: Array<{ type_id: number; name: string }>
  unpriced_surplus: Array<{ type_id: number; name: string }>
}

export interface PiPlannerResult {
  planet: {
    planet_id: number | null; name: string | null; planet_type_id: number; planet_type: string
    radius_km: number
    system: { solar_system_id: number; name: string | null; security: number | null; region_id: number | null } | null
  }
  zone: PiZone
  cc_level: number
  chain: string
  product: { type_id: number; name: string }
  assumptions: {
    yield_per_head: number; effective_yield_per_head: number; program_hours: number
    interval_hours: number; tax_rate: number; freight_per_m3: number
    market_label?: string; npc_tax_rate?: number; owner_tax_rate?: number; default_zone?: PiZone
  }
  evaluation: PiEvaluation
  economics: PiEconomics
  prices: Record<string, { sell: number | null; buy: number | null }>
  trend: PiTrend | null
}

export interface PiPlannerBody {
  chain: string
  product_type_id: number
  planet_id?: number
  planet_type_id?: number
  radius_km?: number
  cc_level?: number
  zone?: string
  owner_tax_rate?: number
  freight_per_m3?: number
  yield_per_head?: number
  program_hours?: number
  interval_hours?: number
  design?: Partial<PiDesign>
}

export interface PiSystemHit {
  solar_system_id: number; name: string; security: number; region_id: number
  zone: PiZone; planet_count: number
}

export interface PiPlanet {
  planet_id: number; name: string; planet_type_id: number; planet_type: string | null; radius_km: number
}

export interface PiSystemPlanets {
  solar_system_id: number; name: string; security: number; region_id: number
  zone: PiZone; reachable: boolean; planets: PiPlanet[]
}

export interface PiChainNode {
  product_type_id: number
  product_name: string
  tier: number
  chain: string
  colonies: number | null
  colonies_ceil: number | null
  per_colony_per_hour: number | null
  profit_per_colony_day: number | null
  reason: string | null
  children: PiChainNode[]
}

export interface PiChainPlan {
  product_type_id: number
  product_name: string
  per_hour: number | null
  feasible: boolean
  tree: PiChainNode
  stop_at: Array<{
    tier: number; feasible: boolean; planets?: number; planets_ceil?: number
    profit_per_day?: number; profit_per_planet_day?: number | null
  }>
  zone: PiZone
  cc_level: number
}

export interface PiSystemAnalysis {
  system: { solar_system_id: number; name: string; security: number; region_id: number; zone: PiZone; reachable: boolean }
  zone: PiZone
  slots: number
  characters: number
  cc_level: number
  assumptions: { yield_per_head: number; program_hours: number; interval_hours: number; tax_rate: number }
  planets: Array<PiPlanet & {
    best: Array<{
      chain: string; product_type_id: number; product_name: string; profit_per_day: number
      worth_it: boolean; reason: string | null; output_per_day: number; design: PiDesign
    }>
  }>
  plan: {
    status: string
    profit_per_day: number
    profit_per_slot: number
    used_slots: number
    best_single_uses_profit_per_day: number
    chain_gain_per_day: number
    colonies: Array<{
      planet_id: number; planet_name: string | null; chain: string; product_type_id: number
      product_name: string; count: number; yield_factors: number[]
    }>
    flows: Array<{
      type_id: number; name: string; produced: number; consumed: number
      internal: number; sold: number; bought: number
    }>
    notes: string[]
  }
  cannot: string[]
}

export interface PiPlan {
  plan_id: number
  name: string
  planet_id: number | null
  planet_type_id: number
  radius_km: number
  character_id: number | null
  design: PiDesign
  owner_tax_rate: number | null
  freight_per_m3: number | null
  yield_override: number | null
  notes: string | null
  created_at: string | null
  updated_at: string | null
}

export interface PiSettings {
  hub_region_id: number
  pi_price_structure_id: number
  pi_price_structure_slug: string
  pi_broker_fee_rate: number
  pi_sales_tax_rate: number
  pi_valuation: 'sell_orders' | 'buy_orders'
  pi_freight_per_m3: number
  pi_owner_tax_rate: number
  pi_yield_highsec: number
  pi_yield_lowsec: number
  pi_yield_nullsec: number
  pi_yield_wormhole: number
  pi_zone: PiZone
  pi_program_hours: number
  pi_collection_interval_hours: number
  pi_amortisation_days: number
  pi_min_isk_per_planet_day: number
  pi_market_share_warning: number
  pi_planets_per_character: number
  pi_characters: number
  pi_cc_level: number
  pi_customs_code_expertise_level: number
  pi_reference_radius_km: number
  pi_demand_days: number
}

export interface PiColonyExtractor {
  pin_id: number
  product_type_id: number | null
  product_name: string | null
  heads: number
  hours_left: number | null
  expired: boolean
  per_head_per_hour: number
  rate_source: 'esi' | 'expired' | 'assumption'
}

export interface PiColonyProjection {
  age_hours: number | null
  uncertain: boolean
  extractors: PiColonyExtractor[]
  storage_m3: number
  stored_m3: number
  hours_until_full: number | null
  hours_until_inputs_empty: number | null
  inputs_empty_type: string | null
  idle_factories: Array<{ pin: number; product_name: string | null }>
  product_name: string | null
  chain: string | null
  skipped_routes: number
  cc_bypassed: number
}

export interface PiColonyView {
  planet_id: number
  planet_name: string | null
  planet_type: string | null
  zone: string
  upgrade_level: number | null
  radius_km: number | null
  last_update: string | null
  template_available: boolean
  projection: PiColonyProjection
}

export interface PiColoniesCharacter {
  character_id: number
  character_name: string
  state: 'ok' | 'not_shared' | 'reauth_needed' | 'not_synced'
  detail?: string | null
  synced_at: string | null
  colonies: PiColonyView[]
}

export interface PiColonies {
  characters: PiColoniesCharacter[]
  shared: boolean
  now: string
}

export interface PiCalibration {
  min_samples: number
  sample_count: number
  zones: Array<{ zone: string; count: number; median: number | null; default: number; active: boolean }>
  p0: Array<{ type_id: number; name: string | null; count: number; median: number | null }>
}

export interface PiCharacters {
  characters: Array<{
    character_id: number; character_name: string | null; source: 'esi' | 'manual'
    planets: number; cc_level: number; customs_code_expertise: number
  }>
  total_slots: number
  max_cc_level: number
  character_count: number
}

export interface PiDemandRow {
  type_id: number
  name: string
  tier: number
  quantity: number
  buy_price: number | null
  buy_total: number | null
  pi_unit_cost: number | null
  chain: string | null
  planet_type_id?: number | null
  pi_total?: number | null
  saving: number | null
  colonies_needed: number | null
  colonies_needed_ceil?: number | null
  make_via_pi: boolean
}

export interface PiDemand { rows: PiDemandRow[]; days: number; zone: PiZone }

export interface PiFinding {
  severity: 'error' | 'warning' | 'info'
  code: string
  message: string
  pins: number[]
  route: number | null
  link: number | null
}

export interface PiAnalysis {
  ok: boolean
  findings: PiFinding[]
  kinds: Array<string | null>
  cpu_used: number; cpu_capacity: number
  power_used: number; power_capacity: number
  link_cpu: number; link_power: number
  links: Array<{ km: number; load_m3h: number; capacity_m3h: number }>
  runs_per_hour: Record<string, number>
  max_runs_per_hour: Record<string, number>
  extracted: PiNamedRate[]
  produced: PiNamedRate[]
  imports: PiNamedRate[]
  exports: PiNamedRate[]
  storage_m3: number
  buffer_hours: number | null
  import_m3_per_hour: number
  export_m3_per_hour: number
  product_type_id: number | null
  product_name: string | null
  chain: string | null
  setup_isk: number
}

export interface PiLayoutPayload {
  template: Record<string, unknown>
  template_json: string
  analysis: PiAnalysis
}

export interface PiGeneratePayload extends PiLayoutPayload {
  design: PiDesign
  notes: string[]
  shape: string | null
  planet: { planet_id: number | null; planet_type_id: number; radius_km: number }
}

export interface PiTemplateRow {
  template_id: number
  name: string
  comment: string | null
  planet_type_id: number | null
  cc_level: number | null
  diameter_km: number | null
  source: string
  created_at: string | null
}

export type PiTemplateDetail = PiTemplateRow & PiLayoutPayload

// ---- PI design tools (phase 5b/5c)
export interface PiTemplateJson {
  CmdCtrLv: number
  Cmt?: string
  Diam: number
  L: Array<{ D: number; Lv: number; S: number }>
  P: Array<{ H: number; La: number; Lo: number; S: number | null; T: number }>
  Pln: number
  R: Array<{ P: number[]; Q: number; T: number }>
}

export type PiEditOp =
  | { op: 'move'; pin: number; la: number; lo: number }
  | { op: 'remove'; pin: number }
  | { op: 'add'; kind: string; product?: number; heads?: number; la: number; lo: number }
  | { op: 'link_level'; link: number; level: number }
  | { op: 'route_storage' }

export interface PiNamedRef { type_id: number; name: string }

export interface PiWayRow {
  made?: PiNamedRef[]
  extracted?: PiNamedRef
  hauled: PiNamedRef[]
  evaluation: PiEvaluation | null
  economics: PiEconomics | null
}

export interface PiWays {
  product: PiNamedRef
  zone: PiZone
  cc_level: number
  variants: PiWayRow[]
  partial: PiWayRow[]
}

export interface PiMixedP2 {
  zone: PiZone
  cc_level: number
  evaluation: PiEvaluation
  economics: PiEconomics
}

export interface PiStorageSuggestion {
  interval_hours: number
  kind: 'covered' | 'add_storage' | 'trade' | 'higher_tier' | 'none'
  buffer_hours?: number | null
  storages?: number
  reaches_interval?: boolean
  removed_factories?: number
  output_share?: number
  chain?: string
  design?: PiDesign
}

export interface PiGrow {
  design: PiDesign
  evaluation: PiEvaluation
}

export interface PiDesignBase {
  planet_id?: number
  planet_type_id?: number
  radius_km?: number
  zone?: string
  cc_level?: number
}

// POST /api/pi/design/chain-plan (eve_trader/pi/chain_actions.py)
export interface PiChainPlanBody {
  product_type_id: number
  solar_system_id?: number
  characters?: Array<{ name?: string; character_id?: number; planets: number; cc_level: number }>
  cc_level?: number
  owner_tax_rate?: number
  allow_buy?: boolean
  target_per_hour?: number
}

export interface PiChainAssignment {
  character_key: string
  character: string
  cc_level: number
  planet_id: number | null
  planet_name: string
  planet_type_id: number | null
  planet_type: string | null
  chain: string
  product_type_id: number
  product_name: string
  is_extraction: boolean
  units_per_day: number
  layout_request: Record<string, unknown>
}

export interface PiChainStage {
  type_id: number
  name: string
  tier: number
  in_tree: boolean
  colonies: number
  made: number
  needed: number
  internal: number
  bought: number
  sold: number
  discarded: number
}

export interface PiChainPurchase {
  type_id: number
  name: string
  units_per_day: number
  cost_per_day: number
  reason_code: string
  reason: string
}

export interface PiChainPlanResult {
  status: 'optimal' | 'time_limit' | 'fallback' | 'infeasible'
  mode: 'target' | 'inputs'
  target_type_id: number
  target_name: string
  target_units_per_day: number
  max_target_units_per_day: number | null
  requested_units_per_day: number | null
  profit_per_day: number
  profit_per_slot: number | null
  used_slots: number
  slots: number
  free_slots: number
  characters: number
  assignments: PiChainAssignment[]
  stages: PiChainStage[]
  purchases: PiChainPurchase[]
  notes: string[]
  system: { solar_system_id?: number; name?: string; zone?: string } | null
  zone: string
  allow_buy: boolean
}
