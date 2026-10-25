// Locations tab: region -> system -> location on the left, the chosen
// location's owner -> hangar -> container -> item tree on the right
// (docs/ASSET_MANAGER_PLAN.md phase 3). A ship opens the ship view.
import { useState } from 'react'
import {
  ActionIcon, Badge, Grid, Group, Paper, ScrollArea, Skeleton, Stack, Switch, Text, TextInput, Tooltip, UnstyledButton,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { IconChevronDown, IconChevronRight, IconSearch, IconShip } from '@tabler/icons-react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { assetManagerApi } from '../../api/client'
import type { AssetQuery, AssetTreeNode } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { isk, qty, volume } from '../../format'
import { AssetBadges } from './common'
import { securityColor } from './assetQuery'
import { ShipDrawer, type ShipRef } from './ShipDrawer'
import { TypeDetailDrawer } from './TypeDetailDrawer'

function Totals({ node }: { node: AssetTreeNode }) {
  return (
    <Group gap="md" wrap="nowrap" style={{ flexShrink: 0 }}>
      {node.quantity != null && <Text size="xs" c="dimmed" w={90} ta="right">{qty(node.quantity)}</Text>}
      <Text size="xs" w={110} ta="right">{isk(node.value)}{node.unpriced ? <Text span c="orange"> *</Text> : null}</Text>
    </Group>
  )
}

function TreeNode({ node, depth, onType, onShip }: {
  node: AssetTreeNode; depth: number; onType: (id: number) => void; onShip: (ref: ShipRef) => void
}) {
  const [open, setOpen] = useState(depth < 2)
  const hasChildren = node.children.length > 0
  return (
    <div>
      <Group gap={4} wrap="nowrap" justify="space-between" py={2} pl={depth * 16}
        style={{ borderBottom: '1px solid var(--mantine-color-dark-6)' }}>
        <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
          {hasChildren ? (
            <ActionIcon size="xs" variant="subtle" onClick={() => setOpen((o) => !o)} aria-label={open ? 'Collapse' : 'Expand'}>
              {open ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
            </ActionIcon>
          ) : <span style={{ width: 18, display: 'inline-block' }} />}
          {node.type_id != null && ['item', 'container', 'ship'].includes(node.kind) ? (
            <UnstyledButton onClick={() => onType(node.type_id as number)}>
              <Text size="sm" truncate>{node.label}</Text>
            </UnstyledButton>
          ) : (
            <Text size="sm" fw={node.kind === 'owner' || node.kind === 'source' ? 700 : 600} truncate>{node.label}</Text>
          )}
          {node.kind === 'ship' && node.item_id && node.owner_type && (
            <Tooltip label="Open ship view" withArrow>
              <ActionIcon size="xs" variant="subtle" aria-label="Open ship view"
                onClick={() => onShip({ ownerType: node.owner_type as string, ownerId: node.owner_id as number, itemId: node.item_id as number })}>
                <IconShip size={12} />
              </ActionIcon>
            </Tooltip>
          )}
          {node.section && <Badge size="xs" variant="outline" color="gray">{node.section}</Badge>}
          <AssetBadges badges={node.badges} />
          {node.owner_name && node.kind === 'item' && <Text size="xs" c="dimmed" truncate>{node.owner_name}</Text>}
        </Group>
        <Totals node={node} />
      </Group>
      {open && node.children.map((child) => (
        <TreeNode key={child.key} node={child} depth={depth + 1} onType={onType} onShip={onShip} />
      ))}
    </div>
  )
}

export default function AssetLocations() {
  const [params, setParams] = useSearchParams()
  const [text, setText] = useState(params.get('q') ?? '')
  const [debounced] = useDebouncedValue(text, 300)
  const [stockOnly, setStockOnly] = useState(params.get('stock_only') === 'true')
  const selected = params.get('location')
  const [typeId, setTypeId] = useState<number | null>(null)
  const [ship, setShip] = useState<ShipRef | null>(null)
  const filter: AssetQuery = { q: debounced || undefined, stock_only: stockOnly || undefined }

  const summary = useQuery({
    queryKey: ['asset-manager', 'locations', filter],
    queryFn: () => assetManagerApi.locations(filter),
    placeholderData: keepPreviousData,
  })
  const tree = useQuery({
    queryKey: ['asset-manager', 'tree', selected, filter],
    queryFn: () => assetManagerApi.locationTree(Number(selected), filter),
    enabled: selected !== null,
    placeholderData: keepPreviousData,
  })

  const select = (locationId: number | null | undefined) => {
    const next = new URLSearchParams(params)
    next.set('location', String(locationId ?? 0))
    setParams(next)
  }

  const allLocations = (summary.data?.regions ?? []).flatMap((r) => r.children.flatMap((s) => s.children))
  const selectedNode = allLocations.find((l) => String(l.location_id ?? 0) === selected)

  return (
    <Stack>
      <Group gap="md" align="flex-end">
        <TextInput label="Search" placeholder="Item, container, owner…" leftSection={<IconSearch size={14} />}
          value={text} onChange={(e) => setText(e.currentTarget.value)} w={280} />
        <Switch label="Stock only" checked={stockOnly} onChange={(e) => setStockOnly(e.currentTarget.checked)} />
      </Group>
      {summary.data && summary.data.regions.length === 0 && <HintCard>Nothing matches this filter.</HintCard>}
      <Grid>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Paper withBorder p="xs">
            <ScrollArea.Autosize mah={640}>
              {summary.isLoading && <Skeleton h={200} />}
              {(summary.data?.regions ?? []).map((region) => (
                <div key={region.key}>
                  <Group justify="space-between" mt="xs">
                    <Text size="sm" fw={700}>{region.label}</Text>
                    <Text size="xs">{isk(region.value)}</Text>
                  </Group>
                  {region.children.map((system) => (
                    <div key={system.key}>
                      <Group justify="space-between" pl="xs">
                        <Text size="xs" fw={600}>
                          {system.label}{' '}
                          {system.security != null && <Text span size="xs" c={securityColor(system.security)}>{system.security.toFixed(1)}</Text>}
                        </Text>
                        <Text size="xs" c="dimmed">{isk(system.value)}</Text>
                      </Group>
                      {system.children.map((loc) => {
                        const active = String(loc.location_id ?? 0) === selected
                        return (
                          <UnstyledButton key={loc.key} onClick={() => select(loc.location_id)} w="100%"
                            pl="md" py={2} bg={active ? 'var(--mantine-color-dark-5)' : undefined} style={{ borderRadius: 4 }}>
                            <Group justify="space-between" wrap="nowrap">
                              <Text size="xs" truncate c={loc.named === false && loc.location_id ? 'dimmed' : undefined}>{loc.label}</Text>
                              <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>{isk(loc.value)}</Text>
                            </Group>
                          </UnstyledButton>
                        )
                      })}
                    </div>
                  ))}
                </div>
              ))}
            </ScrollArea.Autosize>
          </Paper>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 8 }}>
          <Paper withBorder p="xs">
            {selected === null && <Text size="sm" c="dimmed">Pick a location on the left.</Text>}
            {selectedNode && (
              <Group justify="space-between" mb="xs">
                <Text fw={700}>{selectedNode.label}</Text>
                <Text size="xs" c="dimmed">
                  {qty(selectedNode.stacks)} stacks · {volume(selectedNode.volume)} · {isk(selectedNode.value)}
                </Text>
              </Group>
            )}
            {selectedNode && selectedNode.named === false && selectedNode.location_id ? (
              <Text size="xs" c="dimmed" mb="xs">
                This structure has no name yet. Resolve it from Production (Logistics) or give it a manual name there.
              </Text>
            ) : null}
            {tree.isLoading && <Skeleton h={200} />}
            <ScrollArea.Autosize mah={640}>
              {(tree.data?.nodes ?? []).map((node) => (
                <TreeNode key={node.key} node={node} depth={0} onType={setTypeId} onShip={setShip} />
              ))}
            </ScrollArea.Autosize>
          </Paper>
        </Grid.Col>
      </Grid>
      <TypeDetailDrawer typeId={typeId} onClose={() => setTypeId(null)} />
      <ShipDrawer ship={ship} onClose={() => setShip(null)} />
    </Stack>
  )
}
