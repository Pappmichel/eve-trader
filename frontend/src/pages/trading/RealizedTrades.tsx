import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Text, Title, Stack } from '@mantine/core'
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid } from 'recharts'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { RealizedTrade } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { isk, pct, qty } from '../../format'
import { COLORS } from '../../theme'

interface ItemSummary {
  type_id: number
  item: string
  trades: number
  matchedQty: number
  avgBuyPrice: number
  avgSellPrice: number
  avgMargin: number
  totalProfit: number
}

export default function RealizedTrades() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['trading', 'trades', 'realized'], queryFn: tradingApi.realizedTrades })
  const total = useMemo(() => (data ?? []).reduce((sum, t) => sum + t.realized_profit, 0), [data])

  // One row per item (not per trade) - price/margin columns are the average
  // across that item's matched trades, quantity/profit are summed.
  const byItem = useMemo<ItemSummary[]>(() => {
    const groups = new Map<number, RealizedTrade[]>()
    for (const t of data ?? []) {
      const list = groups.get(t.type_id) ?? []
      list.push(t)
      groups.set(t.type_id, list)
    }
    return [...groups.values()].map((trades) => ({
      type_id: trades[0].type_id,
      item: trades[0].item,
      trades: trades.length,
      matchedQty: trades.reduce((sum, t) => sum + t.matched_qty, 0),
      avgBuyPrice: trades.reduce((sum, t) => sum + t.buy_unit_price, 0) / trades.length,
      avgSellPrice: trades.reduce((sum, t) => sum + t.sell_unit_price, 0) / trades.length,
      avgMargin: trades.reduce((sum, t) => sum + t.margin, 0) / trades.length,
      totalProfit: trades.reduce((sum, t) => sum + t.realized_profit, 0),
    })).sort((a, b) => b.totalProfit - a.totalProfit)
  }, [data])

  const topByProfit = useMemo(() => byItem.slice(0, 15), [byItem])

  const cumulativeProfit = useMemo(() => {
    const byDay = new Map<string, number>()
    for (const t of data ?? []) {
      const day = t.sell_date.slice(0, 10)
      byDay.set(day, (byDay.get(day) ?? 0) + t.realized_profit)
    }
    const days = [...byDay.keys()].sort()
    let running = 0
    return days.map((date) => {
      running += byDay.get(date) ?? 0
      return { date, cumulative: running }
    })
  }, [data])

  const columns = useMemo<ColumnDef<ItemSummary, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 220 },
    { header: 'Trades', accessorKey: 'trades', size: 90, cell: (i) => qty(i.getValue()) },
    { header: 'Total Qty', accessorKey: 'matchedQty', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Avg Buy Price', accessorKey: 'avgBuyPrice', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Avg Sell Price', accessorKey: 'avgSellPrice', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Avg Margin', accessorKey: 'avgMargin', size: 110, cell: (i) => pct(i.getValue()) },
    { header: 'Total Profit', accessorKey: 'totalProfit', size: 140, cell: (i) => isk(i.getValue()) },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={480} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={480} />
  if (!data || data.length === 0) {
    return <HintCard>No realized trades yet. Click <b>Reconcile Trades</b> on the left once buyer and seller are logged in.</HintCard>
  }

  return (
    <Stack>
      <Card withBorder padding="sm" w={260}>
        <Text size="xs" c="dimmed" tt="uppercase">Total Profit</Text>
        <Title order={3} c="accent">{isk(total)}</Title>
      </Card>

      <Text size="sm" c="dimmed">{byItem.length} items, {data.length} trades total</Text>

      <DataTable data={byItem} columns={columns} maxHeight={480} dataUpdatedAt={dataUpdatedAt} />

      <Title order={6} c="dimmed" tt="uppercase" mt="lg">Cumulative Profit Over Time</Title>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={cumulativeProfit}>
          <CartesianGrid stroke={COLORS.border} />
          <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <YAxis stroke={COLORS.textDim} tickFormatter={(v) => isk(v)} width={110} />
          <Tooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }}
            formatter={(v) => isk(Number(v))} />
          <Line type="monotone" dataKey="cumulative" stroke={COLORS.accent} dot={false} />
        </LineChart>
      </ResponsiveContainer>

      <Title order={6} c="dimmed" tt="uppercase" mt="lg">Top Items by Profit</Title>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={topByProfit} layout="vertical" margin={{ left: 120 }}>
          <XAxis type="number" stroke={COLORS.textDim} />
          <YAxis type="category" dataKey="item" width={200} stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <Tooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }} />
          <Bar dataKey="totalProfit" fill={COLORS.accent} />
        </BarChart>
      </ResponsiveContainer>
    </Stack>
  )
}
