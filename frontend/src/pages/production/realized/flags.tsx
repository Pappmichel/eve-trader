import { Badge, Group, Tooltip } from '@mantine/core'

import type { RealizedFlags } from '../../../api/types'
import { pct } from '../../../format'
import { coverageColor } from './coverage'
import { structureBonusText } from './labels'

export function CoverageBadge({ coverage }: { coverage: number | null | undefined }) {
  const color = coverageColor(coverage)
  if (color === null) {
    return <Badge size="xs" color="gray" variant="light">unknown</Badge>
  }
  return <Badge size="xs" color={color} variant="light">{pct(coverage)}</Badge>
}

function Marker({ label, tip, color }: { label: string; tip: string; color: string }) {
  return (
    <Tooltip label={tip} multiline w={240}>
      <Badge size="xs" color={color} variant="outline">{label}</Badge>
    </Tooltip>
  )
}

/** Every flag the API sent. `job` renders a null structure bonus as "unknown".
 * `showCoverage` is off where the row already has its own coverage column. */
export function FlagMarkers({ flags, job = false, showCoverage = true }: {
  flags: RealizedFlags
  job?: boolean
  showCoverage?: boolean
}) {
  return (
    <Group gap={4} wrap="wrap">
      {flags.estimated && <Marker label="estimated" tip="Part of this cost is an estimate (opening, unknown, or a missing price)." color="warn" />}
      {flags.price_missing && <Marker label="price missing" tip="A value had no price and was booked at 0." color="danger" />}
      {flags.assumed_me && <Marker label="assumed ME" tip="Material efficiency was assumed, not read from the blueprint." color="warn" />}
      {flags.assumed_decryptor && <Marker label="assumed decryptor" tip="The decryptor was derived, not stored on the job." color="warn" />}
      {flags.tax_missing && <Marker label="tax missing" tip="No sales-tax journal entry; revenue is gross." color="warn" />}
      {flags.detail_missing && <Marker label="detail missing" tip="The cost chain could not be expanded fully." color="warn" />}
      {showCoverage && flags.coverage != null && <CoverageBadge coverage={flags.coverage} />}
      {(job || flags.no_structure_bonus !== null) && (
        <Marker
          label={`structure bonus: ${structureBonusText(flags.no_structure_bonus)}`}
          tip={flags.no_structure_bonus === null
            ? 'Unknown whether the structure bonus applied. Older jobs stay unknown until the next rebuild that can see it.'
            : flags.no_structure_bonus
              ? 'No structure bonus was applied to this job.'
              : 'The structure bonus was applied.'}
          color={flags.no_structure_bonus === null ? 'gray' : flags.no_structure_bonus ? 'warn' : 'accent'}
        />
      )}
    </Group>
  )
}
