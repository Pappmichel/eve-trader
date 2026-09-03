import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, Badge, MultiSelect } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { BuyListEntry } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'

const CATEGORY_UNKNOWN = 'no category'

export default function BuyList() {
  const { data: plan, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'plan'], queryFn: productionApi.plan })
  const buyList = plan?.buy_list ?? []

  const refreshPlan = useAction('Refresh Production', productionApi.refreshPlan, [
    ['production', 'plan'], ['production', 'stock-targets'], ['production', 'logistics'],
  ])

  const categories = useMemo(
    () => [...new Set(buyList.map((e) => e.category ?? CATEGORY_UNKNOWN))].sort(), [buyList],
  )
  const [selCategories, setSelCategories] = useState<string[]>([])
  const filtered = useMemo(() => {
    if (selCategories.length === 0) return buyList
    return buyList.filter((e) => selCategories.includes(e.category ?? CATEGORY_UNKNOWN))
  }, [buyList, selCategories])

  const total = useMemo(() => filtered.reduce((sum, e) => sum + (e.total_price ?? 0), 0), [filtered])

  const columns = useMemo<ColumnDef<BuyListEntry, any>[]>(() => [
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

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
  if (!plan) {
    return (
      <Stack align="flex-start">
        <HintCard>No computation yet.</HintCard>
        <Button onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
          Compute Buy/Build List
        </Button>
      </Stack>
    )
  }
  if (buyList.length === 0) return <HintCard>Nothing to buy - all stock targets are covered.</HintCard>

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <Card withBorder padding="sm" w={280}>
            <Text size="xs" c="dimmed" tt="uppercase">Total Buy List Value</Text>
            <Title order={3} c="accent">{isk(total)}</Title>
          </Card>
          <MultiSelect
            label="Category" data={categories} value={selCategories} onChange={setSelCategories}
            placeholder="All" clearable w={280}
          />
        </Group>
        <Button variant="default" onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
          Recompute
        </Button>
      </Group>
      <Text size="xs" c="dimmed">{filtered.length} of {buyList.length} items</Text>
      {filtered.length === 0 ? (
        <HintCard>No items in the selected categories.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      )}
    </Stack>
  )
}
