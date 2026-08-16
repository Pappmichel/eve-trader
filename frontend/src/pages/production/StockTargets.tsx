import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Group, TextInput, NumberInput, Button, Select, Stack } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { StockTarget } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'

const STOCK_KEYS = [
  ['production', 'stock-targets'],
  ['production', 'plan'],
]

// Uncontrolled NumberInput (defaultValue, mutate()-per-keystroke) reused
// across every selected item - confirmed real bug: without a `key` forcing a
// remount, switching the "Item" Select kept showing the *previous* item's
// value (React reuses the same DOM node, defaultValue only applies on first
// mount), so an edit right after switching items silently saved a stale
// number against the *new* type_id. Local controlled state + `key={typeId}`
// (remounts on selection change, giving each item its own fresh draft) +
// commit on blur instead of every keystroke (was firing a POST + toast per
// digit typed) fixes both at once.
function CurrentStockInput({ initial, onCommit }: { initial: number; onCommit: (count: number) => void }) {
  const [value, setValue] = useState<number | ''>(initial)
  return (
    <NumberInput
      label="Current stock"
      value={value}
      onChange={(v) => setValue(v === '' ? '' : Number(v))}
      onBlur={() => onCommit(value === '' ? 0 : Number(value))}
      min={0}
    />
  )
}

export default function StockTargets() {
  const { data: targets, isLoading: targetsLoading } = useQuery({ queryKey: ['production', 'stock-targets'], queryFn: productionApi.stockTargets })
  const { data: manualStock } = useQuery({ queryKey: ['production', 'manual-stock'], queryFn: productionApi.manualStock })
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

  const [newName, setNewName] = useState('')
  const [newBackup, setNewBackup] = useState<number | ''>(0)
  const [newHome, setNewHome] = useState<number | ''>(0)
  const [newJita, setNewJita] = useState<number | ''>(0)

  const addTarget = useAction('Add Stock Target', productionApi.addStockTarget, STOCK_KEYS)
  const removeTarget = useAction('Remove Stock Target', productionApi.removeStockTarget, STOCK_KEYS)
  const setManualStockAction = useAction('Save Current Stock', (args: { typeId: number; count: number }) =>
    productionApi.setManualStock(args.typeId, args.count),
    [['production', 'manual-stock'], ['production', 'stock-value']])
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
    { header: 'Backup Target', accessorKey: 'backup_stock', size: 130, cell: (i) => qty(i.getValue()) },
    {
      header: 'Current Stock (manual)', id: 'manual', size: 150, accessorFn: (r) => manualStock?.[r.type_id] ?? 0,
      cell: (i) => qty(i.getValue()),
    },
    {
      header: 'Current Stock (incl. ESI)', id: 'computed', size: 160, accessorFn: (r) => computedStock.get(r.type_id) ?? null,
      cell: (i) => (i.getValue() === null ? '–' : qty(i.getValue())),
    },
    { header: 'Home Market Target', accessorKey: 'home_market_stock', size: 150, cell: (i) => qty(i.getValue()) },
    { header: 'Jita Market Target', accessorKey: 'jita_market_stock', size: 140, cell: (i) => qty(i.getValue()) },
    {
      header: 'Build/Buy Override', id: 'override', size: 150, accessorFn: (r) => overrides?.[r.type_id] ?? 'Auto',
    },
  ], [manualStock, computedStock, overrides])

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
        </Text>
        <Group grow align="flex-end">
          <TextInput label="Item name (exact)" value={newName} onChange={(e) => setNewName(e.currentTarget.value)} />
          <NumberInput label="Backup target" value={newBackup} onChange={(v) => setNewBackup(v === '' ? '' : Number(v))} min={0} />
          <NumberInput label="Home market target" value={newHome} onChange={(v) => setNewHome(v === '' ? '' : Number(v))} min={0} />
          <NumberInput label="Jita market target" value={newJita} onChange={(v) => setNewJita(v === '' ? '' : Number(v))} min={0} />
          <Button onClick={() => addTarget.mutate({
            type_name: newName, backup_stock: Number(newBackup) || 0,
            home_market_stock: newHome ? Number(newHome) : null, jita_market_stock: newJita ? Number(newJita) : null,
          })} loading={addTarget.isPending}>
            Add
          </Button>
        </Group>
      </Card>

      {targetsLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={480} />
      ) : !targets || targets.length === 0 ? (
        <HintCard>No stock targets configured yet.</HintCard>
      ) : (
        <>
          <DataTable data={targets} columns={columns} maxHeight={480} />

          <Title order={6} c="dimmed" tt="uppercase" mt="lg">Manage Current Stock / Override</Title>
          <Select data={options} value={chosenId} onChange={setChosenId} searchable label="Item" w={400} />

          {chosen && (
            <Group grow align="flex-end" mt="sm">
              <CurrentStockInput
                key={chosen.type_id}
                initial={manualStock?.[chosen.type_id] ?? 0}
                onCommit={(count) => setManualStockAction.mutate({ typeId: chosen.type_id, count })}
              />
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
              <Button color="danger" variant="outline" onClick={() => removeTarget.mutate(chosen.type_id)} loading={removeTarget.isPending}>
                Remove Stock Target
              </Button>
            </Group>
          )}
        </>
      )}
    </Stack>
  )
}
