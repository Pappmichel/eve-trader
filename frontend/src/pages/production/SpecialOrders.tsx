import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert, Badge, Button, Card, Checkbox, Group, NumberInput, Stack, Text, Textarea, Title, ActionIcon,
} from '@mantine/core'
import { modals } from '@mantine/modals'
import { IconPlus, IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type {
  BuildJobEntry, BuyListEntry, InventionNeedRow, SpecialOrder, SpecialOrderLineItem,
} from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { dateTime, isk, pct, qty } from '../../format'

const SPECIAL_ORDER_KEYS = [['production', 'special-orders']]

function stockpileBadge(v: number) {
  const color = v >= 50 ? 'accent' : v > 0 ? 'warn' : 'gray'
  return <Badge color={color} variant="light">{v.toFixed(0)}%</Badge>
}

// One draft item row in the "New Order" form - typeId is the SearchableSelect's
// own string value (its underlying option value is the type_id, stringified,
// same convention MaterialTree.tsx/StockTargets.tsx already use).
interface DraftItem {
  key: number
  typeId: string | null
  quantity: number | ''
}

let draftKeySeq = 0
function newDraftItem(): DraftItem {
  return { key: draftKeySeq++, typeId: null, quantity: 1 }
}

function NewOrderForm() {
  const { data: itemNameOptions } = useItemNameOptions()
  const options = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [draftItems, setDraftItems] = useState<DraftItem[]>([newDraftItem()])
  const [note, setNote] = useState('')
  const [netAgainstStock, setNetAgainstStock] = useState(false)

  const createOrder = useAction('Create Special Order', productionApi.createSpecialOrder, SPECIAL_ORDER_KEYS)

  const updateDraft = (key: number, patch: Partial<DraftItem>) =>
    setDraftItems((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const removeDraft = (key: number) => setDraftItems((rows) => rows.filter((r) => r.key !== key))

  const validItems = draftItems
    .filter((r) => r.typeId !== null && r.quantity !== '' && Number(r.quantity) > 0)
    .map((r) => ({ type_id: Number(r.typeId), quantity: Number(r.quantity) }))

  const handleCreate = () => {
    createOrder.mutate(
      { items: validItems, note: note.trim() || null, net_against_stock: netAgainstStock },
      {
        onSuccess: () => {
          setDraftItems([newDraftItem()])
          setNote('')
          setNetAgainstStock(false)
        },
      },
    )
  }

  return (
    <Card withBorder>
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">New Special Order</Title>
      <Text size="xs" c="dimmed" mb="sm">
        A one-off order (multiple items allowed) - never added to Stock Targets. Computed independently from the
        regular Buy/Build list, always in full (no margin gate).
      </Text>
      <Stack gap="xs">
        {draftItems.map((row) => (
          <Group key={row.key} align="flex-end" wrap="nowrap">
            <SearchableSelect
              label="Item name" placeholder="Search item…" data={options}
              value={row.typeId} onChange={(v) => updateDraft(row.key, { typeId: v })} w={320}
            />
            <NumberInput
              label="Quantity" value={row.quantity} min={1} w={140}
              onChange={(v) => updateDraft(row.key, { quantity: v === '' ? '' : Number(v) })}
            />
            <ActionIcon size="lg" variant="subtle" color="danger" aria-label="Remove item row"
              disabled={draftItems.length === 1} onClick={() => removeDraft(row.key)}>
              <IconTrash size={16} />
            </ActionIcon>
          </Group>
        ))}
        <Button variant="default" size="xs" leftSection={<IconPlus size={14} />}
          onClick={() => setDraftItems((rows) => [...rows, newDraftItem()])} w={160}>
          Add Item
        </Button>
      </Stack>

      <Textarea label="Note (optional)" placeholder="Customer / purpose" value={note}
        onChange={(e) => setNote(e.currentTarget.value)} mt="sm" autosize minRows={1} maxRows={3} />
      <Checkbox
        label="Prefer current stock (net against real stock instead of planning from scratch)"
        checked={netAgainstStock} onChange={(e) => setNetAgainstStock(e.currentTarget.checked)} mt="sm"
      />
      <Button mt="sm" onClick={handleCreate} loading={createOrder.isPending} disabled={validItems.length === 0}>
        Create Order
      </Button>
    </Card>
  )
}

function OrderDetail({ order }: { order: SpecialOrder }) {
  const { data: detail } = useQuery({
    queryKey: ['production', 'special-orders', order.order_id],
    queryFn: () => productionApi.getSpecialOrder(order.order_id),
  })
  const compute = useAction('Compute Special Order', () => productionApi.computeSpecialOrder(order.order_id))
  const result = compute.data

  const itemColumns = useMemo<ColumnDef<SpecialOrderLineItem, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 240 },
    { header: 'Quantity', accessorKey: 'quantity', size: 120, cell: (i) => qty(i.getValue()) },
  ], [])

  const buyColumns = useMemo<ColumnDef<BuyListEntry, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    {
      header: 'Buy From', accessorKey: 'buy_from', size: 120,
      cell: (i) => {
        const v = i.getValue() as string | null
        return v ? <Badge color={v === 'C-J' ? 'accent' : 'info'} variant="light">{v}</Badge> : '–'
      },
    },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Unit Price', accessorKey: 'unit_price', size: 120, cell: (i) => isk(i.getValue()) },
    { header: 'Total Price', accessorKey: 'total_price', size: 140, cell: (i) => isk(i.getValue()) },
  ], [])

  const buildColumns = useMemo<ColumnDef<BuildJobEntry, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    { header: 'Category', accessorKey: 'job_category', size: 150, cell: (i) => i.getValue() ?? '–' },
    { header: 'Activity', accessorKey: 'activity', size: 120 },
    { header: 'Job Runs', accessorKey: 'job_runs', size: 100, cell: (i) => qty(i.getValue()) },
    { header: 'Job Time (h)', id: 'hours', size: 110, accessorFn: (r) => r.job_time_seconds / 3600, cell: (i) => (i.getValue() as number).toFixed(2) },
    { header: 'Modeled Unit Cost', accessorKey: 'unit_build_cost', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: 'Margin', accessorKey: 'margin', size: 110,
      cell: (i) => {
        const v = i.getValue()
        return v === null ? '–' : <Text c={v > 0 ? 'accent' : 'danger'}>{pct(v)}</Text>
      },
    },
  ], [])

  const inventionColumns = useMemo<ColumnDef<InventionNeedRow, any>[]>(() => [
    { header: 'T2 Item', accessorKey: 'type_name', size: 210 },
    { header: 'T1 Blueprint', accessorKey: 't1_blueprint_name', size: 210 },
    { header: 'Decryptor', accessorKey: 'decryptor', size: 130 },
    { header: 'Success Chance', accessorKey: 'probability', size: 130, cell: (i) => pct(i.getValue()) },
    { header: 'Runs Needed', accessorKey: 'runs_needed', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Recommended Runs', accessorKey: 'recommended_invention_runs', size: 160, cell: (i) => qty(i.getValue()) },
    { header: 'Stockpile %', accessorKey: 'stockpile_pct', size: 120, cell: (i) => stockpileBadge(i.getValue()) },
  ], [])

  return (
    <Card withBorder mt="sm">
      <Group justify="space-between" mb="xs">
        <Title order={6}>{order.note ?? `Order ${order.order_id.slice(0, 8)}`}</Title>
        <Button size="xs" onClick={() => compute.mutate()} loading={compute.isPending}>
          {result ? 'Recompute' : 'Compute'}
        </Button>
      </Group>

      <DataTable data={detail?.items ?? []} columns={itemColumns} maxHeight={200} />

      {!result && <HintCard>Click Compute to see the Buy/Build/Invention breakdown for this order.</HintCard>}

      {result && (
        <Stack mt="md" gap="md">
          {result.stock_overlap_warning.length > 0 && (
            <Alert color="warn" variant="light" title="Possible stock overlap with your Stock Targets">
              These items are on hand right now and also reachable from your configured Stock Targets - the same
              physical stock might get claimed by both this order and a separately-computed regular Buy/Build list:
              {' '}
              {result.stock_overlap_warning.map((w) => `${w.type_name} (${qty(w.current_stock)})`).join(', ')}.
            </Alert>
          )}

          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Buy List</Title>
            {result.buy_list.length === 0
              ? <HintCard>Nothing to buy.</HintCard>
              : <DataTable data={result.buy_list} columns={buyColumns} maxHeight={320} />}
          </div>

          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Build List</Title>
            {result.build_list.length === 0
              ? <HintCard>Nothing to build.</HintCard>
              : <DataTable data={result.build_list} columns={buildColumns} maxHeight={320} />}
          </div>

          {result.invention_list.length > 0 && (
            <div>
              <Title order={6} c="dimmed" tt="uppercase" mb="xs">Invention</Title>
              <DataTable data={result.invention_list} columns={inventionColumns} maxHeight={320} />
            </div>
          )}
        </Stack>
      )}
    </Card>
  )
}

