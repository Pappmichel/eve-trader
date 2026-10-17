// Display color for a coverage fraction the API already computed.
// Green >= 90%, yellow >= 70%, red below. Null stays unknown (never painted as 0%).
// "estimated" is summary.estimated / flags.estimated from the API, not this helper.

export type CoverageColor = 'accent' | 'warn' | 'danger'

export function coverageColor(coverage: number | null | undefined): CoverageColor | null {
  if (coverage === null || coverage === undefined || Number.isNaN(coverage)) return null
  if (coverage >= 0.9) return 'accent'
  if (coverage >= 0.7) return 'warn'
  return 'danger'
}
