import { ApiError, tradingApi } from '../api/client'
import { notify } from '../notify'

// Spotlight (Ctrl/Cmd+K) commands that start something instead of navigating.
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
