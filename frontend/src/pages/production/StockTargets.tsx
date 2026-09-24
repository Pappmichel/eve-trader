import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Group, NumberInput, Button, Select, Stack, ActionIcon, Tooltip } from '@mantine/core'
import { modals } from '@mantine/modals'
import { IconCheck, IconAlertTriangle, IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { ManualStockEntry, StockTarget } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { LocationPicker } from '../../components/LocationPicker'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { isk, qty } from '../../format'

const STOCK_KEYS = [
  ['production', 'stock-targets'],
  ['production', 'plan'],
]
// Both /manual-stock (per-type total) and /manual-stock/entries (per-
// location breakdown, docs/MANUAL_TRACKING_PLAN.md phase 3) read the same
// underlying table - a write through either endpoint invalidates both.
const MANUAL_STOCK_KEYS = [
  ['production', 'manual-stock'],
  ['production', 'manual-stock-entries'],
  ['production', 'stock-value'],
]

// A manual-stock override this many times larger than everything the item's
// own targets add up to is almost certainly a stray/mistyped value, not real
// inventory (GitHub issue #17 - confirmed real case: Small Shield Extender
// II showing 987,654 units in manual stock). Ratio-based against the item's
// own targets rather than a flat number, since a raw material's legitimate
// stock can easily run into the hundreds of thousands on its own.
const MANUAL_STOCK_OUTLIER_MULTIPLIER = 50

function isManualStockOutlier(manual: number, target: StockTarget): boolean {
  const targetSum = target.backup_stock + (target.home_market_stock ?? 0) + (target.jita_market_stock ?? 0)
  return targetSum > 0 && manual > targetSum * MANUAL_STOCK_OUTLIER_MULTIPLIER
}

// Inline cell editor (GitHub issue #16 - "should be able to change the
// targets directly in the table, like the targets on the doctrine table")
// - same "local draft state, checkmark appears once it differs from the
// saved value, click to save" pattern as doctrine/DoctrineDetail.tsx's own
// TargetEditor. Safe to key state purely off the initial `value` prop
// (no resync effect needed) for the same reason that component doesn't need
// one either: a successful save's own draft value becomes the next `value`
// this cell receives, so draft and value naturally converge without one -
// see DataTable's `getRowId` prop (used below) for what actually *would*
// break this if it were missing: without a stable per-row identity, sorting
// or filtering could hand this same mounted component a *different* row's
// data on cell state, silently saving one item's edit against another's
// type_id (the exact bug StockTargets.tsx's old CurrentStockInput hit once,
// documented in that component's own history).
function EditableNumberCell({ value, ariaLabel, isPending, onSave, flagged }: {
  value: number
  ariaLabel: string
  isPending: boolean
  onSave: (value: number) => void
  flagged?: boolean
}) {
  const [draft, setDraft] = useState(value)
  const dirty = draft !== value
  return (
    <Group gap={4} wrap="nowrap">
      <NumberInput
        value={draft}
        onChange={(v) => setDraft(v === '' ? 0 : Number(v))}
        min={0}
        size="xs"
        w={90}
        aria-label={ariaLabel}
        styles={flagged ? { input: { borderColor: 'var(--mantine-color-danger-5)' } } : undefined}
      />
      {flagged && !dirty && (
        <Tooltip label="Far larger than every target set for this item - likely a stray value. Edit and save to fix.">
          <IconAlertTriangle size={14} color="var(--mantine-color-danger-5)" />
        </Tooltip>
      )}
      {dirty && (
        <ActionIcon size="sm" variant="filled" color="accent" aria-label={`Save ${ariaLabel}`}
          onClick={() => onSave(draft)} loading={isPending}>
          <IconCheck size={14} />
        </ActionIcon>
      )}
    </Group>
  )
}

export default function StockTargets() {
  const { data: targets, isLoading: targetsLoading, isError: targetsError, refetch: refetchTargets, dataUpdatedAt: targetsUpdatedAt } =
    useQuery({ queryKey: ['production', 'stock-targets'], queryFn: productionApi.stockTargets })
  const { data: manualStock } = useQuery({ queryKey: ['production', 'manual-stock'], queryFn: productionApi.manualStock })
  const { data: manualStockEntries } = useQuery({
    queryKey: ['production', 'manual-stock-entries'], queryFn: productionApi.manualStockEntries,
  })
  const entriesByType = useMemo(() => {
    const m = new Map<number, ManualStockEntry[]>()
    for (const e of manualStockEntries ?? []) {
      const list = m.get(e.type_id) ?? []
      list.push(e)
      m.set(e.type_id, list)
    }
    return m
  }, [manualStockEntries])
  const { data: overrides } = useQuery({ queryKey: ['production', 'manual-build-buy'], queryFn: productionApi.manualBuildBuy })
  const { data: decryptorOverrides } = useQuery({ queryKey: ['production', 'selected-decryptors'], queryFn: productionApi.selectedDecryptors })
  const { data: decryptors } = useQuery({ queryKey: ['production', 'decryptors'], queryFn: productionApi.decryptors })
  const { data: plan } = useQuery({ queryKey: ['production', 'plan'], queryFn: productionApi.plan })
  const { data: stockValue } = useQuery({ queryKey: ['production', 'stock-value'], queryFn: productionApi.stockValue })

  const computedStock = useMemo(() => {
    const m = new Map<number, number>()
    for (const row of plan?.inventory ?? []) m.set(row.type_id, row.current_stock)
    return m
  }, [plan])

  const { data: itemNameOptions } = useItemNameOptions()
  const newItemOptions = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [newItemId, setNewItemId] = useState<string | null>(null)
  const [newBackup, setNewBackup] = useState<number | ''>(0)
  const [newHome, setNewHome] = useState<number | ''>(0)
  const [newJita, setNewJita] = useState<number | ''>(0)

  const addTarget = useAction('Add Stock Target', productionApi.addStockTarget, STOCK_KEYS)
  const removeTarget = useAction('Remove Stock Target', productionApi.removeStockTarget, STOCK_KEYS)
  // One shared mutation instance reused across every row's delete button -
  // without tracking which row is actually pending, clicking delete for one
  // row would put *every* row's button into the loading/disabled state (same
  // bug production/Blueprints.tsx's own removeCost/pendingTypeId already
  // fixes for its identical pattern).
  const [pendingRemoveId, setPendingRemoveId] = useState<number | null>(null)
  const updateTarget = useAction('Save Target', (args: {
    typeId: number
    field: 'backup_stock' | 'home_market_stock' | 'jita_market_stock'
    value: number
  }) => productionApi.updateStockTarget(args.typeId, { [args.field]: args.value }), STOCK_KEYS)
  const setManualStockAction = useAction('Save Current Stock', (args: { typeId: number; count: number; locationId: number }) =>
    productionApi.setManualStock(args.typeId, args.count, args.locationId), MANUAL_STOCK_KEYS)
  const setOverride = useAction('Save Override', (args: { typeId: number; decision: string }) =>
    productionApi.setManualBuildBuy(args.typeId, args.decision), [['production', 'manual-build-buy']])
  const clearOverride = useAction('Save Override', productionApi.clearManualBuildBuy, [['production', 'manual-build-buy']])
  const setDecryptor = useAction('Save Decryptor', (args: { typeId: number; decryptor: string }) =>
    productionApi.setSelectedDecryptor(args.typeId, args.decryptor), [['production', 'selected-decryptors']])
  const clearDecryptor = useAction('Save Decryptor', productionApi.clearSelectedDecryptor, [['production', 'selected-decryptors']])

  const [chosenId, setChosenId] = useState<string | null>(null)
  const options = useMemo(
    () => (targets ?? []).map((t) => ({ value: String(t.type_id), label: `${t.type_name} (#${t.type_id})` })),
    [targets],
  )
  const chosen = targets?.find((t) => String(t.type_id) === chosenId)

  const columns = useMemo<ColumnDef<StockTarget, any>[]>(() => [
    { header: 'TypeID', accessorKey: 'type_id', size: 90 },
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    {
      header: 'Backup Target', accessorKey: 'backup_stock', size: 140,
      cell: (i) => (
        <EditableNumberCell
          value={i.getValue()}
          ariaLabel={`Backup target for ${i.row.original.type_name}`}
          isPending={updateTarget.isPending}
          onSave={(value) => updateTarget.mutate({ typeId: i.row.original.type_id, field: 'backup_stock', value })}
        />
      ),
    },
    {
      header: 'Current Stock (manual)', id: 'manual', size: 170, accessorFn: (r) => manualStock?.[r.type_id] ?? 0,
      cell: (i) => {
        const entries = entriesByType.get(i.row.original.type_id) ?? []
        // Decision 9: the total column stays directly editable only while
        // there's at most one location entry for this type - once there's
        // more than one, editing this single field would be ambiguous
        // about *which* location to change, so it becomes a read-only
        // total and the per-location "Manual stock" table below is the
        // only way to edit it.
        if (entries.length > 1) {
          return (
            <Tooltip label="Split across multiple locations - edit in the Manual stock table below">
              <Text size="sm">{qty(i.getValue())}</Text>
            </Tooltip>
          )
        }
        const locationId = entries.length === 1 ? entries[0].location_id : 0
        return (
          <EditableNumberCell
            value={i.getValue()}
            ariaLabel={`Manual current stock for ${i.row.original.type_name}`}
            isPending={setManualStockAction.isPending}
            flagged={isManualStockOutlier(i.getValue(), i.row.original)}
            onSave={(value) => setManualStockAction.mutate({ typeId: i.row.original.type_id, count: value, locationId })}
          />
        )
      },
    },
    {
      header: 'Current Stock (incl. ESI)', id: 'computed', size: 180, accessorFn: (r) => computedStock.get(r.type_id) ?? null,
      cell: (i) => (i.getValue() === null ? '–' : qty(i.getValue())),
    },
    {
      header: 'Home Market Target', accessorKey: 'home_market_stock', size: 170,
      cell: (i) => (
        <EditableNumberCell
          value={i.getValue() ?? 0}
          ariaLabel={`Home market target for ${i.row.original.type_name}`}
          isPending={updateTarget.isPending}
          onSave={(value) => updateTarget.mutate({ typeId: i.row.original.type_id, field: 'home_market_stock', value })}
        />
      ),
    },
    {
      header: 'Jita Market Target', accessorKey: 'jita_market_stock', size: 160,
      cell: (i) => (
        <EditableNumberCell
          value={i.getValue() ?? 0}
          ariaLabel={`Jita market target for ${i.row.original.type_name}`}
          isPending={updateTarget.isPending}
          onSave={(value) => updateTarget.mutate({ typeId: i.row.original.type_id, field: 'jita_market_stock', value })}
        />
      ),
    },
    {
      header: 'Build/Buy Override', id: 'override', size: 150, accessorFn: (r) => overrides?.[r.type_id] ?? 'Auto',
    },
    {
      header: '', id: 'actions', size: 50, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Remove stock target for ${i.row.original.type_name}`}
          onClick={() => modals.openConfirmModal({
            title: 'Remove stock target',
            children: (
              <Text size="sm">
                Remove the stock target for {i.row.original.type_name}? Backup, home, and Jita targets are deleted. Manual stock and the build/buy override stay.
              </Text>
            ),
            labels: { confirm: 'Remove', cancel: 'Cancel' },
            confirmProps: { color: 'danger' },
            onConfirm: () => { setPendingRemoveId(i.row.original.type_id); removeTarget.mutate(i.row.original.type_id) },
          })}
          loading={removeTarget.isPending && pendingRemoveId === i.row.original.type_id}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
  ], [manualStock, entriesByType, computedStock, overrides, updateTarget, setManualStockAction, removeTarget, pendingRemoveId])

  return (
    <Stack>
      {stockValue && (
        <Card withBorder>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">Stock Value</Title>
          <Group>
            <Text size="xl" fw={700}>{isk(stockValue.total_value)}</Text>
            <Text size="xs" c="dimmed">
              {stockValue.priced_items} priced
              {stockValue.unpriced_items > 0 && `, ${stockValue.unpriced_items} without a market price (not included)`}
            </Text>
          </Group>
        </Card>
      )}

      <Card withBorder>
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">New Stock Target</Title>
        <Text size="xs" c="dimmed" mb="sm">
          Backup stock = personal component/raw material buffer. Home/Jita market stock = how many should be permanently listed.
          Targets, current stock, and Backup/Home/Jita columns are editable directly in the table below once added.
        </Text>
        <Group grow align="flex-end">
          <SearchableSelect label="Item name" placeholder="Search item…" data={newItemOptions} value={newItemId} onChange={setNewItemId} />
          <NumberInput label="Backup target" value={newBackup} onChange={(v) => setNewBackup(v === '' ? '' : Number(v))} min={0} />
          <NumberInput label="Home market target" value={newHome} onChange={(v) => setNewHome(v === '' ? '' : Number(v))} min={0} />
          <NumberInput label="Jita market target" value={newJita} onChange={(v) => setNewJita(v === '' ? '' : Number(v))} min={0} />
          <Button onClick={() => addTarget.mutate({
            type_name: newItemOptions.find((o) => o.value === newItemId)?.label ?? '', backup_stock: Number(newBackup) || 0,
            home_market_stock: newHome ? Number(newHome) : null, jita_market_stock: newJita ? Number(newJita) : null,
          })} loading={addTarget.isPending} disabled={!newItemId}>
            Add
          </Button>
        </Group>
      </Card>

      {targetsLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={480} />
      ) : targetsError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetchTargets()} maxHeight={480} />
      ) : !targets || targets.length === 0 ? (
        <HintCard>No stock targets configured yet.</HintCard>
      ) : (
        <>
          <DataTable
            data={targets}
            columns={columns}
            maxHeight={480}
            tableId="stock-targets"
            exportFilename="stock-targets"
            getRowId={(row) => String(row.type_id)}
            dataUpdatedAt={targetsUpdatedAt}
          />

          <Title order={6} c="dimmed" tt="uppercase" mt="lg">Manage Override / Decryptor</Title>
          <Select data={options} value={chosenId} onChange={setChosenId} searchable label="Item" w={400} />

          {chosen && (
            <Group grow align="flex-end" mt="sm">
              <Select
                label="Override"
                data={['Auto', 'Build', 'Buy']}
                value={overrides?.[chosen.type_id] ?? 'Auto'}
                onChange={(v) => {
                  if (!v || v === 'Auto') clearOverride.mutate(chosen.type_id)
                  else setOverride.mutate({ typeId: chosen.type_id, decision: v })
                }}
              />
              <Select
                label="Decryptor (Tech II only)"
                data={['Best (auto)', ...(decryptors ?? [])]}
                value={decryptorOverrides?.[chosen.type_id] ?? 'Best (auto)'}
                onChange={(v) => {
                  if (!v || v === 'Best (auto)') clearDecryptor.mutate(chosen.type_id)
                  else setDecryptor.mutate({ typeId: chosen.type_id, decryptor: v })
                }}
              />
            </Group>
          )}
        </>
      )}

      <ManualStockEntriesSection />
    </Stack>
  )
}

// docs/MANUAL_TRACKING_PLAN.md phase 3, decision 9 - a separate per-
// (item, location) table, deliberately not folded into the main Stock
// Targets table above (which only shows the per-type total, and only
// directly editable there while a type has at most one location entry).
// Same "own section, own query key, own add form + DataTable" shape as
// Blueprints.tsx's ManualBlueprintCopyCostsSection.
function ManualStockEntriesSection() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['production', 'manual-stock-entries'], queryFn: productionApi.manualStockEntries,
  })
  const addEntry = useAction(
    'Add Manual Stock',
    (args: { itemName: string; count: number; locationId: number }) =>
      productionApi.addManualStockEntry(args.itemName, args.count, args.locationId),
    MANUAL_STOCK_KEYS,
  )
  // Same one-shared-mutation-instance caveat as StockTargets' own
  // removeTarget/pendingRemoveId above - tracked per action since a row's
  // edit and delete can each be in flight independently.
  const [pendingRemoveKey, setPendingRemoveKey] = useState<string | null>(null)
  const removeEntry = useAction(
    'Remove Manual Stock',
    (args: { typeId: number; locationId: number }) => productionApi.removeManualStockEntry(args.typeId, args.locationId),
    MANUAL_STOCK_KEYS,
  )
  const [pendingEditKey, setPendingEditKey] = useState<string | null>(null)
  const updateEntry = useAction(
    'Save Manual Stock',
    (args: { typeId: number; count: number; locationId: number }) =>
      productionApi.setManualStock(args.typeId, args.count, args.locationId),
    MANUAL_STOCK_KEYS,
  )

  const { data: itemNameOptions } = useItemNameOptions()
  const entryItemOptions = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [itemId, setItemId] = useState<string | null>(null)
  const [count, setCount] = useState<number | ''>('')
  const [locationId, setLocationId] = useState<number | null>(0)

  const columns = useMemo<ColumnDef<ManualStockEntry, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 240 },
    {
      header: 'Location', id: 'location', size: 200,
      cell: (i) => (i.row.original.location_id === 0 ? 'No location' : String(i.row.original.location_id)),
    },
    {
      header: 'Quantity', accessorKey: 'count', size: 150,
      cell: (i) => {
        const key = `${i.row.original.type_id}:${i.row.original.location_id}`
        return (
          <EditableNumberCell
            value={i.getValue()}
            ariaLabel={`Manual stock for ${i.row.original.type_name} at ${i.row.original.location_id}`}
            isPending={updateEntry.isPending && pendingEditKey === key}
            onSave={(value) => {
              setPendingEditKey(key)
              updateEntry.mutate({ typeId: i.row.original.type_id, count: value, locationId: i.row.original.location_id })
            }}
          />
        )
      },
    },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (i) => {
        const key = `${i.row.original.type_id}:${i.row.original.location_id}`
        return (
          <ActionIcon size="sm" variant="subtle" color="danger"
            aria-label={`Remove manual stock for ${i.row.original.type_name} at ${i.row.original.location_id}`}
            onClick={() => modals.openConfirmModal({
              title: 'Remove manual stock entry',
              children: (
                <Text size="sm">
                  Remove the manual stock entry for {i.row.original.type_name}
                  {i.row.original.location_id !== 0 ? ` at location ${i.row.original.location_id}` : ''}?
                </Text>
              ),
              labels: { confirm: 'Remove', cancel: 'Cancel' },
              confirmProps: { color: 'danger' },
              onConfirm: () => {
                setPendingRemoveKey(key)
                removeEntry.mutate({ typeId: i.row.original.type_id, locationId: i.row.original.location_id })
              },
            })}
            loading={removeEntry.isPending && pendingRemoveKey === key}>
            <IconTrash size={14} />
          </ActionIcon>
        )
      },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [updateEntry, pendingEditKey, removeEntry, pendingRemoveKey])

  return (
    <div>
      <Title order={6} c="dimmed" tt="uppercase" mt="lg" mb="xs">Manual Stock</Title>
      <Text size="xs" c="dimmed" mb="sm">
        Stock you track by hand, per location - not fetched from ESI. Feeds every stock-target/plan calculation the
        same way ESI-derived stock does.
      </Text>

      <Card withBorder mb="sm">
        <Group grow align="flex-end">
          <SearchableSelect label="Item name" placeholder="Search item…" data={entryItemOptions} value={itemId} onChange={setItemId} />
          <LocationPicker label="Location" value={locationId} onChange={setLocationId} allowNone />
          <NumberInput label="Quantity" value={count} onChange={(v) => setCount(v === '' ? '' : Number(v))} min={0} />
          <Button
            disabled={!itemId || count === ''}
            loading={addEntry.isPending}
            onClick={() => addEntry.mutate(
              {
                itemName: entryItemOptions.find((o) => o.value === itemId)?.label ?? '',
                count: Number(count), locationId: locationId ?? 0,
              },
              { onSuccess: () => { setItemId(null); setCount(''); setLocationId(0) } },
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
        <Text c="dimmed" size="sm">No manual stock entries yet.</Text>
      ) : (
        <DataTable data={data} columns={columns} tableId="manual-stock-entries"
          exportFilename="manual-stock-entries" getRowId={(r) => `${r.type_id}:${r.location_id}`} maxHeight={300}
          dataUpdatedAt={dataUpdatedAt} />
      )}
    </div>
  )
}
