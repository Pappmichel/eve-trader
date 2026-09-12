import { describe, expect, it } from 'vitest'

import type { AssetPlanJob } from '../../api/types'
import { blockedRunsTitle } from './assetPlanBlockers'

function job(partial: Partial<AssetPlanJob>): AssetPlanJob {
  return {
    type_id: 1,
    type_name: 'Photonic Metamaterials',
    blueprint_type_id: 101,
    activity: 'Reaction',
    quantity: 2478,
    job_runs: 2478,
    runs_ready_now: 0,
    job_time_seconds: 0,
    unit_build_cost: null,
    decryptor: null,
    job_category: 'Reactions',
    margin: null,
    stock_coverage: null,
    recommended_slots: null,
    blockers: [],
    ...partial,
  }
}

describe('blockedRunsTitle', () => {
  it('is undefined when every run is ready', () => {
    expect(blockedRunsTitle(job({ job_runs: 10, runs_ready_now: 10 }))).toBeUndefined()
  })

  it('lists each short material and how much is missing', () => {
    expect(blockedRunsTitle(job({
      blockers: [
        { type_id: 30389, type_name: 'Fulleroferrocene', needed: 12480, covered: 0 },
        { type_id: 16670, type_name: 'Dysprosium', needed: 100, covered: 40 },
      ],
    }))).toBe(
      'Fulleroferrocene: 12,480 missing\n'
      + 'Dysprosium: 60 missing (40 of 100 on hand for this job)',
    )
  })
})
