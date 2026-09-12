import type { AssetPlanJob } from '../../api/types'
import { qty } from '../../format'

export function blockedRunsTitle(row: AssetPlanJob): string | undefined {
  const blocked = row.job_runs - row.runs_ready_now
  if (blocked <= 0) return undefined
  if (!row.blockers?.length) return 'Missing materials (see this job\'s bill of materials)'
  return row.blockers.map((b) => {
    const missing = Math.max(0, b.needed - b.covered)
    if (b.covered > 0) {
      return `${b.type_name}: ${qty(missing)} missing (${qty(b.covered)} of ${qty(b.needed)} on hand for this job)`
    }
    return `${b.type_name}: ${qty(missing)} missing`
  }).join('\n')
}
