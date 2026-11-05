import { Badge, Tooltip } from '@mantine/core'

import { qty } from '../../format'

// Stock in the Strategic Reserve that could cover a shortfall. A hint only: the
// reserve never counts as stockpile, so the shortfall stays as it is.
export function ReserveHint({ value }: { value: number | null | undefined }) {
  if (!value || value <= 0) return <>–</>
  return (
    <Tooltip multiline w={280}
      label="Held in the Strategic Reserve. It does not count as stockpile, so the shortfall stays. Taking it from there lowers the reserve.">
      <Badge component="span" size="sm" color="warn" variant="light" style={{ textTransform: 'none' }}>
        {qty(value)} in reserve
      </Badge>
    </Tooltip>
  )
}
