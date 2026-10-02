import { useQuery } from '@tanstack/react-query'
import { Select, Text } from '@mantine/core'

import { sdeApi } from '../api/client'

interface Props {
  label: string
  value: number
  onChange: (regionId: number) => void
}

/**
 * Region picker backed by the SDE's region names (GitHub issue #223).
 * The stored value stays a numeric region id. When the name table is empty
 * (SDE not refreshed since sde_regions was added) or the current id is not in
 * it, the id is still offered as "Region <id>" so the value is never dropped.
 */
export function RegionSelect({ label, value, onChange }: Props) {
  const { data: regions } = useQuery({
    queryKey: ['sde', 'regions'],
    queryFn: sdeApi.regions,
    staleTime: 10 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const list = regions ?? []
  const data = list.map((r) => ({ value: String(r.region_id), label: r.region_name }))
  if (!list.some((r) => r.region_id === value)) {
    data.unshift({ value: String(value), label: `Region ${value}` })
  }

  return (
    <div>
      <Select label={label} searchable data={data} value={String(value)}
        onChange={(v) => { if (v) onChange(Number(v)) }} />
      {regions && list.length === 0 && (
        <Text size="xs" c="dimmed" mt={4}>Region names appear after the next SDE refresh</Text>
      )}
    </div>
  )
}
