import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import { ApiError, tradingApi } from '../api/client'
import type { PipelineRunProgress, PipelineRunStatus } from '../api/types'
import { warnIfPricedViaFallback } from './useAction'

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

function jobLabel(jobName: string | null | undefined): string {
  return (jobName && JOB_LABELS[jobName]) || 'Trading job'
}

export function formatPipelineProgress(
  progress: PipelineRunProgress | null | undefined,
  jobName?: string | null,
): string {
  let body = 'Running…'
  if (progress) {
    if (progress.phase === 'search' && progress.batch && progress.total_batches) {
      const skipped = progress.skipped ? `, ${progress.skipped} skipped` : ''
      body = `Batch ${progress.batch}/${progress.total_batches}, ${progress.evaluated ?? 0} items scored${skipped}`
    } else if (progress.phase === 'cleanup' && progress.batch && progress.total_batches) {
      const skipped = progress.skipped ? `, ${progress.skipped} skipped` : ''
      body = `Cleanup batch ${progress.batch}/${progress.total_batches}, ${progress.refreshed ?? 0} items refreshed${skipped}`
    } else if (progress.message) {
      body = progress.message
    } else if (progress.phase === 'add') {
      body = 'Adding recommended candidates…'
    } else if (progress.phase === 'cleanup') {
      body = 'Refreshing shortlist prices…'
    } else if (progress.phase === 'search') {
      body = 'Searching for new import candidates…'
    } else if (progress.phase === 'build_universe') {
      body = 'Loading market groups…'
    } else if (progress.phase === 'reconcile') {
      body = 'Reconciling trades…'
    }
  }
  if (jobName && jobName !== 'refresh_and_prune') {
    return `${jobLabel(jobName)}: ${body}`
  }
  return body
}

function pricedViaFallbackResult(result: Record<string, unknown> | null | undefined): unknown {
  if (!result) return result
  if (result.priced_via_fallback) return result
  const nested = result.refresh_and_prune_candidates
  if (nested && typeof nested === 'object') return nested
  return result
}

export function useTradingPipelineJob() {
  const queryClient = useQueryClient()
  const prevStatus = useRef<string | undefined>(undefined)

  const statusQuery = useQuery({
    queryKey: STATUS_KEY,
    queryFn: tradingApi.refreshAndPruneStatus,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 4000 : false),
    refetchOnWindowFocus: true,
  })

  useEffect(() => {
    const status = statusQuery.data?.status
    const prev = prevStatus.current
    const label = jobLabel(statusQuery.data?.job_name)
    if (prev === 'running' && status === 'succeeded') {
      notifications.show({
        title: label,
        message: 'Done',
        color: 'accent',
      })
      warnIfPricedViaFallback(pricedViaFallbackResult(statusQuery.data?.result ?? null))
      for (const key of RESULT_KEYS) {
        queryClient.invalidateQueries({ queryKey: key })
      }
    }
    if (prev === 'running' && status === 'failed') {
      notifications.show({
        title: `${label} - Error`,
        message: statusQuery.data?.error || 'The background job failed.',
        color: 'danger',
      })
    }
    prevStatus.current = status
  }, [queryClient, statusQuery.data?.error, statusQuery.data?.job_name, statusQuery.data?.result, statusQuery.data?.run_id, statusQuery.data?.status])

  const onStartSuccess = (result: PipelineRunStatus) => {
    queryClient.setQueryData(STATUS_KEY, result)
    queryClient.invalidateQueries({ queryKey: STATUS_KEY })
  }

  const onStartError = (err: unknown) => {
    if (err instanceof ApiError && err.status === 409) {
      queryClient.invalidateQueries({ queryKey: STATUS_KEY })
      notifications.show({
        title: 'Trading job already running',
        message: err.message,
        color: 'warn',
      })
      return
    }
    const message = err instanceof ApiError ? err.message : String(err)
    notifications.show({ title: 'Trading job - Error', message, color: 'danger' })
  }

  const startRefreshAndPrune = useMutation({
    mutationFn: (safe: boolean) => tradingApi.startRefreshAndPrune(safe),
    onSuccess: onStartSuccess,
    onError: onStartError,
  })
  const startRefreshShortlist = useMutation({
    mutationFn: () => tradingApi.refreshShortlist(),
    onSuccess: onStartSuccess,
    onError: onStartError,
  })
  const startPipeline = useMutation({
    mutationFn: () => tradingApi.runPipeline(true, false),
    onSuccess: onStartSuccess,
    onError: onStartError,
  })

  const starting = startRefreshAndPrune.isPending || startRefreshShortlist.isPending || startPipeline.isPending
  const running = statusQuery.data?.status === 'running' || starting
  const jobName = statusQuery.data?.job_name

  return {
    status: statusQuery.data,
    running,
    jobName,
    isJob: (name: string) => running && jobName === name,
    startRefreshAndPrune: (safe: boolean) => startRefreshAndPrune.mutate(safe),
    startRefreshShortlist: () => startRefreshShortlist.mutate(),
    startPipeline: () => startPipeline.mutate(),
    progressLabel: running ? formatPipelineProgress(statusQuery.data?.progress, jobName) : null,
  }
}
