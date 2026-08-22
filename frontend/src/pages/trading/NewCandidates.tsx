import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Badge } from '@mantine/core'
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { NewCandidateResult } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { pct, qty } from '../../format'
import { COLORS } from '../../theme'

export default function NewCandidates() {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['trading', 'candidates', 'new'], queryFn: tradingApi.newCandidates })
  const sorted = useMemo(() => [...(data ?? [])].sort((a, b) => b.score - a.score), [data])

  const columns = useMemo<ColumnDef<NewCandidateResult, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 220 },
    { header: 'Category', accessorKey: 'category', size: 120 },
    {
      header: 'Recommendation', accessorKey: 'recommendation', size: 150,
      cell: (i) => <Badge color={i.getValue() !== 'Skip' ? 'accent' : 'warn'} variant="light">{i.getValue()}</Badge>,
    },
    { header: 'Score', accessorKey: 'score', size: 90, cell: (i) => (i.getValue() as number).toFixed(1) },
    { header: 'Hit Rate', accessorKey: 'hit_rate', size: 100, cell: (i) => pct(i.getValue()) },
    { header: 'Margin (current)', accessorKey: 'latest_margin', size: 130, cell: (i) => pct(i.getValue()) },
    { header: 'Best Margin', accessorKey: 'best_margin', size: 110, cell: (i) => pct(i.getValue()) },
    { header: 'Avg Profit/m³', accessorKey: 'avg_profit_m3', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Days (Data)', accessorKey: 'paired_days', size: 110, cell: (i) => qty(i.getValue()) },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={480} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={480} />
  if (!data || data.length === 0) {
    return <HintCard>No run yet. Click <b>Search + Add + Clean Up</b> on the left to backtest candidates against price history.</HintCard>
  }

  const top15 = sorted.slice(0, 15).map((r) => ({ item: r.item, score: r.score }))

  return (
    <Stack>
      <DataTable data={sorted} columns={columns} maxHeight={480} />
      <Title order={6} c="dimmed" tt="uppercase" mt="lg">Score · Top 15</Title>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={top15} layout="vertical" margin={{ left: 120 }}>
          <XAxis type="number" stroke={COLORS.textDim} />
          <YAxis type="category" dataKey="item" width={200} stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <Tooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }} />
          <Bar dataKey="score" fill={COLORS.warn} />
        </BarChart>
      </ResponsiveContainer>
    </Stack>
  )
}
