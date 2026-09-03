import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Badge, Button, Card, Checkbox, Group, MultiSelect, NumberInput, Stack, Text, Textarea, Title, ActionIcon,
} from '@mantine/core'
import { modals } from '@mantine/modals'
import { IconCheck, IconPlus, IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type {
  BuildJobEntry, BuyListEntry, InventionNeedRow, SpecialOrder, SpecialOrderComputeResult, SpecialOrderLineItem,
} from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { dateTime, isk, pct, qty } from '../../format'

const SPECIAL_ORDER_KEYS = [['production', 'special-orders']]
const CATEGORY_UNKNOWN = 'no category'

function stockpileBadge(v: number) {
  const color = v >= 50 ? 'accent' : v > 0 ? 'warn' : 'gray'
  return <Badge color={color} variant="light">{v.toFixed(0)}%</Badge>
}

// Same "local draft state, checkmark appears once it differs from the saved
// value, click to save" pattern as StockTargets.tsx's own EditableNumberCell
// - see that component's comment for why getRowId (used below) matters here.
function EditableQuantityCell({ value, ariaLabel, isPending, onSave }: {
  value: number
  ariaLabel: string
  isPending: boolean
  onSave: (value: number) => void
}) {
  const [draft, setDraft] = useState(value)
  const dirty = draft !== value
  return (
    <Group gap={4} wrap="nowrap">
      <NumberInput
        value={draft} onChange={(v) => setDraft(v === '' ? 0 : Number(v))}
        min={0.01} size="xs" w={110} aria-label={ariaLabel}
      />
      {dirty && (
        <ActionIcon size="sm" variant="filled" color="accent" aria-label={`Save ${ariaLabel}`}
          onClick={() => onSave(draft)} loading={isPending}>
          <IconCheck size={14} />
        </ActionIcon>
      )}
    </Group>
  )
}

// Shared Buy/Build/Invention/overlap-warning rendering - used by both a
// single order's own detail view and the temporary combined-orders preview,
// so the two can never drift apart on columns/filtering/total-cost display.
function ComputeResultView({ result }: { result: SpecialOrderComputeResult }) {
  const [selCategories, setSelCategories] = useState<string[]>([])
  const categories = useMemo(
    () => [...new Set(result.buy_list.map((e) => e.category ?? CATEGORY_UNKNOWN))].sort(), [result.buy_list],
  )
  const filteredBuyList = useMemo(() => {
    if (selCategories.length === 0) return result.buy_list
    return result.buy_list.filter((e) => selCategories.includes(e.category ?? CATEGORY_UNKNOWN))
  }, [result.buy_list, selCategories])
  const totalCost = useMemo(() => filteredBuyList.reduce((sum, e) => sum + (e.total_price ?? 0), 0), [filteredBuyList])

  const buyColumns = useMemo<ColumnDef<BuyListEntry, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    { header: 'Category', accessorKey: 'category', size: 150, cell: (i) => i.getValue() ?? '–' },
    {
      header: 'Buy From', accessorKey: 'buy_from', size: 120,
      cell: (i) => {
        const v = i.getValue() as string | null
        return v ? <Badge color={v === 'C-J' ? 'accent' : 'info'} variant="light">{v}</Badge> : '–'
      },
    },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
    {
      header: 'On Hand', accessorKey: 'on_hand_pct', size: 100,
      cell: (i) => {
        const v = i.getValue() as number
        const color = v >= 50 ? 'accent' : v > 0 ? 'warn' : 'gray'
        return <Badge color={color} variant="light">{v.toFixed(0)}%</Badge>
      },
    },
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
    <Stack gap="md">
      {result.stock_overlap_warning.length > 0 && (
        <Alert color="warn" variant="light" title="Possible stock overlap with your Stock Targets">
          These items are on hand right now and also reachable from your configured Stock Targets - the same
          physical stock might get claimed by both this order and a separately-computed regular Buy/Build list:
          {' '}
          {result.stock_overlap_warning.map((w) => `${w.type_name} (${qty(w.current_stock)})`).join(', ')}.
        </Alert>
      )}

      <div>
        <Group justify="space-between" align="flex-end" mb="xs">
          <Title order={6} c="dimmed" tt="uppercase">Buy List</Title>
          {result.buy_list.length > 0 && (
            <Group align="flex-end">
              <Card withBorder padding="sm" w={220}>
                <Text size="xs" c="dimmed" tt="uppercase">Total Cost</Text>
                <Title order={4} c="accent">{isk(totalCost)}</Title>
              </Card>
              <MultiSelect
                label="Category" data={categories} value={selCategories} onChange={setSelCategories}
                placeholder="All" clearable w={240} size="sm"
              />
            </Group>
          )}
        </Group>
        {result.buy_list.length === 0 ? (
          <HintCard>Nothing to buy.</HintCard>
        ) : filteredBuyList.length === 0 ? (
          <HintCard>No items in the selected categories.</HintCard>
        ) : (
          <DataTable data={filteredBuyList} columns={buyColumns} maxHeight={320} />
        )}
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
  )
}

