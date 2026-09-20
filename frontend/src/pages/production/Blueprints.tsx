import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Stack, Card, Title, Text, Group, NumberInput, Button, ActionIcon, Divider } from '@mantine/core'
import { modals } from '@mantine/modals'
import { IconTrash, IconCheck } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { ManualBlueprintCopyCostRow, ManualBlueprintMeTeOverrideRow, OwnedBlueprintRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { isk, qty } from '../../format'

const MANUAL_COPY_COSTS_KEY = [['production', 'manual-blueprint-copy-costs']]

function ManualBlueprintCopyCostsSection() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['production', 'manual-blueprint-copy-costs'], queryFn: productionApi.manualBlueprintCopyCosts,
  })
  const addCost = useAction(
    'Add Blueprint Copy Cost',
    (args: { itemName: string; purchaseCost: number; runs: number }) =>
      productionApi.addManualBlueprintCopyCost(args.itemName, args.purchaseCost, args.runs),
    MANUAL_COPY_COSTS_KEY,
  )
  const removeCost = useAction('Remove Blueprint Copy Cost', productionApi.removeManualBlueprintCopyCost, MANUAL_COPY_COSTS_KEY)
  // GitHub issue #59 (found in a full-codebase audit 2026-08-21): one shared
  // mutation instance reused across every row's Remove button - without
  // tracking which row is actually pending, clicking Remove for one row put
  // *every* row's button into the loading/disabled state, not just the one
  // being removed (same bug ProductionLayout.tsx's own removeCharacter/
  // pendingRoleKey comment already documents and fixes).
  const [pendingTypeId, setPendingTypeId] = useState<number | null>(null)
  const updateCost = useAction(
    'Save Blueprint Copy Cost',
    (args: { typeId: number; purchaseCost: number; runs: number }) =>
      productionApi.updateManualBlueprintCopyCost(args.typeId, args.purchaseCost, args.runs),
    MANUAL_COPY_COSTS_KEY,
  )
  // Same one-shared-mutation-instance caveat as removeCost/pendingTypeId
  // above, tracked separately since a row's edit and delete can each be
  // in flight independently.
  const [pendingEditTypeId, setPendingEditTypeId] = useState<number | null>(null)

  const { data: itemNameOptions } = useItemNameOptions()
  const copyCostItemOptions = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [itemId, setItemId] = useState<string | null>(null)
  const [purchaseCost, setPurchaseCost] = useState<number | ''>('')
  const [runs, setRuns] = useState<number | ''>('')

  const columns = useMemo<ColumnDef<ManualBlueprintCopyCostRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260 },
    {
      header: 'Purchase Cost', accessorKey: 'purchase_cost', size: 170,
      cell: (i) => (
        <EditableCopyCostCell value={i.getValue()} ariaLabel={`Purchase cost for ${i.row.original.type_name}`}
          min={0} isPending={updateCost.isPending && pendingEditTypeId === i.row.original.type_id}
          onSave={(v) => {
            setPendingEditTypeId(i.row.original.type_id)
            updateCost.mutate({ typeId: i.row.original.type_id, purchaseCost: v, runs: i.row.original.runs })
          }} />
      ),
    },
    {
      header: 'Runs', accessorKey: 'runs', size: 130,
      cell: (i) => (
        <EditableCopyCostCell value={i.getValue()} ariaLabel={`Runs for ${i.row.original.type_name}`}
          min={1} isPending={updateCost.isPending && pendingEditTypeId === i.row.original.type_id}
          onSave={(v) => {
            setPendingEditTypeId(i.row.original.type_id)
            updateCost.mutate({ typeId: i.row.original.type_id, purchaseCost: i.row.original.purchase_cost, runs: v })
          }} />
      ),
    },
    { header: 'Cost/Run', accessorKey: 'cost_per_run', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger"
          onClick={() => modals.openConfirmModal({
            title: 'Remove blueprint copy cost',
            children: <Text size="sm">Remove the registered copy cost for {i.row.original.type_name}?</Text>,
            labels: { confirm: 'Remove', cancel: 'Cancel' },
            confirmProps: { color: 'danger' },
            onConfirm: () => { setPendingTypeId(i.row.original.type_id); removeCost.mutate(i.row.original.type_id) },
          })}
          loading={removeCost.isPending && pendingTypeId === i.row.original.type_id}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [removeCost, pendingTypeId, updateCost, pendingEditTypeId])

  return (
    <div>
      <Title order={5} mb="xs">Blueprint Copies That Must Be Bought</Title>
      <Text size="sm" c="dimmed" mb="sm">
        Some items can only be built from a blueprint <b>copy</b> bought outright (never owned as a BPO, not
        inventable) - register its purchase cost and included run count so the amortized cost per run feeds
        into that item's build cost everywhere (Build List, Build Candidates, Margin).
      </Text>

      <Card withBorder mb="sm">
        <Group grow align="flex-end">
          <SearchableSelect label="Item name" placeholder="Search item…" data={copyCostItemOptions} value={itemId} onChange={setItemId} />
          <NumberInput label="Purchase cost (ISK)" value={purchaseCost}
            onChange={(v) => setPurchaseCost(v === '' ? '' : Number(v))} min={0} />
          <NumberInput label="Runs included" value={runs} onChange={(v) => setRuns(v === '' ? '' : Number(v))} min={1} />
          <Button
            disabled={!itemId || purchaseCost === '' || runs === ''}
            loading={addCost.isPending}
            onClick={() => addCost.mutate(
              { itemName: copyCostItemOptions.find((o) => o.value === itemId)?.label ?? '', purchaseCost: Number(purchaseCost), runs: Number(runs) },
              { onSuccess: () => { setItemId(null); setPurchaseCost(''); setRuns('') } },
            )}
          >
            Add
          </Button>
        </Group>
      </Card>

      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={300} />
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={300} />
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

// Inline cell editor shared by the manual-copy-costs and manual-me-te-
// overrides tables below - same "local draft state, checkmark appears once
// it differs from the saved value, click to save" pattern as
// StockTargets.tsx's own EditableNumberCell/doctrine/DoctrineDetail.tsx's
// TargetEditor. Safe to key state purely off the initial `value` prop (no
// resync effect needed) for the same reason those components don't need
// one either - see DataTable's `getRowId` prop (used below) for what
// actually *would* break this if it were missing. `max` is optional
// (purchase_cost/runs have none; ME/TE are capped at 10/20).
function EditableCopyCostCell({ value, ariaLabel, min, max, isPending, onSave }: {
  value: number
  ariaLabel: string
  min: number
  max?: number
  isPending: boolean
  onSave: (value: number) => void
}) {
  const [draft, setDraft] = useState(value)
  const dirty = draft !== value
  return (
    <Group gap={4} wrap="nowrap">
      <NumberInput value={draft} onChange={(v) => setDraft(v === '' ? min : Number(v))}
        min={min} max={max} size="xs" w={110} aria-label={ariaLabel} />
      {dirty && (
        <ActionIcon size="sm" variant="filled" color="accent" aria-label={`Save ${ariaLabel}`}
          onClick={() => onSave(draft)} loading={isPending}>
          <IconCheck size={14} />
        </ActionIcon>
      )}
    </Group>
  )
}

const MANUAL_ME_TE_OVERRIDES_KEY = [['production', 'manual-blueprint-me-te-overrides']]

// Confirmed with the user 2026-09-16: a fixed ME/TE for a blueprint whose
// product can't be researched in-game (e.g. Zirnitra - a Precursor/Faction
// Titan) or whose real owned BPO's ME/TE differs from what this app would
// otherwise assume - takes priority over both the flat "perfect research"
// baseline and any owned BPO's own ME/TE (see engine._activity_mods'
// docstring for the full resolution chain). Same shape as
// ManualBlueprintCopyCostsSection above - deliberately duplicates its
// add-row/inline-edit/delete structure rather than sharing it, since a
// shared abstraction over two different field sets (purchase_cost/runs vs.
// material_efficiency/time_efficiency) would need more indirection than it
// saves for just two tables.
function ManualBlueprintMeTeOverridesSection() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['production', 'manual-blueprint-me-te-overrides'], queryFn: productionApi.manualBlueprintMeTeOverrides,
  })
  const addOverride = useAction(
    'Add ME/TE Override',
    (args: { itemName: string; materialEfficiency: number; timeEfficiency: number }) =>
      productionApi.addManualBlueprintMeTeOverride(args.itemName, args.materialEfficiency, args.timeEfficiency),
    MANUAL_ME_TE_OVERRIDES_KEY,
  )
  const removeOverride = useAction('Remove ME/TE Override', productionApi.removeManualBlueprintMeTeOverride, MANUAL_ME_TE_OVERRIDES_KEY)
  // Same one-shared-mutation-instance caveat as ManualBlueprintCopyCostsSection's
  // own pendingTypeId/pendingEditTypeId above (GitHub issue #59).
  const [pendingTypeId, setPendingTypeId] = useState<number | null>(null)
  const updateOverride = useAction(
    'Save ME/TE Override',
    (args: { typeId: number; materialEfficiency: number; timeEfficiency: number }) =>
      productionApi.updateManualBlueprintMeTeOverride(args.typeId, args.materialEfficiency, args.timeEfficiency),
    MANUAL_ME_TE_OVERRIDES_KEY,
  )
  const [pendingEditTypeId, setPendingEditTypeId] = useState<number | null>(null)

  const { data: itemNameOptions } = useItemNameOptions()
  const overrideItemOptions = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [itemId, setItemId] = useState<string | null>(null)
  const [materialEfficiency, setMaterialEfficiency] = useState<number | ''>('')
  const [timeEfficiency, setTimeEfficiency] = useState<number | ''>('')

  const columns = useMemo<ColumnDef<ManualBlueprintMeTeOverrideRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260 },
    {
      header: 'ME', accessorKey: 'material_efficiency', size: 140,
      cell: (i) => (
        <EditableCopyCostCell value={i.getValue()} ariaLabel={`Material Efficiency for ${i.row.original.type_name}`}
          min={0} max={10} isPending={updateOverride.isPending && pendingEditTypeId === i.row.original.type_id}
          onSave={(v) => {
            setPendingEditTypeId(i.row.original.type_id)
            updateOverride.mutate({ typeId: i.row.original.type_id, materialEfficiency: v, timeEfficiency: i.row.original.time_efficiency })
          }} />
      ),
    },
    {
      header: 'TE', accessorKey: 'time_efficiency', size: 140,
      cell: (i) => (
        <EditableCopyCostCell value={i.getValue()} ariaLabel={`Time Efficiency for ${i.row.original.type_name}`}
          min={0} max={20} isPending={updateOverride.isPending && pendingEditTypeId === i.row.original.type_id}
          onSave={(v) => {
            setPendingEditTypeId(i.row.original.type_id)
            updateOverride.mutate({ typeId: i.row.original.type_id, materialEfficiency: i.row.original.material_efficiency, timeEfficiency: v })
          }} />
      ),
    },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger"
          onClick={() => modals.openConfirmModal({
            title: 'Remove ME/TE override',
            children: <Text size="sm">Remove the registered ME/TE override for {i.row.original.type_name}?</Text>,
            labels: { confirm: 'Remove', cancel: 'Cancel' },
            confirmProps: { color: 'danger' },
            onConfirm: () => { setPendingTypeId(i.row.original.type_id); removeOverride.mutate(i.row.original.type_id) },
          })}
          loading={removeOverride.isPending && pendingTypeId === i.row.original.type_id}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [removeOverride, pendingTypeId, updateOverride, pendingEditTypeId])

  return (
    <div>
      <Title order={5} mb="xs">Blueprint ME/TE Overrides</Title>
      <Text size="sm" c="dimmed" mb="sm">
        Set a fixed ME/TE for a blueprint's product - takes priority over both the "assumes perfect research"
        default and any owned BPO's own ME/TE, including for blueprints that can't actually be researched
        in-game (Faction/Officer/Storyline/Deadspace).
      </Text>

      <Card withBorder mb="sm">
        <Group grow align="flex-end">
          <SearchableSelect label="Item name" placeholder="Search item…" data={overrideItemOptions} value={itemId} onChange={setItemId} />
          <NumberInput label="ME" value={materialEfficiency}
            onChange={(v) => setMaterialEfficiency(v === '' ? '' : Number(v))} min={0} max={10} />
          <NumberInput label="TE" value={timeEfficiency}
            onChange={(v) => setTimeEfficiency(v === '' ? '' : Number(v))} min={0} max={20} />
          <Button
            disabled={!itemId || materialEfficiency === '' || timeEfficiency === ''}
            loading={addOverride.isPending}
            onClick={() => addOverride.mutate(
              {
                itemName: overrideItemOptions.find((o) => o.value === itemId)?.label ?? '',
                materialEfficiency: Number(materialEfficiency), timeEfficiency: Number(timeEfficiency),
              },
              { onSuccess: () => { setItemId(null); setMaterialEfficiency(''); setTimeEfficiency('') } },
            )}
          >
            Add
          </Button>
        </Group>
      </Card>

      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={300} />
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={300} />
      ) : !data || data.length === 0 ? (
        <Text c="dimmed" size="sm">None registered yet.</Text>
      ) : (
        <DataTable data={data} columns={columns} tableId="manual-blueprint-me-te-overrides"
          exportFilename="manual-blueprint-me-te-overrides" getRowId={(r) => String(r.type_id)} maxHeight={300}
          dataUpdatedAt={dataUpdatedAt} />
      )}
    </div>
  )
}

export default function Blueprints() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'blueprints'], queryFn: productionApi.ownedBlueprints })

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
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
      ) : !data || data.length === 0 ? (
        <HintCard>No blueprints found - or not synced yet (&apos;Refresh what I need&apos; in the sidebar).</HintCard>
      ) : (
        <DataTable data={data} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      )}

      <Divider />
      <ManualBlueprintCopyCostsSection />

      <Divider />
      <ManualBlueprintMeTeOverridesSection />
    </Stack>
  )
}
