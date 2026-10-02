import { Select } from '@mantine/core'

import { hubSelectData } from '../tradingHubs'

interface Props {
  label: string
  description?: string
  value: number
  onChange: (regionId: number) => void
}

// One market-hub dropdown shared by the per-tool Settings pages (GitHub issue
// #222). A stored region id that isn't one of TRADE_HUBS stays selectable as
// a "Custom" option instead of silently snapping to Jita (same behaviour as
// Trading's own Buy hub select).
export function HubSelect({ label, description, value, onChange }: Props) {
  return (
    <Select label={label} description={description} data={hubSelectData(value)} value={String(value)}
      onChange={(v) => v && onChange(Number(v))} allowDeselect={false} />
  )
}