// One draft item row in the "New Order" form / the "Add Item" row in an
// existing order - typeId is the SearchableSelect's own string value (its
// underlying option value is the type_id, stringified, same convention
// MaterialTree.tsx/StockTargets.tsx already use).
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
        regular Buy/Build list, always in full (no margin gate). Items, quantities, and "prefer stock" stay
        editable after creation.
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
        label="Prefer current stock (net component/material demand against real stock instead of planning from scratch - the ordered quantity itself is always sourced fresh either way)"
        checked={netAgainstStock} onChange={(e) => setNetAgainstStock(e.currentTarget.checked)} mt="sm"
      />
      <Button mt="sm" onClick={handleCreate} loading={createOrder.isPending} disabled={validItems.length === 0}>
        Create Order
      </Button>
    </Card>
  )
}

function OrderItemsEditor({ order, items }: { order: SpecialOrder; items: SpecialOrderLineItem[] }) {
  const queryClient = useQueryClient()
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['production', 'special-orders'] })
    queryClient.invalidateQueries({ queryKey: ['production', 'special-orders', order.order_id] })
  }
  const setItem = useAction('Save Item', (args: { typeId: number; quantity: number }) =>
    productionApi.setSpecialOrderItem(order.order_id, args.typeId, args.quantity))
  const removeItem = useAction('Remove Item', (typeId: number) =>
    productionApi.removeSpecialOrderItem(order.order_id, typeId))
  const [pendingTypeId, setPendingTypeId] = useState<number | null>(null)

  const { data: itemNameOptions } = useItemNameOptions()
  const options = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [newTypeId, setNewTypeId] = useState<string | null>(null)
  const [newQuantity, setNewQuantity] = useState<number | ''>(1)

  const columns = useMemo<ColumnDef<SpecialOrderLineItem, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 240 },
    {
      header: 'Quantity', accessorKey: 'quantity', size: 160,
      cell: (i) => (
        <EditableQuantityCell
          value={i.getValue()} ariaLabel={`Quantity for ${i.row.original.type_name}`}
          isPending={setItem.isPending && pendingTypeId === i.row.original.type_id}
          onSave={(value) => {
            setPendingTypeId(i.row.original.type_id)
            setItem.mutate({ typeId: i.row.original.type_id, quantity: value }, { onSuccess: invalidate })
          }}
        />
      ),
    },
    {
      header: '', id: 'actions', size: 50, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Remove ${i.row.original.type_name}`}
          disabled={items.length === 1}
          loading={removeItem.isPending && pendingTypeId === i.row.original.type_id}
          onClick={() => {
            setPendingTypeId(i.row.original.type_id)
            removeItem.mutate(i.row.original.type_id, { onSuccess: invalidate })
          }}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [items.length, setItem.isPending, removeItem.isPending, pendingTypeId])

  return (
    <Stack gap="xs">
      <DataTable data={items} columns={columns} maxHeight={240} getRowId={(row) => String(row.type_id)} />
      <Group align="flex-end" wrap="nowrap">
        <SearchableSelect
          label="Add item" placeholder="Search item…" data={options}
          value={newTypeId} onChange={setNewTypeId} w={320}
        />
        <NumberInput
          label="Quantity" value={newQuantity} min={1} w={140}
          onChange={(v) => setNewQuantity(v === '' ? '' : Number(v))}
        />
        <Button
          variant="default" leftSection={<IconPlus size={14} />}
          disabled={newTypeId === null || newQuantity === '' || Number(newQuantity) <= 0}
          loading={setItem.isPending && pendingTypeId === null}
          onClick={() => {
            setPendingTypeId(null)
            setItem.mutate({ typeId: Number(newTypeId), quantity: Number(newQuantity) }, {
              onSuccess: () => { invalidate(); setNewTypeId(null); setNewQuantity(1) },
            })
          }}
        >
          Add
        </Button>
      </Group>
    </Stack>
  )
}

function OrderDetail({ order }: { order: SpecialOrder }) {
  const queryClient = useQueryClient()
  const { data: detail } = useQuery({
    queryKey: ['production', 'special-orders', order.order_id],
    queryFn: () => productionApi.getSpecialOrder(order.order_id),
  })
  const compute = useAction('Compute Special Order', () => productionApi.computeSpecialOrder(order.order_id))
  const result = compute.data
  const setNetAgainstStock = useAction('Save Special Order',
    (netAgainstStock: boolean) => productionApi.updateSpecialOrder(order.order_id, { net_against_stock: netAgainstStock }),
    SPECIAL_ORDER_KEYS)

  return (
    <Card withBorder mt="sm">
      <Group justify="space-between" mb="xs">
        <Title order={6}>{order.note ?? `Order ${order.order_id.slice(0, 8)}`}</Title>
        <Button size="xs" onClick={() => compute.mutate()} loading={compute.isPending}>
          {result ? 'Recompute' : 'Compute'}
        </Button>
      </Group>

      {detail && <OrderItemsEditor order={order} items={detail.items} />}

      <Checkbox
        mt="sm"
        label="Prefer current stock (net component/material demand against real stock - the ordered quantity itself is always sourced fresh either way)"
        checked={order.net_against_stock} disabled={setNetAgainstStock.isPending}
        onChange={(e) => setNetAgainstStock.mutate(e.currentTarget.checked, {
          onSuccess: () => queryClient.invalidateQueries({ queryKey: ['production', 'special-orders'] }),
        })}
      />

      <Stack mt="md">
        {!result && <HintCard>Click Compute to see the Buy/Build/Invention breakdown for this order.</HintCard>}
        {result && <ComputeResultView result={result} />}
      </Stack>
    </Card>
  )
}

function CombinePanel({ orderIds, onClear }: { orderIds: string[]; onClear: () => void }) {
  const [netAgainstStock, setNetAgainstStock] = useState(false)
  const combine = useAction('Combine Special Orders',
    () => productionApi.computeCombinedSpecialOrders(orderIds, netAgainstStock))
  const result = combine.data

  return (
    <Card withBorder mt="sm">
      <Group justify="space-between" mb="xs">
        <Title order={6}>Combined preview - {orderIds.length} orders</Title>
        <Button size="xs" variant="subtle" onClick={onClear}>Clear selection</Button>
      </Group>
      <Text size="xs" c="dimmed" mb="sm">
        Temporary - pools the selected orders' own items (shared items summed) into one Buy/Build/Invention
        computation. Nothing is saved; the individual orders are never changed.
      </Text>
      <Group align="flex-end">
        <Checkbox
          label="Prefer current stock for this combined preview"
          checked={netAgainstStock} onChange={(e) => setNetAgainstStock(e.currentTarget.checked)}
        />
        <Button size="xs" onClick={() => combine.mutate()} loading={combine.isPending}>
          {result ? 'Recompute' : 'Compute Combined'}
        </Button>
      </Group>
      {result && <Stack mt="md"><ComputeResultView result={result} /></Stack>}
    </Card>
  )
}

export default function SpecialOrders() {
  const { data: orders, isLoading, isError, refetch, dataUpdatedAt } =
    useQuery({ queryKey: ['production', 'special-orders'], queryFn: productionApi.specialOrders })

  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const updateOrder = useAction('Save Special Order', (args: { orderId: string; status: string }) =>
    productionApi.updateSpecialOrder(args.orderId, { status: args.status }), SPECIAL_ORDER_KEYS)
  const removeOrder = useAction('Remove Special Order', productionApi.removeSpecialOrder, SPECIAL_ORDER_KEYS)

  const toggleSelected = (orderId: string) => setSelectedIds((prev) => {
    const next = new Set(prev)
    if (next.has(orderId)) next.delete(orderId)
    else next.add(orderId)
    return next
  })

  const columns = useMemo<ColumnDef<SpecialOrder, any>[]>(() => [
    {
      header: '', id: 'select', size: 40, enableSorting: false,
      cell: (i) => (
        <Checkbox
          aria-label={`Select order ${i.row.original.note ?? i.row.original.order_id}`}
          checked={selectedIds.has(i.row.original.order_id)}
          onChange={() => toggleSelected(i.row.original.order_id)}
        />
      ),
    },
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
                  setSelectedIds((prev) => { const next = new Set(prev); next.delete(order.order_id); return next })
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [expandedId, pendingId, updateOrder, removeOrder, selectedIds])

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
        <>
          <Text size="xs" c="dimmed">Select 2 or more orders to combine them into a temporary preview.</Text>
          <DataTable
            data={orders} columns={columns} maxHeight={420}
            getRowId={(row) => row.order_id} dataUpdatedAt={dataUpdatedAt}
          />
        </>
      )}

      {selectedIds.size >= 2 && (
        <CombinePanel orderIds={[...selectedIds]} onClear={() => setSelectedIds(new Set())} />
      )}

      {expandedOrder && <OrderDetail order={expandedOrder} />}
    </Stack>
  )
}
