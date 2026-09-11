import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import { ApiError } from '../api/client'
import type { PipelineRunProgress, PipelineRunStatus } from '../api/types'

export function formatBackgroundProgress(
  progress: PipelineRunProgress | null | undefined,
  jobName?: string | null,
  labels: Record<string, string> = {},
  defaultLabel = 'Job',
): string {
  let body = 'Running…'
  if (progress) {
    if (progress.phase === 'search' && progress.batch && progress.total_batches) {
      const skipped = progress.skipped ? `, ${progress.skipped} skipped` : ''
      body = `Batch ${progress.batch}/${progress.total_batches}, ${progress.evaluated ?? 0} items scored${skipped}`
    } else if (progress.phase === 'cleanup' && progress.batch && progress.total_batches) {
      const skipped = progress.skipped ? `, ${progress.skipped} skipped` : ''
      body = `Cleanup batch ${progress.batch}/${progress.total_batches}, ${progress.refreshed ?? 0} items refreshed${skipped}`
    } else if (progress.batch && progress.total_batches) {
      // Generic batch progress (Doctrine contract sync, Admin SDE refresh).
      // Same batch/total_batches vocabulary as Trading - no per-tool schema.
      const extra = progress.message ? ` (${progress.message})` : ''
      body = `Batch ${progress.batch}/${progress.total_batches}${extra}`
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
  const label = (jobName && labels[jobName]) || defaultLabel
  if (jobName && jobName !== 'refresh_and_prune') {
    return `${label}: ${body}`
  }
  return body
}

export function useBackgroundJob(opts: {
  queryKey: readonly unknown[]
  fetchStatus: () => Promise<PipelineRunStatus>
  resultKeys: string[][]
  labels: Record<string, string>
  defaultLabel: string
  formatProgress?: (
    progress: PipelineRunProgress | null | undefined,
    jobName?: string | null,
  ) => string
  onSucceeded?: (status: PipelineRunStatus) => void
  // Poll cadence while a job is running. Default 4000ms matches Trading's
  // original behavior (a run can take minutes - no need to poll faster).
  // Callers whose job is typically much shorter (Admin SDE refresh finishes
  // in ~6s) can pass a tighter interval so the UI shows more than a single
  // "Running…" before it's already done - that tradeoff (more status-poll
  // requests for the job's duration) should stay opt-in per caller, not a
  // blanket change that also speeds up polling during a long Trading run.
  pollIntervalMs?: number
}) {
  const queryClient = useQueryClient()
  const prevStatus = useRef<string | undefined>(undefined)
  const {
    queryKey, fetchStatus, resultKeys, labels, defaultLabel, onSucceeded,
    pollIntervalMs = 4000,
  } = opts
  const formatProgress = opts.formatProgress
    ?? ((progress, jobName) => formatBackgroundProgress(progress, jobName, labels, defaultLabel))

  const statusQuery = useQuery({
    queryKey,
    queryFn: fetchStatus,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? pollIntervalMs : false),
    refetchOnWindowFocus: true,
  })

  useEffect(() => {
    const status = statusQuery.data?.status
    const prev = prevStatus.current
    const label = (statusQuery.data?.job_name && labels[statusQuery.data.job_name]) || defaultLabel
    if (prev === 'running' && status === 'succeeded') {
      notifications.show({ title: label, message: 'Done', color: 'accent' })
      onSucceeded?.(statusQuery.data as PipelineRunStatus)
      for (const key of resultKeys) {
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
  }, [
    defaultLabel,
    queryClient,
    statusQuery.data?.error,
    statusQuery.data?.job_name,
    statusQuery.data?.result,
    statusQuery.data?.run_id,
    statusQuery.data?.status,
  ])

  const onStartSuccess = (result: PipelineRunStatus) => {
    queryClient.setQueryData(queryKey, result)
    queryClient.invalidateQueries({ queryKey })
  }

  const onStartError = (err: unknown) => {
    if (err instanceof ApiError && err.status === 409) {
      queryClient.invalidateQueries({ queryKey })
      notifications.show({
        title: 'Job already running',
        message: err.message,
        color: 'warn',
      })
      return
    }
    const message = err instanceof ApiError ? err.message : String(err)
    notifications.show({ title: `${defaultLabel} - Error`, message, color: 'danger' })
  }

  return {
    status: statusQuery.data,
    jobName: statusQuery.data?.job_name,
    onStartSuccess,
    onStartError,
    formatProgress,
    runningStatus: statusQuery.data?.status === 'running',
  }
}

export function useBackgroundJobStart<TArgs = void>(
  job: ReturnType<typeof useBackgroundJob>,
  mutationFn: (args: TArgs) => Promise<PipelineRunStatus>,
) {
  return useMutation({
    mutationFn,
    onSuccess: job.onStartSuccess,
    onError: job.onStartError,
  })
}
