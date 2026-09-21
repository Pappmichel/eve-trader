import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { notifications } from '@mantine/notifications'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PipelineRunStatus } from '../api/types'
import {
  formatBackgroundProgress,
  formatDegradedJobMessage,
  shouldPollBackgroundJob,
  useBackgroundJob,
} from './useBackgroundJob'

vi.mock('@mantine/notifications', () => ({
  notifications: { show: vi.fn() },
}))

describe('formatBackgroundProgress', () => {
  it('formats Trading search batches with scored/skipped counts', () => {
    expect(formatBackgroundProgress({
      phase: 'search', batch: 2, total_batches: 5, evaluated: 40, skipped: 3,
    })).toBe('Batch 2/5, 40 items scored, 3 skipped')
  })

  it('formats generic sync batches with the message (Doctrine contract sync)', () => {
    expect(formatBackgroundProgress({
      phase: 'sync', batch: 4, total_batches: 12, message: 'Fetching contract items',
    }, 'sync_contracts', { sync_contracts: 'Sync Contracts' })).toBe(
      'Sync Contracts: Batch 4/12 (Fetching contract items)',
    )
  })

  it('formats generic run batches with the message (Admin SDE refresh)', () => {
    expect(formatBackgroundProgress({
      phase: 'run', batch: 3, total_batches: 13, message: 'Fetching invGroups.csv',
    }, 'sde_refresh', { sde_refresh: 'Refresh SDE' })).toBe(
      'Refresh SDE: Batch 3/13 (Fetching invGroups.csv)',
    )
  })

  it('falls back to Running… when progress is empty', () => {
    expect(formatBackgroundProgress(null, 'sync_contracts', { sync_contracts: 'Sync Contracts' }))
      .toBe('Sync Contracts: Running…')
  })
})

describe('shouldPollBackgroundJob', () => {
  it('polls only while status is running, including the new degraded terminal', () => {
    expect(shouldPollBackgroundJob('running')).toBe(true)
    expect(shouldPollBackgroundJob('succeeded')).toBe(false)
    expect(shouldPollBackgroundJob('failed')).toBe(false)
    expect(shouldPollBackgroundJob('degraded')).toBe(false)
    expect(shouldPollBackgroundJob('idle')).toBe(false)
    expect(shouldPollBackgroundJob(undefined)).toBe(false)
  })
})

describe('formatDegradedJobMessage', () => {
  it('names failed steps and their error text from failed_steps', () => {
    expect(formatDegradedJobMessage({
      run_id: 'r1',
      status: 'degraded',
      result: {
        failed_steps: { refresh_and_prune_candidates: 'ESI timeout' },
        reconcile_trades: { matched_trades: 2 },
      },
    })).toBe('refresh_and_prune_candidates: ESI timeout')
  })
})

function makeWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children)
  }
}

function runningStatus(): PipelineRunStatus {
  return {
    run_id: 'run-1',
    job_name: 'pipeline',
    status: 'running',
    result: null,
    error: null,
  }
}

function degradedStatus(): PipelineRunStatus {
  return {
    run_id: 'run-1',
    job_name: 'pipeline',
    status: 'degraded',
    result: {
      refresh_and_prune_candidates: { error: 'ESI timeout' },
      reconcile_trades: { matched_trades: 2 },
      failed_steps: { refresh_and_prune_candidates: 'ESI timeout' },
    },
    error: 'refresh_and_prune_candidates: ESI timeout',
  }
}

describe('useBackgroundJob degraded branch', () => {
  beforeEach(() => {
    vi.mocked(notifications.show).mockClear()
  })

  it('shows a warning naming the failed step and still invalidates result keys', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const onSucceeded = vi.fn()
    let payload: PipelineRunStatus = runningStatus()
    const fetchStatus = vi.fn(async () => payload)

    const { result } = renderHook(
      () => useBackgroundJob({
        queryKey: ['trading', 'job'],
        fetchStatus,
        resultKeys: [['trading', 'shortlist']],
        labels: { pipeline: 'Pipeline' },
        defaultLabel: 'Trading job',
        onSucceeded,
      }),
      { wrapper: makeWrapper(queryClient) },
    )

    await waitFor(() => {
      expect(result.current.status?.status).toBe('running')
    })

    payload = degradedStatus()
    queryClient.setQueryData(['trading', 'job'], payload)

    await waitFor(() => {
      expect(notifications.show).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Pipeline - Partial failure',
          message: 'refresh_and_prune_candidates: ESI timeout',
          color: 'warn',
        }),
      )
    })
    expect(onSucceeded).toHaveBeenCalledTimes(1)
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['trading', 'shortlist'] })
  })
})
