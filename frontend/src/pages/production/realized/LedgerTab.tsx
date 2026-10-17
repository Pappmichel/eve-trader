import { useState } from 'react'
import { Anchor, Badge, Button, Group, NumberInput, Select, Stack, Text, TextInput, Title } from '@mantine/core'
import { modals } from '@mantine/modals'
import { IconTrash } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../../api/client'
import type {
  CharInfoField, EsiTokenCharacter, RealizedArchiveOwner,
  RealizedLedgerEntry, RealizedOpeningRow,
} from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { EditableNumberCell } from '../../../components/EditableCell'
import { FieldState } from '../../../components/FieldState'
import { HintCard } from '../../../components/HintCard'
import { SearchableSelect } from '../../../components/SearchableSelect'
import { useAction } from '../../../hooks/useAction'
import { useItemNameOptions } from '../../../hooks/useStaticOptions'
import { dateTime, isk, qty } from '../../../format'
import { displayQty, ownerLabel } from './labels'

const SHARE_KINDS = ['wallet', 'industry_jobs', 'market_orders', 'contracts'] as const

function when(iso: string | null | undefined): string {
  return iso ? dateTime(iso) : '–'
}

function ShareState({ shared }: { shared: boolean }) {
  const field: CharInfoField<boolean> = shared
    ? { state: 'ok', value: true }
    : { state: 'not_shared', value: null }
  const body = (
    <FieldState field={field}>
      {() => <Badge size="xs" color="accent" variant="light">shared</Badge>}
    </FieldState>
  )
  if (shared) return body
  return (
    <Anchor component={Link} to="/character-management/characters" size="xs">
      {body}
    </Anchor>
  )
}

function recordText(record: Record<string, string | number | null>): string {
  const entries = Object.entries(record)
  if (entries.length === 0) return '–'
  return entries.map(([key, value]) => `${key}: ${value == null ? '–' : value}`).join(', ')
}

