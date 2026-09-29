import type { SkillQueueWarning } from '../../../api/types'

// One place that words the queue guard, used by the overview badge, the page
// banner and the hub badge so they always agree.
export function warningText(w: SkillQueueWarning): string {
  switch (w.kind) {
    case 'empty': return 'queue empty'
    case 'paused': return 'queue paused'
    case 'ended': return 'queue finished'
    case 'ends_soon': {
      const h = w.hours_left ?? 0
      return h < 1 ? 'ends in under 1 h' : `ends in ${h < 10 ? h.toFixed(1) : Math.round(h)} h`
    }
  }
}

export function warningSummary(count: number): string | undefined {
  return count > 0 ? `${count} queue warning${count === 1 ? '' : 's'}` : undefined
}
