import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Group, NumberInput, Paper, Select, SimpleGrid, Stack, Text } from '@mantine/core'

import { hubsApi } from '../api/client'
import { useAction } from '../hooks/useAction'
import { useTradeHubs } from '../hooks/useTradeHubs'
import { ObservedFreightHint } from './ObservedFreightHint'
import { ALL_HUBS, hubSelectData } from '../tradingHubs'
import type { HubFreightRow } from '../api/types'

interface Props {
  label: string
  description?: string
  value: number
  onChange: (regionId: number) => void
  // Offer "All hubs (best per item)" (GitHub issue #222). Not for tools that
  // sit at one hub (Trading, Station Trading).
  allowAll?: boolean
}

// One market-hub dropdown shared by the per-tool Settings pages (GitHub issue
// #222). A stored region id that isn't one of the hub list stays selectable as
// a "Custom" option instead of silently snapping to Jita. With "All hubs"
// picked, the shared per-hub freight table is shown right below it.
export function HubSelect({ label, description, value, onChange, allowAll = false }: Props) {
  const { data: tradeHubs } = useTradeHubs()
  return (
    <Stack gap="xs">
      <Select label={label} description={description} data={hubSelectData(tradeHubs, value, allowAll)} value={String(value)}
        onChange={(v) => v !== null && onChange(Number(v))} allowDeselect={false} />
      {allowAll && value === ALL_HUBS && <HubFreightTable />}
    </Stack>
  )
}

const tableRows = (rows: HubFreightRow[], draft: Record<number, number | ''>) => rows.map((r) => ({
  region_id: r.region_id,
  freight_cost_per_m3: draft[r.region_id] === '' || draft[r.region_id] === undefined ? null : Number(draft[r.region_id]),
}))

// The tenant's shared freight table (ISK/m³ from each hub to the structure),
// used by every tool set to "All hubs". Saved on its own, independent of the
// surrounding Settings form.
export function HubFreightTable() {
  const { data } = useQuery({ queryKey: ['hubs', 'freight'], queryFn: hubsApi.freight })
  const [draft, setDraft] = useState<Record<number, number | ''>>({})
  useEffect(() => {
    if (data) setDraft(Object.fromEntries(data.map((r) => [r.region_id, r.freight_cost_per_m3 ?? ''])))
  }, [data])
  const save = useAction('Hub Freight', hubsApi.updateFreight, [['hubs', 'freight']], { tier: 'local' })

  if (!data) return null
  return (
    <Paper withBorder p="sm" radius="sm">
      <Text size="sm" fw={600}>Freight per hub (ISK/m³ to the structure)</Text>
      <Text size="xs" c="dimmed" mb="xs">
        Shared by every tool set to "All hubs". Each item is priced at the hub with the lowest cost
        including this freight. An empty field uses the tool's own freight value.
      </Text>
      <SimpleGrid cols={2}>
        {data.map((r) => (
          <Stack key={r.region_id} gap={4}>
            <NumberInput label={r.hub} min={0} step={50} value={draft[r.region_id] ?? ''}
              onChange={(v) => setDraft((d) => ({ ...d, [r.region_id]: v === '' ? '' : Number(v) }))} />
            <ObservedFreightHint compact regionId={r.region_id} applying={save.isPending}
              current={draft[r.region_id] === '' || draft[r.region_id] === undefined ? NaN : Number(draft[r.region_id])}
              onApply={(v) => { setDraft((d) => ({ ...d, [r.region_id]: v })); save.mutate(tableRows(data, { ...draft, [r.region_id]: v })) }} />
          </Stack>
        ))}
      </SimpleGrid>
      <Group justify="flex-end" mt="xs">
        <Button size="xs" loading={save.isPending} onClick={() => save.mutate(tableRows(data, draft))}>
          Save freight table
        </Button>
      </Group>
    </Paper>
  )
}