export function LedgerTab({
  opening, openingNeedsRebuild, hint, entries, archive,   characters,
  onRebuildOffer, freightBusy, onFreightPreview,
}: {
  opening: RealizedOpeningRow[]
  openingNeedsRebuild: boolean
  hint: string
  entries: RealizedLedgerEntry[]
  archive: RealizedArchiveOwner[]
  characters: EsiTokenCharacter[] | undefined
  onRebuildOffer: (message: string) => void
  freightBusy: boolean
  onFreightPreview: () => void
}) {
  const { data: itemNames } = useItemNameOptions()
  const [kind, setKind] = useState<'cost' | 'revenue'>('cost')
  const [typeId, setTypeId] = useState<string | null>(null)
  const [quantity, setQuantity] = useState<number | ''>('')
  const [amount, setAmount] = useState<number | ''>('')
  const [entryDate, setEntryDate] = useState('')
  const [note, setNote] = useState('')

  const saveOpening = useAction(
    'Opening cost',
    (body: { type_id: number; unit_cost: number; entry_date?: string | null }) =>
      productionApi.setRealizedOpeningCost(body).then(() => 'Saved. It applies on the next recompute.'),
    [['production', 'realized', 'opening'], ['production', 'realized', 'entries']],
    { effect: 'Stores an opening unit-cost override. It applies on the next recompute.' },
  )
  const addEntry = useAction(
    'Ledger entry',
    (body: Parameters<typeof productionApi.addRealizedEntry>[0]) =>
      productionApi.addRealizedEntry(body).then(() => 'Added.'),
    [['production', 'realized']],
    { effect: 'Adds a manual cost or revenue to the period report.' },
  )
  const removeEntry = useAction(
    'Delete ledger entry',
    (entryId: number) => productionApi.deleteRealizedEntry(entryId).then(() => 'Deleted.'),
    [['production', 'realized']],
  )

  const openingColumns: ColumnDef<RealizedOpeningRow, any>[] = [
    { header: 'Type', accessorKey: 'type_name', size: 200, cell: (info) => info.getValue() || `Type ${info.row.original.type_id}` },
    { header: 'Frozen quantity', accessorKey: 'quantity', size: 140, cell: (info) => displayQty(info.getValue()) },
    { header: 'Frozen at', accessorKey: 'frozen_at', size: 160, cell: (info) => when(info.getValue()) },
    { header: 'Reference price on T0', accessorKey: 'reference_price', size: 180, cell: (info) => isk(info.getValue()) },
    { header: 'Unit cost used', accessorKey: 'unit_cost', size: 140, cell: (info) => isk(info.getValue()) },
    { header: 'Price source', accessorKey: 'price_source', size: 130 },
    {
      header: 'Override', id: 'override', size: 220,
      accessorFn: (row) => row.override_unit_cost,
      cell: (info) => {
        const row = info.row.original
        const stored = entries.find((entry) => entry.entry_kind === 'opening_cost' && entry.type_id === row.type_id)
        return (
          <Group gap={4} wrap="nowrap">
            <EditableNumberCell
              value={row.override_unit_cost ?? row.unit_cost}
              ariaLabel={`Opening cost override for ${row.type_name || row.type_id}`}
              isPending={saveOpening.isPending}
              onSave={(value) => saveOpening.mutate(
                { type_id: row.type_id, unit_cost: value },
                { onSuccess: () => onRebuildOffer('Opening cost saved. Recompute to apply it.') },
              )}
            />
            {row.override_unit_cost != null && stored && (
              <Button size="compact-xs" variant="subtle" color="danger"
                onClick={() => modals.openConfirmModal({
                  title: 'Clear opening cost override',
                  children: <Text size="sm">Remove the override for {row.type_name || `type ${row.type_id}`}? The next recompute uses the reference price again.</Text>,
                  labels: { confirm: 'Clear override', cancel: 'Cancel' },
                  confirmProps: { color: 'danger' },
                  onConfirm: () => removeEntry.mutate(stored.entry_id, {
                    onSuccess: () => onRebuildOffer('Opening cost override cleared. Recompute to apply it.'),
                  }),
                })}>
                Clear
              </Button>
            )}
          </Group>
        )
      },
    },
  ]

  const entryColumns: ColumnDef<RealizedLedgerEntry, any>[] = [
    { header: 'Kind', accessorKey: 'entry_kind', size: 120 },
    { header: 'Type', accessorKey: 'type_name', size: 180, cell: (info) => info.getValue() || (info.row.original.type_id ?? '–') },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (info) => info.getValue() == null ? '–' : qty(info.getValue()) },
    { header: 'Amount', accessorKey: 'amount', size: 140, cell: (info) => isk(info.getValue()) },
    { header: 'Date', accessorKey: 'entry_date', size: 160, cell: (info) => when(info.getValue()) },
    { header: 'Note', accessorKey: 'note', size: 220, cell: (info) => info.getValue() || '–' },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (info) => {
        const row = info.row.original
        if (row.entry_kind === 'opening_cost') return <Text size="xs" c="dimmed">Edited above</Text>
        return (
          <Button size="compact-xs" variant="subtle" color="danger" aria-label={`Delete entry ${row.entry_id}`}
            leftSection={<IconTrash size={14} />}
            onClick={() => modals.openConfirmModal({
              title: 'Delete ledger entry',
              children: <Text size="sm">Delete this {row.entry_kind} entry of {isk(row.amount)}?</Text>,
              labels: { confirm: 'Delete', cancel: 'Cancel' },
              confirmProps: { color: 'danger' },
              onConfirm: () => removeEntry.mutate(row.entry_id),
            })}>
            Delete
          </Button>
        )
      },
    },
  ]

  const archiveColumns: ColumnDef<RealizedArchiveOwner, any>[] = [
    {
      header: 'Owner', id: 'owner', size: 200,
      accessorFn: (row) => ownerLabel(row.owner_type, row.owner_id, characters),
    },
    ...SHARE_KINDS.map((kind) => ({
      header: kind.replaceAll('_', ' '),
      id: kind,
      size: 140,
      accessorFn: (row: RealizedArchiveOwner) => (row.shared[kind] ? 'shared' : 'not shared'),
      cell: (info: { row: { original: RealizedArchiveOwner } }) => <ShareState shared={Boolean(info.row.original.shared[kind])} />,
    })),
    {
      header: 'Last sync', id: 'last_sync', size: 240,
      accessorFn: (row) => recordText(row.last_sync),
      cell: (info) => <Text size="xs">{Object.entries(info.row.original.last_sync).map(([key, value]) => `${key}: ${when(value)}`).join(' · ') || '–'}</Text>,
    },
    {
      header: 'Gaps', id: 'gaps', size: 160,
      accessorFn: (row) => recordText(row.gaps),
    },
    {
      header: 'Hidden', id: 'hidden', size: 220,
      accessorFn: (row) => row.hidden_kinds.join(', '),
      cell: (info) => info.row.original.hidden_kinds.length === 0
        ? '–'
        : `Kept until shared again: ${info.row.original.hidden_kinds.join(', ')}`,
    },
  ]

  function submitEntry() {
    if (amount === '' || !entryDate) return
    addEntry.mutate({
      entry_kind: kind,
      amount: Number(amount),
      entry_date: entryDate,
      type_id: typeId ? Number(typeId) : null,
      quantity: quantity === '' ? null : Number(quantity),
      note: note.trim() || null,
    }, {
      onSuccess: () => {
        setAmount('')
        setQuantity('')
        setNote('')
      },
    })
  }

  return (
    <Stack gap="lg">
      <div>
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">Opening inventory</Title>
        {openingNeedsRebuild ? <HintCard>{hint}</HintCard> : (
          <DataTable
            tableId="production-realized-opening"
            data={opening}
            columns={openingColumns}
            maxHeight={360}
            getRowId={(row) => String(row.type_id)}
          />
        )}
      </div>

      <div>
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">Manual entries</Title>
        <Group align="flex-end" mb="sm">
          <Select label="Kind" data={[{ value: 'cost', label: 'Cost' }, { value: 'revenue', label: 'Revenue' }]}
            value={kind} onChange={(value) => value && setKind(value as 'cost' | 'revenue')} w={140} />
          <SearchableSelect
            label="Type"
            placeholder="Optional"
            data={(itemNames ?? []).map((row) => ({ value: String(row.type_id), label: row.type_name }))}
            value={typeId}
            onChange={setTypeId}
            w={260}
          />
          <NumberInput label="Quantity" value={quantity} onChange={(value) => setQuantity(value === '' ? '' : Number(value))} min={0} w={120} />
          <NumberInput label="Amount (ISK)" value={amount} onChange={(value) => setAmount(value === '' ? '' : Number(value))} min={0} w={160} />
          <TextInput label="Date" type="date" value={entryDate} onChange={(event) => setEntryDate(event.currentTarget.value)} w={160} />
          <TextInput label="Note" value={note} onChange={(event) => setNote(event.currentTarget.value)} w={220} maxLength={500} />
          <Button onClick={submitEntry} loading={addEntry.isPending} disabled={amount === '' || !entryDate}>Add entry</Button>
        </Group>
        <DataTable
          tableId="production-realized-entries"
          data={entries}
          columns={entryColumns}
          maxHeight={320}
          getRowId={(row) => String(row.entry_id)}
        />
      </div>

      <div>
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">Freight</Title>
        <Text size="sm" c="dimmed" mb="xs">
          Re-apply writes the current configured rate onto purchases that already have a freight volume. It does not recompute profit.
        </Text>
        <Button variant="default" loading={freightBusy} onClick={onFreightPreview}>
          Re-apply current freight rate
        </Button>
      </div>

      <div>
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">Archive status</Title>
        <Text size="sm" c="dimmed" mb="xs">
          Hidden kinds are archived but not shared with Production. The rows are kept and come back when shared again.
        </Text>
        <DataTable
          tableId="production-realized-archive"
          data={archive}
          columns={archiveColumns}
          maxHeight={360}
          getRowId={(row) => `${row.owner_type}:${row.owner_id}`}
        />
      </div>
    </Stack>
  )
}
