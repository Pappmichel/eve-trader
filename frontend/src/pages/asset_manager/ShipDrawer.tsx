// An assembled ship: fitting and holds, EFT export and the Doctrine match
// (docs/ASSET_MANAGER_PLAN.md F-1).
import { Badge, Button, CopyButton, Drawer, Group, Loader, Progress, Stack, Table, Text, Textarea } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { assetManagerApi } from '../../api/client'
import { isk, qty } from '../../format'

export interface ShipRef { ownerType: string; ownerId: number; itemId: number }

export function ShipDrawer({ ship, onClose }: { ship: ShipRef | null; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['asset-manager', 'ship', ship?.ownerType, ship?.ownerId, ship?.itemId],
    queryFn: () => assetManagerApi.ship(ship!.ownerType, ship!.ownerId, ship!.itemId),
    enabled: ship !== null,
  })
  const title = data ? (data.ship.item_name ? `${data.ship.item_name} (${data.ship.type_name})` : data.ship.type_name) : 'Ship'
  return (
    <Drawer opened={ship !== null} onClose={onClose} position="right" size="lg" title={<Text fw={700} size="lg">{title}</Text>}>
      {isLoading && <Loader size="sm" />}
      {error && <Text c="red" size="sm">{String((error as Error).message)}</Text>}
      {data && (
        <Stack>
          <Text size="sm" c="dimmed">
            {data.ship.owner_name} · {data.ship.location_name ?? 'Unknown location'} · hull {isk(data.ship.value)} ·
            contents {isk(data.contents_value)} · total {isk(data.total_value)}
          </Text>

          {data.sections.map((section) => (
            <div key={section.label}>
              <Text size="sm" fw={600} mb={4}>{section.label}</Text>
              <Table fz="xs" withTableBorder>
                <Table.Tbody>
                  {section.items.map((item) => (
                    <Table.Tr key={item.key}>
                      <Table.Td>{item.item_name ? `${item.item_name} (${item.type_name})` : item.type_name}</Table.Td>
                      <Table.Td ta="right" w={90}>{qty(item.quantity)}</Table.Td>
                      <Table.Td ta="right" w={120}>{isk(item.value)}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          ))}

          <div>
            <Group justify="space-between" mb={4}>
              <Text size="sm" fw={600}>EFT</Text>
              <CopyButton value={data.eft}>
                {({ copied, copy }) => <Button size="xs" variant="light" onClick={copy}>{copied ? 'Copied' : 'Copy EFT'}</Button>}
              </CopyButton>
            </Group>
            <Textarea value={data.eft} readOnly autosize minRows={4} maxRows={16} styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }} />
          </div>

          {data.doctrine_visible && (
            <div>
              <Text size="sm" fw={600} mb={4}>Doctrine fittings for this hull</Text>
              {data.doctrine_matches.length === 0 ? (
                <Text size="xs" c="dimmed">No active doctrine fitting uses this hull.</Text>
              ) : data.doctrine_matches.map((m) => (
                <Stack key={m.fitting_id} gap={2} mb="xs">
                  <Group justify="space-between">
                    <Text size="sm">{m.doctrine_name} · {m.fitting_name}{m.variant_label ? ` (${m.variant_label})` : ''}</Text>
                    <Badge color={m.matches ? 'teal' : 'gray'} variant="light">
                      {m.matches ? 'Matches' : 'Does not match'} · {Math.round(m.score * 100)}%
                    </Badge>
                  </Group>
                  <Progress value={m.score * 100} size="sm" color={m.matches ? 'teal' : 'gray'} />
                  {m.missing.length > 0 && (
                    <Text size="xs" c="dimmed">
                      Missing: {m.missing.map((x) => `${x.type_name} x${qty(x.quantity)}`).join(', ')}
                    </Text>
                  )}
                </Stack>
              ))}
            </div>
          )}
        </Stack>
      )}
    </Drawer>
  )
}
