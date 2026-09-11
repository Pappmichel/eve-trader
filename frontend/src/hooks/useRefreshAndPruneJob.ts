import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import { ApiError, tradingApi } from '../api/client'
import type { PipelineRunProgress, PipelineRunStatus } from '../api/types'

const STATUS_KEY = ['trading', 'pipeline', 'refresh-and-prune'] as const

const RESULT_KEYS: string[][] = [
  ['trading', 'shortlist', 'snapshot'],
  ['trading', 'shortlist', 'items'],
  ['trading', 'candidates', 'new'],
  ['trading', 'kpis'],
  ['trading', 'esi-sync-time'],
]

export function formatPipelineProgress(progress: PipelineRunProgress | null | undefined): string {
  if (!progress) return 'Running…'
  if (progress.phase === 'search' && progress.batch && progress.total_batches) {
    const skipped = progress.skipped ? `, ${progress.skipped} skipped` : ''
    return `Batch ${progress.batch}/${progress.total_batches}, ${progress.evaluated ?? 0} items scored${skipped}`
  }
  if (progress.phase === 'cleanup' && progress.batch && progress.total_batches) {
    const skipped = progress.skipped ? `, ${progress.skipped} skipped` : ''
    return `Cleanup batch ${progress.batch}/${progress.total_batches}, ${progress.refreshed ?? 0} items refreshed${skipped}`
  }
  if (progress.message) return progress.message
  if (progress.phase === 'add') return 'Adding recommended candidates…'
  if (progress.phase === 'cleanup') return 'Refreshing shortlist prices…'
  if (progress.phase === 'search') return 'Searching for new import candidates…'
  return 'Running…'
}

export function useRefreshAndPruneJob() {
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
    if (prev === 'running' && status === 'succeeded') {
      notifications.show({
        title: 'Search, Add & Clean Up',
        message: 'Done',
        color: 'accent',
      })
      for (const key of RESULT_KEYS) {
        queryClient.invalidateQueries({ queryKey: key })
      }
    }
    if (prev === 'running' && status === 'failed') {
      notifications.show({
        title: 'Search, Add & Clean Up - Error',
        message: statusQuery.data?.error || 'The background job failed.',
        color: 'danger',
      })
    }
    prevStatus.current = status
  }, [queryClient, statusQuery.data?.error, statusQuery.data?.run_id, statusQuery.data?.status])

  const start = useMutation({
    mutationFn: (safe: boolean) => tradingApi.startRefreshAndPrune(safe),
    onSuccess: (result) => {
      queryClient.setQueryData(STATUS_KEY, result)
      queryClient.invalidateQueries({ queryKey: STATUS_KEY })
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 409) {
        queryClient.invalidateQueries({ queryKey: STATUS_KEY })
        notifications.show({
          title: 'Search, Add & Clean Up',
          message: err.message,
          color: 'warn',
        })
        return
      }
      const message = err instanceof ApiError ? err.message : String(err)
      notifications.show({ title: 'Search, Add & Clean Up - Error', message, color: 'danger' })
    },
  })

  const running = statusQuery.data?.status === 'running' || start.isPending

  return {
    status: statusQuery.data,
    running,
    start: (safe: boolean) => start.mutate(safe),
    progressLabel: running ? formatPipelineProgress(statusQuery.data?.progress) : null,
  }
}
