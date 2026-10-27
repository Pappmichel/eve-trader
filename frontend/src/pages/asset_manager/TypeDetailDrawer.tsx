// One owned type across every owner, place and source, with a 90-day price
// history (docs/ASSET_MANAGER_PLAN.md F-5).
import { Drawer, Group, Loader, SimpleGrid, Stack, Table, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { assetManagerApi } from '../../api/client'
import { isk, qty, volume } from '../../format'
import { COLORS } from '../../theme'
import { AssetBadges, WantedBy } from './common'
import { SOURCE_LABELS } from './assetQuery'
import { locationLabel } from './locationLabel'

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <Text size="xs" c="dimmed">{label}</Text>
      <Text fw={600}>{value}</Text>
    </div>
  )
}

export function TypeDetailDrawer({ typeId, onClose }: { typeId: number | null; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['asset-manager', 'type', typeId],
    queryFn: () => assetManagerApi.typeDetail(typeId as number),
    enabled: typeId !== null,
  })
  return (
    <Drawer opened={typeId !== null} onClose={onClose} position="right" size="xl"
      title={<Text fw={700} size="lg">{data?.type_name ?? 'Item'}</Text>}>
      {isLoading && <Loader size="sm" />}
      {error && <Text c="red" size="sm">{String((error as Error).message)}</Text>}
      {data && (
        <Stack>
          <Text size="sm" c="dimmed">{[data.category_name, data.group_name].filter(Boolean).join(' / ')}</Text>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <Stat label="Quantity" value={qty(data.totals.quantity)} />
            <Stat label="Value" value={isk(data.totals.value)} />
            <Stat label="Unit price" value={isk(data.unit_price)} />
            <Stat label="Volume" value={volume(data.totals.volume)} />
          </SimpleGrid>
          {data.wanted_by.length > 0 && (
            <Group gap="xs"><Text size="sm">Wanted by:</Text><WantedBy wanted={data.wanted_by} /></Group>
          )}

          <div>
            <Text size="sm" fw={600} mb={4}>Average price, last 90 days</Text>
            {data.price_history.points.length === 0 ? (
              <Text size="xs" c="dimmed">No price history available for this type.</Text>
            ) : (
              <div style={{ height: 180 }}>
                <ResponsiveContainer>
                  <LineChart data={data.price_history.points} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                    <CartesianGrid stroke={COLORS.border} vertical={false} />
                    <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} minTickGap={32} />
                    <YAxis stroke={COLORS.textDim} tick={{ fontSize: 11 }} width={72}
                      tickFormatter={(v: number) => isk(v)} domain={['auto', 'auto']} />
                    <Tooltip
                      contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}`, color: COLORS.text }}
                      formatter={(v) => [isk(Number(v)), 'Average price']} />
                    <Line type="monotone" dataKey="avg_price" stroke={COLORS.accent} strokeWidth={2} dot={false}
                      activeDot={{ r: 4 }} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          <div>
            <Text size="sm" fw={600} mb={4}>Where it is</Text>
            <Table striped withTableBorder fz="xs">
              <Table.Thead>
                <Table.Tr><Table.Th>Location</Table.Th><Table.Th>Owners</Table.Th><Table.Th>Sources</Table.Th>
                  <Table.Th ta="right">Quantity</Table.Th><Table.Th ta="right">Value</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.places.map((p) => (
                  <Table.Tr key={p.key}>
                    <Table.Td>{locationLabel(p)}
                      {p.solar_system_name && <Text span size="xs" c="dimmed"> · {p.solar_system_name}</Text>}</Table.Td>
                    <Table.Td>{p.owners.join(', ')}</Table.Td>
                    <Table.Td>{p.sources.map((s) => SOURCE_LABELS[s] ?? s).join(', ')}</Table.Td>
                    <Table.Td ta="right">{qty(p.quantity)}</Table.Td>
                    <Table.Td ta="right">{isk(p.value)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>

          <div>
            <Text size="sm" fw={600} mb={4}>Stacks</Text>
            <Table withTableBorder fz="xs">
              <Table.Thead>
                <Table.Tr><Table.Th>Owner</Table.Th><Table.Th>Place</Table.Th><Table.Th>Badges</Table.Th>
                  <Table.Th ta="right">Quantity</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.stacks.map((s) => (
                  <Table.Tr key={s.key}>
                    <Table.Td>{s.owner_name}</Table.Td>
                    <Table.Td>{[locationLabel(s), s.hangar, ...(s.container_path ?? [])].filter(Boolean).join(' › ')}</Table.Td>
                    <Table.Td><AssetBadges badges={s.badges} /></Table.Td>
                    <Table.Td ta="right">{qty(s.quantity)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
        </Stack>
      )}
    </Drawer>
  )
}
