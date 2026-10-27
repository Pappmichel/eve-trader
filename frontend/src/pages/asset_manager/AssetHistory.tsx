// History tab: what arrived and what left between two daily snapshots
// (docs/ASSET_MANAGER_PLAN.md phase 6), valued at today's prices.
import { useState } from 'react'
import { Group, Paper, Select, SimpleGrid, Skeleton, Stack, Switch, Table, Text } from '@mantine/core'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { assetManagerApi } from '../../api/client'
import { HintCard } from '../../components/HintCard'
import { isk, qty } from '../../format'
import { locationLabel } from './locationLabel'

export default function AssetHistory() {
  const [fromDate, setFromDate] = useState<string | null>(null)
  const [toDate, setToDate] = useState<string | null>(null)
  const [byOwner, setByOwner] = useState(false)
  const history = useQuery({
    queryKey: ['asset-manager', 'history', fromDate, toDate, byOwner],
    queryFn: () => assetManagerApi.history(fromDate, toDate, byOwner),
    placeholderData: keepPreviousData,
  })
  const data = history.data
  const dates = (data?.dates ?? []).slice().reverse()

  return (
    <Stack>
      <HintCard>
        One snapshot per day of every owner&apos;s ESI assets (per location and item), kept for 90 days
        (operator setting asset_history_retention_days). Changes are valued at today&apos;s price.
      </HintCard>
      <Group align="flex-end">
        <Select label="From" w={160} data={dates} value={data?.from_date ?? null} onChange={setFromDate} />
        <Select label="To" w={160} data={dates} value={data?.to_date ?? null} onChange={setToDate} />
        <Switch label="Per owner" checked={byOwner} onChange={(e) => setByOwner(e.currentTarget.checked)} />
      </Group>
      {history.isLoading && <Skeleton h={200} />}
      {history.isError && <Text c="red" size="sm">{String((history.error as Error).message)}</Text>}
      {data && data.dates.length < 2 && (
        <Text size="sm" c="dimmed">
          Only {data.dates.length} day recorded so far - changes appear from the second day on.
        </Text>
      )}
      {data && data.rows.length > 0 && (
        <>
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="xs">
            <Paper p="xs" withBorder><Text size="xs" c="dimmed">Arrived</Text><Text fw={600} c="teal">{isk(data.arrived_value ?? 0)}</Text></Paper>
            <Paper p="xs" withBorder><Text size="xs" c="dimmed">Left</Text><Text fw={600} c="red">{isk(data.left_value ?? 0)}</Text></Paper>
            <Paper p="xs" withBorder><Text size="xs" c="dimmed">Changed rows</Text><Text fw={600}>{qty(data.total_rows ?? data.rows.length)}</Text></Paper>
          </SimpleGrid>
          <Paper withBorder style={{ overflowX: 'auto' }}>
            <Table striped fz="sm" miw={700}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Item</Table.Th><Table.Th>Location</Table.Th>
                  <Table.Th ta="right">Before</Table.Th><Table.Th ta="right">After</Table.Th>
                  <Table.Th ta="right">Change</Table.Th><Table.Th ta="right">Value change</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.rows.map((r) => (
                  <Table.Tr key={`${r.type_id}:${r.location_id}:${r.owner_type ?? ''}:${r.owner_id ?? ''}`}>
                    <Table.Td>{r.type_name}</Table.Td>
                    <Table.Td>{locationLabel(r, 'Unknown')}
                      {r.solar_system_name && <Text span size="xs" c="dimmed"> · {r.solar_system_name}</Text>}</Table.Td>
                    <Table.Td ta="right">{qty(r.before)}</Table.Td>
                    <Table.Td ta="right">{qty(r.after)}</Table.Td>
                    <Table.Td ta="right" c={r.delta > 0 ? 'teal' : 'red'}>{r.delta > 0 ? '+' : ''}{qty(r.delta)}</Table.Td>
                    <Table.Td ta="right">{r.value_delta == null ? '–' : isk(r.value_delta)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Paper>
        </>
      )}
      {data && data.dates.length >= 2 && data.rows.length === 0 && (
        <Text size="sm" c="dimmed">No changes between these two days.</Text>
      )}
    </Stack>
  )
}
