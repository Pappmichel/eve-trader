import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Stack, Card, Title, Text, Group, TextInput, NumberInput, Button, ActionIcon, Divider } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { ManualBlueprintCopyCostRow, OwnedBlueprintRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'

const MANUAL_COPY_COSTS_KEY = [['production', 'manual-blueprint-copy-costs']]

function ManualBlueprintCopyCostsSection() {
  const { data, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['production', 'manual-blueprint-copy-costs'], queryFn: productionApi.manualBlueprintCopyCosts,
  })
  const addCost = useAction(
    'Add Blueprint Copy Cost',
    (args: { itemName: string; purchaseCost: number; runs: number }) =>
      productionApi.addManualBlueprintCopyCost(args.itemName, args.purchaseCost, args.runs),
    MANUAL_COPY_COSTS_KEY,
  )
  const removeCost = useAction('Remove Blueprint Copy Cost', productionApi.removeManualBlueprintCopyCost, MANUAL_COPY_COSTS_KEY)

  const [itemName, setItemName] = useState('')
  const [purchaseCost, setPurchaseCost] = useState<number | ''>('')
  const [runs, setRuns] = useState<number | ''>('')

  const columns = useMemo<ColumnDef<ManualBlueprintCopyCostRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260 },
    { header: 'Purchase Cost', accessorKey: 'purchase_cost', size: 150, cell: (i) => isk(i.getValue()) },
    { header: 'Runs', accessorKey: 'runs', size: 100, cell: (i) => qty(i.getValue()) },
    { header: 'Cost/Run', accessorKey: 'cost_per_run', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger"
          onClick={() => removeCost.mutate(i.row.original.type_id)} loading={removeCost.isPending}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [removeCost])

  return (
    <div>
      <Title order={5} mb="xs">Blueprint Copies That Must Be Bought</Title>
      <Text size="sm" c="dimmed" mb="sm">
        Some items can only be built from a blueprint <b>copy</b> bought outright (never owned as a BPO, not
        inventable) - register its purchase cost and included run count here so the amortized cost per run gets
        folded into that item's modeled build cost everywhere (Bauliste, Build Candidates, Margin).
      </Text>

      <Card withBorder mb="sm">
        <Group grow align="flex-end">
          <TextInput label="Item name (exact)" placeholder="e.g. Rifter" value={itemName}
            onChange={(e) => setItemName(e.currentTarget.value)} />
          <NumberInput label="Purchase cost (ISK)" value={purchaseCost}
            onChange={(v) => setPurchaseCost(v === '' ? '' : Number(v))} min={0} />
          <NumberInput label="Runs included" value={runs} onChange={(v) => setRuns(v === '' ? '' : Number(v))} min={1} />
          <Button
            disabled={!itemName.trim() || purchaseCost === '' || runs === ''}
            loading={addCost.isPending}
            onClick={() => addCost.mutate(
              { itemName, purchaseCost: Number(purchaseCost), runs: Number(runs) },
              { onSuccess: () => { setItemName(''); setPurchaseCost(''); setRuns('') } },
            )}
          >
            Add
          </Button>
        </Group>
      </Card>

      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={300} />
      ) : !data || data.length === 0 ? (
        <Text c="dimmed" size="sm">None registered yet.</Text>
      ) : (
        <DataTable data={data} columns={columns} tableId="manual-blueprint-copy-costs"
          exportFilename="manual-blueprint-copy-costs" getRowId={(r) => String(r.type_id)} maxHeight={300}
          dataUpdatedAt={dataUpdatedAt} />
      )}
    </div>
  )
}

export default function Blueprints() {
  const { data, isLoading, dataUpdatedAt } = useQuery({ queryKey: ['production', 'blueprints'], queryFn: productionApi.ownedBlueprints })

  const columns = useMemo<ColumnDef<OwnedBlueprintRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260 },
    {
      header: 'Type', accessorKey: 'is_original', size: 100,
      cell: (i) => <Badge color={i.getValue() ? 'accent' : 'info'} variant="light">{i.getValue() ? 'BPO' : 'BPC'}</Badge>,
    },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'ME', accessorKey: 'material_efficiency', size: 80 },
    { header: 'TE', accessorKey: 'time_efficiency', size: 80 },
    { header: 'Runs', accessorKey: 'runs', size: 100, cell: (i) => (i.getValue() === null ? '∞' : qty(i.getValue())) },
  ], [])

  return (
    <Stack>
      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
      ) : !data || data.length === 0 ? (
        <HintCard>No blueprints found - or not synced yet ('Sync ESI Data' in the sidebar).</HintCard>
      ) : (
        <DataTable data={data} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      )}

      <Divider />
      <ManualBlueprintCopyCostsSection />
    </Stack>
  )
}
