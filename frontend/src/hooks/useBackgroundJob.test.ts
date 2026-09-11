import { describe, expect, it } from 'vitest'

import { formatBackgroundProgress } from './useBackgroundJob'

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
