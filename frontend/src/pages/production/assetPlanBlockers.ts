import type { AssetPlanJob } from '../../api/types'
import { qty } from '../../format'

// `blocked` overrides the job's own blocked runs (the Asset-Optimized order
// filter shows only the order or stock-target share); the missing materials
// listed are always the whole job's.
export function blockedRunsTitle(row: AssetPlanJob, blocked = row.job_runs - row.runs_ready_now): string | undefined {
  if (blocked <= 0) return undefined
  if (!row.blockers?.length) return 'Missing materials (see this job\'s bill of materials)'
  return row.blockers.map((b) => {
    const missing = Math.max(0, b.needed - b.covered)
    const elsewhere = (b.elsewhere ?? 0) > 0 ? ` - ${qty(b.elsewhere ?? 0)} at other locations` : ''
    if (b.covered > 0) {
      return `${b.type_name}: ${qty(missing)} missing (${qty(b.covered)} of ${qty(b.needed)} on hand for this job)${elsewhere}`
    }
    return `${b.type_name}: ${qty(missing)} missing${elsewhere}`
  }).join('\n')
}
