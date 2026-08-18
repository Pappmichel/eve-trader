import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, Badge } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { BuyListEntry } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'

export default function BuyList() {
  const { data: plan, isLoading } = useQuery({ queryKey: ['production', 'plan'], queryFn: productionApi.plan })
  const buyList = plan?.buy_list ?? []

  const refreshPlan = useAction('Refresh Production', productionApi.refreshPlan, [
    ['production', 'plan'], ['production', 'stock-targets'], ['production', 'logistics'],
  ])

  const total = useMemo(() => buyList.reduce((sum, e) => sum + (e.total_price ?? 0), 0), [buyList])

  const columns = useMemo<ColumnDef<BuyListEntry, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
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
        <Card withBorder padding="sm" w={280}>
          <Text size="xs" c="dimmed" tt="uppercase">Total Buy List Value</Text>
          <Title order={3} c="accent">{isk(total)}</Title>
        </Card>
        <Button variant="default" onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
          Recompute
        </Button>
      </Group>
      <DataTable data={buyList} columns={columns} maxHeight={560} />
    </Stack>
  )
}
