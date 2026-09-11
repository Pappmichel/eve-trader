import { tradingApi } from '../api/client'
import { warnIfPricedViaFallback } from './useAction'
import { formatBackgroundProgress, useBackgroundJob, useBackgroundJobStart } from './useBackgroundJob'
import type { PipelineRunStatus } from '../api/types'

const STATUS_KEY = ['trading', 'pipeline', 'refresh-and-prune'] as const

const RESULT_KEYS: string[][] = [
  ['trading', 'shortlist', 'snapshot'],
  ['trading', 'shortlist', 'items'],
  ['trading', 'candidates', 'new'],
  ['trading', 'kpis'],
  ['trading', 'esi-sync-time'],
  ['trading', 'trades', 'realized'],
]

const JOB_LABELS: Record<string, string> = {
  refresh_and_prune: 'Search, Add & Clean Up',
  refresh_shortlist: 'Refresh Shortlist',
  pipeline: 'Pipeline',
}

function pricedViaFallbackResult(result: Record<string, unknown> | null | undefined): unknown {
  if (!result) return result
  if (result.priced_via_fallback) return result
  const nested = result.refresh_and_prune_candidates
  if (nested && typeof nested === 'object') return nested
  return result
}

export function formatPipelineProgress(
  progress: Parameters<typeof formatBackgroundProgress>[0],
  jobName?: string | null,
): string {
  return formatBackgroundProgress(progress, jobName, JOB_LABELS, 'Trading job')
}

export function useTradingPipelineJob() {
  const job = useBackgroundJob({
    queryKey: STATUS_KEY,
    fetchStatus: tradingApi.refreshAndPruneStatus,
    resultKeys: RESULT_KEYS,
    labels: JOB_LABELS,
    defaultLabel: 'Trading job',
    onSucceeded: (status: PipelineRunStatus) => {
      warnIfPricedViaFallback(pricedViaFallbackResult(status.result ?? null))
    },
  })
  const startRefreshAndPrune = useBackgroundJobStart(job, (safe: boolean) => tradingApi.startRefreshAndPrune(safe))
  const startRefreshShortlist = useBackgroundJobStart(job, () => tradingApi.refreshShortlist())
  const startPipeline = useBackgroundJobStart(job, () => tradingApi.runPipeline(true, false))

  const starting = startRefreshAndPrune.isPending || startRefreshShortlist.isPending || startPipeline.isPending
  const running = job.runningStatus || starting
  const jobName = job.jobName

  return {
    status: job.status,
    running,
    jobName,
    isJob: (name: string) => running && jobName === name,
    startRefreshAndPrune: (safe: boolean) => startRefreshAndPrune.mutate(safe),
    startRefreshShortlist: () => startRefreshShortlist.mutate(),
    startPipeline: () => startPipeline.mutate(),
    progressLabel: running ? job.formatProgress(job.status?.progress, jobName) : null,
  }
}
