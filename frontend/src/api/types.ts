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

export interface UnlistedStockRow {
  type_id: number
  item: string
  asset_quantity: number
  sell_order_remaining: number
  unlisted_quantity: number
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

export interface PriceHistoryPoint {
  region_id: number
  type_id: number
  date: string
  min_price: number
  max_price: number
  avg_price: number
  movement: number
  num_orders: number
}

export interface TradingSettings {
  import_cost_per_m3: number
  structure_sell_haircut: number
  min_profit_threshold: number
  min_margin_threshold: number
  skip_grace_period_days: number
  enforce_shortlist_cap: boolean
  max_active_shortlist_items: number
  min_hit_rate: number
  min_avg_movement: number
  safe_mode_max_ids: number
  lookback_days: number
  jita_region_id: number
  reference_region_id: number
  structure_id: number | null
  buyer_character_name: string | null
  seller_character_name: string | null
}

export interface TradingKpis {
  shortlist_count: number
  import_candidates: number
  own_sell_orders: number
  new_recommendations: number
}

// ------------------------------------------------------------ production
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
  job_category: string | null
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
}

export interface DistributionRow {
  type_id: number
  type_name: string
  from_location_id: number
  to_category: string
  to_location_id: number
  quantity: number
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
  total_attempt_cost: number
  expected_cost_per_success: number | null
  expected_cost_per_run: number | null
  me: number
  te: number
  material_savings_per_run: number
  net_cost_per_run: number | null
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
  job_category: string | null
  stock_coverage: number | null
  recommended_slots: number | null
}

export interface AssetPlan {
  jobs: AssetPlanJob[]
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
}

export interface CharacterSlotRow {
  character_name: string
  job_type: string
  total_slots: number
  used_slots: number
  free_slots: number
}

export interface OwnedBlueprintRow {
  type_id: number
  type_name: string
  is_original: boolean
  quantity: number
  material_efficiency: number
  time_efficiency: number
  runs: number | null
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

export interface ProductionPlan {
  inventory: InventoryRow[]
  buy_list: BuyListEntry[]
  build_list: BuildJobEntry[]
  invention_list: InventionNeedRow[]
}

export interface ProductionSettings {
  component_overbuild: number
  bpc_inventory: number
  market_fees: number
  jita_buy_broker_fee: number
  min_margin: number
  min_daily_profit: number
  haul_cost_per_m3: number
  home_market: string | null
  home_location_id: number | null
  distribution_source_location_id: number | null
  invention_location_id: number | null
  reaction_structure_type: string
  reaction_rig_tier: string
  component_structure_type: string
  component_rig_tier: string
  manufacturing_structure_type: string
  manufacturing_rig_tier: string
  encryption_skill_level: number
  datacore_skill_1_level: number
  datacore_skill_2_level: number
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

export interface AuthStatus {
  buyer: string | null
  seller: string | null
}

export interface GateStatus {
  enabled: boolean
  logged_in: boolean
  character_name: string | null
  tools: string[]
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
}

export interface SchedulerJobStatus {
  interval_hours: number
  last_run_at: string | null
  last_error: string | null
}

export interface SchedulerStatus {
  enabled: boolean
  running: boolean
  jobs: {
    trading_pipeline: SchedulerJobStatus
    production_sync: SchedulerJobStatus
    doctrine_contract_sync: SchedulerJobStatus
    backup: SchedulerJobStatus
  }
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
}

export interface DoctrineCharacter {
  role_key: string
  character_id: number
  character_name: string
}
