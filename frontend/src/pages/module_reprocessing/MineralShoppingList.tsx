import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ActionIcon, Badge, Button, Card, Center, Group, Loader, NumberInput, Select, SimpleGrid, Stack, Table, Text,
  Title, Tooltip,
} from '@mantine/core'
import { IconCalculator, IconDeviceFloppy, IconDownload, IconPlus, IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { moduleReprocessingApi, productionApi } from '../../api/client'
import type {
  DirectMineralPurchase, MineralRequirement, ModuleShoppingListPlan, ReprocessMineralCoverage, ReprocessPurchase,
} from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'

// Same feature and UX shape as Ore & Minerals' own Mineral Shopping List
// (pages/refining/MineralShoppingList.tsx), but the optimizer also considers
// this tool's own active shortlisted T1/Meta modules/drones as reprocessing
// sources alongside compressed ore/ice - one combined plan, shown as two
// purchase tables split by each row's `category`.
export default function MineralShoppingList() {
  const { data: minerals } = useQuery({
    queryKey: ['module_reprocessing', 'shoppable-minerals'], queryFn: moduleReprocessingApi.shoppableMinerals,
  })
  const { data: saved } = useQuery({
    queryKey: ['module_reprocessing', 'shopping-requirements'], queryFn: moduleReprocessingApi.shoppingRequirements,
  })

  const [rows, setRows] = useState<MineralRequirement[]>([])
  useEffect(() => { if (saved) setRows(saved) }, [saved])

  const [newMineral, setNewMineral] = useState<string | null>(null)
  const [newQty, setNewQty] = useState<number | ''>('')
  const [plan, setPlan] = useState<ModuleShoppingListPlan | null>(null)

  const save = useAction('Save Requirements', moduleReprocessingApi.saveShoppingRequirements, [
    ['module_reprocessing', 'shopping-requirements'],
  ])
  // Solved from the on-screen list rather than the saved one, so the button
  // always reflects what the user is looking at - no "save first" step.
  const optimize = useAction('Optimize', (r: MineralRequirement[]) => moduleReprocessingApi.optimizeShoppingList(r), [],
    { tier: 'live', effect: 'Solves the buy/reprocess mix across ore, ice and shortlisted modules with current prices (cached with live fallback).' })

  // Same one-directional pull of Production's already-computed buy-list
  // shortfall as Ore & Minerals' own page (GitHub issue #94): reads GET
  // /api/production/plan, filters to minerals this page can source, and
  // fully replaces the on-screen rows - nothing is saved or solved by the
  // pull itself.
  const loadFromProduction = useAction('Load from Production', async () => {
    const plan = await productionApi.plan()
    if (!plan) {
      throw new Error("Production doesn't have a plan yet - click 'Refresh Production' in Production first.")
    }
    const mineralIds = new Set((minerals ?? []).map((m) => m.type_id))
    const shortfall = plan.buy_list.filter((e) => mineralIds.has(e.type_id) && e.quantity > 0)
    if (shortfall.length === 0) {
      throw new Error("Production's Buy List doesn't currently contain any minerals.")
    }
    return shortfall.map((e) => ({ type_id: e.type_id, name: e.type_name, required_qty: e.quantity }))
  })

  const usedIds = new Set(rows.map((r) => r.type_id))
  const options = (minerals ?? [])
    .filter((m) => !usedIds.has(m.type_id))
    .map((m) => ({ value: String(m.type_id), label: m.name }))

  const addRow = () => {
    if (!newMineral || !newQty || Number(newQty) <= 0) return
    const mineral = (minerals ?? []).find((m) => String(m.type_id) === newMineral)
    if (!mineral) return
    setRows((r) => [...r, { type_id: mineral.type_id, name: mineral.name, required_qty: Number(newQty) }])
    setNewMineral(null)
    setNewQty('')
  }

  const orePurchases = useMemo(() => (plan?.reprocess_purchases ?? []).filter((p) => p.category === 'ore'), [plan])
  const modulePurchases = useMemo(() => (plan?.reprocess_purchases ?? []).filter((p) => p.category === 'module'), [plan])

  const oreColumns = useMemo<ColumnDef<ReprocessPurchase, any>[]>(() => [
    { header: 'Buy in Jita', accessorKey: 'item', size: 220 },
    { header: 'Family', accessorKey: 'family', size: 130 },
    { header: 'Units', accessorKey: 'units', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Portions', accessorKey: 'portions', size: 100, cell: (i) => qty(i.getValue()) },
    { header: 'Volume (m3)', accessorKey: 'volume_m3', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Landed / Unit', accessorKey: 'landed_cost_per_unit', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Total Cost', accessorKey: 'total_cost', size: 140, cell: (i) => isk(i.getValue()) },
  ], [])

  const moduleColumns = useMemo<ColumnDef<ReprocessPurchase, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 260 },
    { header: 'Units', accessorKey: 'units', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Portions', accessorKey: 'portions', size: 100, cell: (i) => qty(i.getValue()) },
    { header: 'Volume (m³)', accessorKey: 'volume_m3', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Landed / Unit', accessorKey: 'landed_cost_per_unit', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Total Cost', accessorKey: 'total_cost', size: 140, cell: (i) => isk(i.getValue()) },
  ], [])

  const directColumns = useMemo<ColumnDef<DirectMineralPurchase, any>[]>(() => [
    { header: 'Buy Directly', accessorKey: 'name', size: 220 },
    { header: 'Quantity', accessorKey: 'quantity', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'Landed / Unit', accessorKey: 'landed_cost_per_unit', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Total Cost', accessorKey: 'total_cost', size: 140, cell: (i) => isk(i.getValue()) },
    {
      header: 'Source', accessorKey: 'source', size: 100,
      cell: (i) => <Badge size="sm" variant="light" color={i.getValue() === 'Home' ? 'accent' : 'gray'}>{i.getValue() ?? '–'}</Badge>,
    },
  ], [])

  const coverageColumns = useMemo<ColumnDef<ReprocessMineralCoverage, any>[]>(() => [
    { header: 'Mineral', accessorKey: 'name', size: 160 },
    { header: 'Required', accessorKey: 'required', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'From Reprocessing', accessorKey: 'from_reprocessing', size: 150, cell: (i) => qty(i.getValue()) },
    { header: 'Bought Directly', accessorKey: 'from_direct', size: 140, cell: (i) => qty(i.getValue()) },
    { header: 'Delivered', accessorKey: 'delivered', size: 120, cell: (i) => qty(i.getValue()) },
    {
      header: 'Surplus', accessorKey: 'surplus', size: 120,
      cell: (i) => <Text size="sm" c={(i.getValue() as number) < 0 ? 'danger' : undefined}>{qty(i.getValue())}</Text>,
    },
  ], [])

  if (!minerals || !saved) return <Center h={200}><Loader color="accent" /></Center>

  return (
    <Stack>
      <HintCard>
        Enter how many of each mineral you need. The optimizer finds the cheapest mix of buying compressed
        ore/ice or your active shortlisted modules and reprocessing them vs. buying the minerals outright, using
        landed prices plus Ore &amp; Minerals&apos; refining yield (ore/ice) and this tool&apos;s own Settings
        (modules).
      </HintCard>

      <Group align="flex-end">
        <Select label="Mineral" placeholder="Pick a mineral" searchable data={options}
          value={newMineral} onChange={setNewMineral} w={240} />
        <NumberInput label="Required quantity" placeholder="e.g. 1000000" min={1} value={newQty}
          onChange={(v) => setNewQty(v === '' ? '' : Number(v))} w={200} thousandSeparator />
        <Button variant="default" leftSection={<IconPlus size={14} />} onClick={addRow}
          disabled={!newMineral || !newQty}>
          Add
        </Button>
        <Tooltip label="Replaces the current list with Production's Buy List shortfall (minerals only)">
          <Button variant="subtle" leftSection={<IconDownload size={14} />}
            loading={loadFromProduction.isPending}
            onClick={() => loadFromProduction.mutate(undefined, { onSuccess: (r) => setRows(r) })}>
            Load from Production
          </Button>
        </Tooltip>
      </Group>

      {rows.length === 0 ? (
        <HintCard>No mineral requirements yet - add one above.</HintCard>
      ) : (
        <Table maw={640} withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Mineral</Table.Th>
              <Table.Th>Required Quantity</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r, index) => (
              <Table.Tr key={r.type_id}>
                <Table.Td>{r.name}</Table.Td>
                <Table.Td>
                  <NumberInput size="xs" min={1} value={r.required_qty} thousandSeparator
                    onChange={(v) => setRows((all) => all.map((row, i) =>
                      i === index ? { ...row, required_qty: Number(v) || 0 } : row))} />
                </Table.Td>
                <Table.Td w={50}>
                  <ActionIcon variant="subtle" color="danger" aria-label={`Remove ${r.name}`}
                    onClick={() => setRows((all) => all.filter((_, i) => i !== index))}>
                    <IconTrash size={14} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Group>
        <Tooltip label={optimize.tooltip} disabled={!optimize.tooltip} multiline w={280}>
          <Button leftSection={<IconCalculator size={14} />} rightSection={optimize.tierIcon} loading={optimize.isPending}
            disabled={rows.length === 0}
            onClick={() => optimize.mutate(rows, { onSuccess: (p) => setPlan(p) })}>
            Optimize
          </Button>
        </Tooltip>
        <Button variant="default" leftSection={<IconDeviceFloppy size={14} />} loading={save.isPending}
          onClick={() => save.mutate(rows)}>
          Save List
        </Button>
        {plan && <Button variant="subtle" onClick={() => setPlan(null)}>Clear Result</Button>}
      </Group>

      {plan && (
        <>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Total Cost</Text>
              <Title order={4}>{isk(plan.total_cost)}</Title>
              <Text size="xs" c="dimmed">{isk(plan.reprocess_cost)} reprocessing + {isk(plan.direct_cost)} minerals</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Buying Minerals Only</Text>
              <Title order={4}>{isk(plan.all_direct_cost)}</Title>
              <Text size="xs" c="dimmed">no reprocessing at all</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Saved by Reprocessing</Text>
              <Title order={4}>
                {isk(plan.savings_vs_all_direct)}{' '}
                {plan.savings_vs_all_direct !== null && plan.savings_vs_all_direct > 0 && (
                  <Badge color="accent" variant="light">cheaper</Badge>
                )}
              </Title>
              <Text size="xs" c="dimmed">vs. buying every mineral outright</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Haul Volume</Text>
              <Title order={4}>{qty(plan.total_volume_m3)} m3</Title>
              <Text size="xs" c="dimmed">rounding cost {isk(plan.total_cost - plan.lp_cost)}</Text>
            </Card>
          </SimpleGrid>

          <Title order={6} c="dimmed" tt="uppercase" mt="md">Ore / Ice to Buy and Refine</Title>
          {orePurchases.length === 0 ? (
            <HintCard>No ore or ice worth refining right now.</HintCard>
          ) : (
            <DataTable data={orePurchases} columns={oreColumns} maxHeight={360}
              getRowId={(r) => String(r.type_id)} />
          )}

          <Title order={6} c="dimmed" tt="uppercase" mt="md">Modules to Buy and Reprocess</Title>
          {modulePurchases.length === 0 ? (
            <HintCard>No shortlisted modules worth reprocessing right now.</HintCard>
          ) : (
            <DataTable data={modulePurchases} columns={moduleColumns} maxHeight={360}
              getRowId={(r) => String(r.type_id)} />
          )}

          <Title order={6} c="dimmed" tt="uppercase" mt="md">Minerals to Buy Outright</Title>
          {plan.direct_purchases.length === 0 ? (
            <HintCard>None - reprocessing covers every requirement more cheaply.</HintCard>
          ) : (
            <DataTable data={plan.direct_purchases} columns={directColumns} maxHeight={300}
              getRowId={(r) => String(r.type_id)} />
          )}

          <Title order={6} c="dimmed" tt="uppercase" mt="md">Coverage Check</Title>
          <DataTable data={plan.coverage} columns={coverageColumns} maxHeight={300}
            getRowId={(r) => String(r.type_id)} />
        </>
      )}
    </Stack>
  )
}