export default function SpecialOrders() {
  const { data: orders, isLoading, isError, refetch, dataUpdatedAt } =
    useQuery({ queryKey: ['production', 'special-orders'], queryFn: productionApi.specialOrders })

  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const updateOrder = useAction('Save Special Order', (args: { orderId: string; status: string }) =>
    productionApi.updateSpecialOrder(args.orderId, { status: args.status }), SPECIAL_ORDER_KEYS)
  const removeOrder = useAction('Remove Special Order', productionApi.removeSpecialOrder, SPECIAL_ORDER_KEYS)

  const columns = useMemo<ColumnDef<SpecialOrder, any>[]>(() => [
    { header: 'Note', accessorKey: 'note', size: 220, cell: (i) => i.getValue() ?? '–' },
    { header: 'Items', accessorKey: 'item_count', size: 90, cell: (i) => qty(i.getValue()) },
    {
      header: 'Status', accessorKey: 'status', size: 100,
      cell: (i) => <Badge color={i.getValue() === 'done' ? 'gray' : 'accent'} variant="light">{i.getValue()}</Badge>,
    },
    {
      header: 'Prefers Stock', accessorKey: 'net_against_stock', size: 130,
      cell: (i) => (i.getValue() ? <Badge color="info" variant="light">Yes</Badge> : '–'),
    },
    { header: 'Created', accessorKey: 'created_at', size: 170, cell: (i) => dateTime(i.getValue()) },
    {
      header: '', id: 'actions', size: 210, enableSorting: false,
      cell: (i) => {
        const order = i.row.original
        const isPending = updateOrder.isPending && pendingId === order.order_id
        return (
          <Group gap={4} wrap="nowrap">
            <Button size="xs" variant="default" onClick={() => setExpandedId(expandedId === order.order_id ? null : order.order_id)}>
              {expandedId === order.order_id ? 'Hide' : 'Open'}
            </Button>
            <Button
              size="xs" variant="default" loading={isPending}
              onClick={() => {
                setPendingId(order.order_id)
                updateOrder.mutate({ orderId: order.order_id, status: order.status === 'done' ? 'open' : 'done' })
              }}
            >
              {order.status === 'done' ? 'Reopen' : 'Complete'}
            </Button>
            <ActionIcon size="lg" variant="subtle" color="danger" aria-label={`Remove order ${order.note ?? order.order_id}`}
              onClick={() => modals.openConfirmModal({
                title: 'Remove special order',
                children: <Text size="sm">Remove this special order and its items? This can't be undone.</Text>,
                labels: { confirm: 'Remove', cancel: 'Cancel' },
                confirmProps: { color: 'danger' },
                onConfirm: () => {
                  if (expandedId === order.order_id) setExpandedId(null)
                  removeOrder.mutate(order.order_id)
                },
              })}
            >
              <IconTrash size={14} />
            </ActionIcon>
          </Group>
        )
      },
    },
  ], [expandedId, pendingId, updateOrder, removeOrder])

  const expandedOrder = orders?.find((o) => o.order_id === expandedId) ?? null

  return (
    <Stack>
      <NewOrderForm />

      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={420} />
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={420} />
      ) : !orders || orders.length === 0 ? (
        <HintCard>No special orders yet.</HintCard>
      ) : (
        <DataTable
          data={orders} columns={columns} maxHeight={420}
          getRowId={(row) => row.order_id} dataUpdatedAt={dataUpdatedAt}
        />
      )}

      {expandedOrder && <OrderDetail order={expandedOrder} />}
    </Stack>
  )
}
