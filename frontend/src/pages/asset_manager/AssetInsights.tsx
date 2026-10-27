// Insights tab: consolidation candidates, forgotten assets and Asset Safety
// (docs/ASSET_MANAGER_PLAN.md phase 7).
import { useState } from 'react'
import { NumberInput, Paper, Skeleton, Stack, Table, Text, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { assetManagerApi } from '../../api/client'
import { isk, qty, volume } from '../../format'
import { locationLabel } from './locationLabel'

function Section({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <Stack gap={4}>
      <Title order={5}>{title}</Title>
      <Text size="xs" c="dimmed">{hint}</Text>
      <Paper withBorder style={{ overflowX: 'auto' }}>{children}</Paper>
    </Stack>
  )
}

export default function AssetInsights() {
  const [minValue, setMinValue] = useState<number>(10_000_000)
  const [debounced] = useDebouncedValue(minValue, 400)
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['asset-manager', 'insights', debounced],
    queryFn: () => assetManagerApi.insights(debounced),
    placeholderData: keepPreviousData,
  })
  if (isLoading) return <Skeleton h={300} />
  if (isError) return <Text c="red" size="sm">{String((error as Error).message)}</Text>
  if (!data) return null
  const empty = (cols: number, text: string) => (
    <Table.Tr><Table.Td colSpan={cols}><Text size="sm" c="dimmed">{text}</Text></Table.Td></Table.Tr>
  )

  return (
    <Stack gap="lg">
      <Section title="Consolidation"
        hint={data.home_location_id
          ? `Items stored in more than one place. Freight to the home structure at ${isk(data.haul_cost_per_m3)}/m³ (Production settings).`
          : 'Items stored in more than one place. Set a home structure in Production settings to see freight to it.'}>
        <Table striped fz="sm" miw={700}>
          <Table.Thead><Table.Tr>
            <Table.Th>Item</Table.Th><Table.Th ta="right">Places</Table.Th><Table.Th ta="right">Total</Table.Th>
            <Table.Th ta="right">Outside home</Table.Th><Table.Th ta="right">Value outside</Table.Th>
            <Table.Th ta="right">Volume outside</Table.Th><Table.Th ta="right">Freight</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {data.consolidation.length === 0 && empty(7, 'Nothing is spread over several places.')}
            {data.consolidation.map((r) => (
              <Table.Tr key={r.type_id}>
                <Table.Td>{r.type_name}</Table.Td><Table.Td ta="right">{r.location_count}</Table.Td>
                <Table.Td ta="right">{qty(r.quantity)}</Table.Td><Table.Td ta="right">{qty(r.outside_quantity)}</Table.Td>
                <Table.Td ta="right">{isk(r.outside_value)}</Table.Td><Table.Td ta="right">{volume(r.outside_volume)}</Table.Td>
                <Table.Td ta="right">{isk(r.freight_cost)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Section>

      <Section title="Forgotten assets"
        hint="Stock at places no tool uses (not the home or Trading structure), of items no tool you hold wants, worth at least the amount below per item and place.">
        <Stack p="xs" gap="xs">
          <NumberInput label="Minimum value" w={220} value={minValue} min={0} step={1_000_000} thousandSeparator=","
            onChange={(v) => setMinValue(Number(v) || 0)} />
          <Table striped fz="sm" miw={700}>
            <Table.Thead><Table.Tr>
              <Table.Th>Item</Table.Th><Table.Th>Location</Table.Th><Table.Th>Owners</Table.Th>
              <Table.Th ta="right">Quantity</Table.Th><Table.Th ta="right">Value</Table.Th><Table.Th ta="right">Volume</Table.Th>
            </Table.Tr></Table.Thead>
            <Table.Tbody>
              {data.forgotten.length === 0 && empty(6, 'Nothing above that value.')}
              {data.forgotten.map((r) => (
                <Table.Tr key={`${r.type_id}:${r.location_id}`}>
                  <Table.Td>{r.type_name}</Table.Td>
                  <Table.Td>{locationLabel(r, 'Unknown')}
                    {r.solar_system_name && <Text span size="xs" c="dimmed"> · {r.solar_system_name}</Text>}</Table.Td>
                  <Table.Td>{r.owners.join(', ')}</Table.Td><Table.Td ta="right">{qty(r.quantity)}</Table.Td>
                  <Table.Td ta="right">{isk(r.value)}</Table.Td><Table.Td ta="right">{volume(r.volume)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      </Section>

      <Section title="Asset Safety"
        hint="Items in Asset Safety wraps. ESI does not report when an item went into Asset Safety, so no timer is shown - check the delivery deadline in game.">
        <Table striped fz="sm" miw={600}>
          <Table.Thead><Table.Tr>
            <Table.Th>Owner</Table.Th><Table.Th>Where</Table.Th><Table.Th ta="right">Stacks</Table.Th>
            <Table.Th ta="right">Value</Table.Th><Table.Th ta="right">Volume</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {data.asset_safety.length === 0 && empty(5, 'Nothing in Asset Safety.')}
            {data.asset_safety.map((r) => (
              <Table.Tr key={`${r.owner_name}:${r.location_id}`}>
                <Table.Td>{r.owner_name}</Table.Td>
                <Table.Td>{locationLabel(r, 'Unknown')}
                  {r.solar_system_name && <Text span size="xs" c="dimmed"> · {r.solar_system_name}</Text>}</Table.Td>
                <Table.Td ta="right">{r.stacks}</Table.Td>
                <Table.Td ta="right">{isk(r.value)}{r.unpriced ? <Text span c="orange"> *</Text> : null}</Table.Td>
                <Table.Td ta="right">{volume(r.volume)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Section>
    </Stack>
  )
}
