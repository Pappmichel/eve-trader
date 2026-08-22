import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ActionIcon, Badge, Button, Card, Center, Group, Loader, NumberInput, Select, SimpleGrid, Stack, Table, Text,
  Title, Tooltip,
} from '@mantine/core'
import { IconCalculator, IconDeviceFloppy, IconDownload, IconPlus, IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { refiningApi } from '../../api/client'
import type { DirectMineralPurchase, MineralCoverage, MineralRequirement, OrePurchase, ShoppingListPlan } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'

export default function MineralShoppingList() {
  const { data: minerals } = useQuery({ queryKey: ['refining', 'refinable-minerals'], queryFn: refiningApi.refinableMinerals })
  const { data: saved } = useQuery({ queryKey: ['refining', 'mineral-requirements'], queryFn: refiningApi.mineralRequirements })

  const [rows, setRows] = useState<MineralRequirement[]>([])
  useEffect(() => { if (saved) setRows(saved) }, [saved])

  const [newMineral, setNewMineral] = useState<string | null>(null)
  const [newQty, setNewQty] = useState<number | ''>('')
  const [plan, setPlan] = useState<ShoppingListPlan | null>(null)

  const save = useAction('Save Requirements', refiningApi.saveMineralRequirements, [
    ['refining', 'mineral-requirements'],
  ])
  // Solved from the on-screen list rather than the saved one, so the button
  // always reflects what the user is looking at - no "save first" step.
  const optimize = useAction('Optimize', (r: MineralRequirement[]) => refiningApi.optimizeShoppingList(r))

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

  const oreColumns = useMemo<ColumnDef<OrePurchase, any>[]>(() => [
    { header: 'Buy in Jita', accessorKey: 'item', size: 220 },
    { header: 'Family', accessorKey: 'family', size: 130, meta: { mobileHide: true } },
    { header: 'Units', accessorKey: 'units', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Portions', accessorKey: 'portions', size: 100, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
    { header: 'Volume (m3)', accessorKey: 'volume_m3', size: 120, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
    { header: 'Landed / Unit', accessorKey: 'landed_cost_per_unit', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Total Cost', accessorKey: 'total_cost', size: 140, cell: (i) => isk(i.getValue()) },
  ], [])

  const directColumns = useMemo<ColumnDef<DirectMineralPurchase, any>[]>(() => [
    { header: 'Buy Directly', accessorKey: 'name', size: 220 },
    { header: 'Quantity', accessorKey: 'quantity', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'Landed / Unit', accessorKey: 'landed_cost_per_unit', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Total Cost', accessorKey: 'total_cost', size: 140, cell: (i) => isk(i.getValue()) },
  ], [])

  const coverageColumns = useMemo<ColumnDef<MineralCoverage, any>[]>(() => [
    { header: 'Mineral', accessorKey: 'name', size: 160 },
    { header: 'Required', accessorKey: 'required', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'From Refining', accessorKey: 'from_ore', size: 130, cell: (i) => qty(i.getValue()) },
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
        Enter how many of each mineral you need; the optimizer solves a real linear program across every
        compressed ore/ice type at once for the cheapest mix of buy-and-refine vs. buying the mineral outright.
        Every price is a Jita buy landed at C-J (Jita sell percentile + broker fee + haul cost), refining yield
        comes from your Settings tab (the structure's reprocessing tax is taken out of the refined materials).
        Ore quantities are rounded UP to whole reprocessing portions, so the plan always covers the requirement.
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
        <Tooltip label="Coming in #94 - pulls the shortfall straight from Production's buy list">
          {/* Wrapped: a disabled Mantine Button swallows pointer events, so the
              tooltip would never show on the button itself. */}
          <span>
            <Button variant="subtle" leftSection={<IconDownload size={14} />} disabled>
              Aus Production laden
            </Button>
          </span>
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
        <Button leftSection={<IconCalculator size={14} />} loading={optimize.isPending}
          disabled={rows.length === 0}
          onClick={() => optimize.mutate(rows, { onSuccess: (p) => setPlan(p) })}>
          Optimize
        </Button>
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
              <Text size="xs" c="dimmed">{isk(plan.ore_cost)} ore + {isk(plan.direct_cost)} minerals</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Buying Minerals Only</Text>
              <Title order={4}>{isk(plan.all_direct_cost)}</Title>
              <Text size="xs" c="dimmed">no refining at all</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Saved by Refining</Text>
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
          {plan.ore_purchases.length === 0 ? (
            <HintCard>Nothing worth refining right now - buying the minerals outright is cheaper.</HintCard>
          ) : (
            <DataTable data={plan.ore_purchases} columns={oreColumns} maxHeight={360}
              getRowId={(r) => String(r.type_id)} />
          )}

          <Title order={6} c="dimmed" tt="uppercase" mt="md">Minerals to Buy Outright</Title>
          {plan.direct_purchases.length === 0 ? (
            <HintCard>None - refining covers every requirement more cheaply.</HintCard>
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
