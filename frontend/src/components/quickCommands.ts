import {
  ApiError, doctrineApi, moduleReprocessingApi, productionApi, refiningApi, stationTradingApi, tradingApi,
} from '../api/client'
import { notify } from '../notify'

// Spotlight (Ctrl/Cmd+K) commands that start something instead of navigating.
// One entry per tool-layout button that calls ESI/Goonmetrics live; they run the same API calls
// and invalidate the same queries as those buttons.
// Every one of these calls ESI and/or Goonmetrics live, so QuickNav always
// asks for confirmation first - a stray Enter must not start a long job.
// Background jobs (shortlist refresh, search, pipeline) share one lock server
// side; the Trading layout polls and reports their result, so after starting
// one we just invalidate its status query and open the Trading page.
export interface QuickCommand {
  id: string
  label: string
  description: string
  toolKey: string
  effect: string
  goto: string
  invalidate: string[][]
  run: () => Promise<unknown>
}

const TRADING_JOB_STATUS = ['trading', 'pipeline', 'refresh-and-prune']

export const QUICK_COMMANDS: QuickCommand[] = [
  {
    id: 'cmd-trading-refresh-shortlist',
    label: 'Run: Refresh Trading Shortlist',
    description: 'Live prices from ESI/Goonmetrics (background job)',
    toolKey: 'trading',
    effect: 'Loads current prices and stock live from ESI (with a Goonmetrics fallback) and updates the shortlist.',
    goto: '/trading/shortlist',
    invalidate: [TRADING_JOB_STATUS],
    run: () => tradingApi.refreshShortlist(),
  },
  {
    id: 'cmd-trading-search',
    label: 'Run: Trading Search + Add + Clean Up',
    description: 'Live search for new import candidates (background job)',
    toolKey: 'trading',
    effect: 'Searches live via Goonmetrics/ESI for new import candidates, adds good hits and cleans up the shortlist.',
    goto: '/trading/shortlist',
    invalidate: [TRADING_JOB_STATUS],
    run: () => tradingApi.startRefreshAndPrune(true),
  },
  {
    id: 'cmd-trading-reconcile',
    label: 'Run: Reconcile Trades',
    description: 'Matches wallet transactions from ESI',
    toolKey: 'trading',
    effect: 'Matches wallet transactions live from ESI against open positions.',
    goto: '/trading/trades',
    invalidate: [['trading', 'trades', 'realized']],
    run: () => tradingApi.reconcileTrades(),
  },
  {
    id: 'cmd-trading-pipeline',
    label: 'Run: Complete Trading Pipeline',
    description: 'Sync, search, add and clean up in one go (background job)',
    toolKey: 'trading',
    effect: 'Runs sync, search, add and shortlist cleanup all in one go.',
    goto: '/trading/shortlist',
    invalidate: [TRADING_JOB_STATUS],
    run: () => tradingApi.runPipeline(true, false),
  },
  {
    id: 'cmd-production-sync',
    label: 'Run: Production - Refresh what I need',
    description: 'Refreshes the ESI snapshots Production uses',
    toolKey: 'production',
    effect: 'Refreshes the ESI snapshots shared with Production.',
    goto: '/production/stock-targets',
    invalidate: [
      ['production', 'jobs'], ['production', 'slots'], ['production', 'market-status'], ['production', 'esi-sync-time'],
      ['production', 'blueprints'], ['production', 'stock-value'],
    ],
    run: () => productionApi.syncEsi(),
  },
  {
    id: 'cmd-production-refresh',
    label: 'Run: Production - Refresh Buy/Build list',
    description: 'Recomputes the plan with current prices',
    toolKey: 'production',
    effect: 'Recomputes the Buy/Build list with current Home prices (live ESI) and Jita prices (cached with live fallback).',
    goto: '/production/buy',
    invalidate: [['production', 'plan'], ['production', 'stock-targets'], ['production', 'logistics']],
    run: () => productionApi.refreshPlan(),
  },
  {
    id: 'cmd-doctrine-sync',
    label: 'Run: Doctrine - Refresh contracts',
    description: 'Syncs contracts from ESI (background job)',
    toolKey: 'doctrine',
    effect: 'Syncs the shared contracts live from ESI and matches them against the doctrine fittings.',
    goto: '/doctrine/contracts',
    invalidate: [['doctrine', 'pipeline', 'sync']],
    run: () => doctrineApi.syncContracts(),
  },
  {
    id: 'cmd-doctrine-assets',
    label: 'Run: Doctrine - Sync assets',
    description: 'Refreshes the asset snapshots Doctrine uses',
    toolKey: 'doctrine',
    effect: 'Refreshes the ESI asset snapshots shared with Doctrine.',
    goto: '/doctrine/stockpile',
    invalidate: [['doctrine', 'stockpile'], ['doctrine', 'asset-sync-time']],
    run: () => doctrineApi.syncAssets(),
  },
  {
    id: 'cmd-station-trading-refresh',
    label: 'Run: Station Trading - Refresh shortlist',
    description: 'Scans Jita live for spread candidates',
    toolKey: 'station_trading',
    effect: 'Scans Jita live via Goonmetrics/ESI for new spread candidates and reprices the shortlist.',
    goto: '/station-trading/shortlist',
    invalidate: [['station-trading', 'shortlist'], ['station-trading', 'esi-sync-time']],
    run: () => stationTradingApi.refreshShortlist(),
  },
  {
    id: 'cmd-ore-refresh',
    label: 'Run: Ore & Minerals - Refresh shortlist',
    description: 'Reprices the ore shortlist live',
    toolKey: 'refining',
    effect: 'Reprices the entire shortlist live via ESI (with a Goonmetrics fallback).',
    goto: '/ore/shortlist',
    invalidate: [['refining', 'shortlist', 'snapshot'], ['refining', 'esi-sync-time']],
    run: () => refiningApi.refreshShortlist(),
  },
  {
    id: 'cmd-modules-refresh',
    label: 'Run: Module Reprocessing - Refresh shortlist',
    description: 'Scans candidates and reprices live (can take a minute)',
    toolKey: 'module_reprocessing',
    effect: 'Scans the full candidate universe against Goonmetrics, auto-adds anything profitable, then reprices the whole shortlist live via ESI.',
    goto: '/modules/shortlist',
    invalidate: [
      ['module_reprocessing', 'shortlist', 'snapshot'], ['module_reprocessing', 'shortlist', 'items'],
      ['module_reprocessing', 'esi-sync-time'],
    ],
    run: () => moduleReprocessingApi.refreshShortlist(),
  },
]

// Same visibility rule as the page entries: `tools` undefined means "grants not
// loaded yet / gate off" and shows everything.
export function visibleCommands(tools: string[] | undefined): QuickCommand[] {
  return QUICK_COMMANDS.filter((c) => tools === undefined || tools.includes(c.toolKey))
}

export async function executeQuickCommand(
  cmd: QuickCommand,
  deps: { invalidate: (key: string[]) => void; navigate: (to: string) => void },
): Promise<void> {
  try {
    await cmd.run()
    notify({ title: cmd.label.replace(/^Run: /, ''), message: 'Started', color: 'accent' })
    for (const key of cmd.invalidate) deps.invalidate(key)
    deps.navigate(cmd.goto)
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      notify({ title: 'Job already running', message: err.message, color: 'warn' })
      deps.navigate(cmd.goto)
      return
    }
    const message = err instanceof ApiError ? err.message : String(err)
    notify({ title: `${cmd.label.replace(/^Run: /, '')} - Error`, message, color: 'danger' })
  }
}
