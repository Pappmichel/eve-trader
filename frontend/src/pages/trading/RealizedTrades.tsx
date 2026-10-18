import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Group, SegmentedControl, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { TradingRealizedItem, TradingRealizedLot, TradingRealizedSale } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { JobProgress } from '../../components/JobProgress'
import { isk, pct, qty } from '../../format'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'
import { COLORS } from '../../theme'
import { compactIsk, profitSeries } from '../production/realized/chart'
import { CoverageBadge } from '../production/realized/flags'
import { periodLongerThan90Days, realizedPeriodSearch, type RealizedPeriod } from '../production/realized/period'

const PERIOD_OPTIONS = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: 'since_t0', label: 'Since T0' },
]

function money(value: number | null | undefined): string {
  return value == null ? '–' : isk(value)
}

function rate(value: number | null | undefined): string {
  return value == null ? '–' : pct(value)
}

function periodFromPreset(preset: string): RealizedPeriod {
  if (preset === 'since_t0') return { mode: 'since_t0' }
  return { mode: 'days', days: Number(preset) as 7 | 30 | 90 }
}

export default function RealizedTrades() {
  const [preset, setPreset] = useState('30')
  const [typeId, setTypeId] = useState<number | null>(null)
  const [saleRef, setSaleRef] = useState<string | null>(null)
  const period = periodFromPreset(preset)
  const periodQuery = realizedPeriodSearch(period)

  const summaryQuery = useQuery({
    queryKey: ['trading', 'realized', 'summary', periodQuery],
    queryFn: () => tradingApi.realizedSummary(periodQuery),
  })
  const itemsQuery = useQuery({
    queryKey: ['trading', 'realized', 'items', periodQuery],
    queryFn: () => tradingApi.realizedItems(periodQuery),
  })
  const inventoryQuery = useQuery({
    queryKey: ['trading', 'realized', 'inventory'],
    queryFn: tradingApi.realizedInventory,
  })
  const warningsQuery = useQuery({
    queryKey: ['trading', 'realized', 'warnings'],
    queryFn: tradingApi.realizedWarnings,
  })
  const detailQuery = useQuery({
    queryKey: ['trading', 'realized', 'item', typeId, periodQuery],
    queryFn: () => tradingApi.realizedItem(typeId as number, periodQuery),
    enabled: typeId != null,
  })
  const saleQuery = useQuery({
    queryKey: ['trading', 'realized', 'sale', saleRef],
    queryFn: () => tradingApi.realizedSale(saleRef as string),
    enabled: saleRef != null,
  })

  const job = useBackgroundJob({
    queryKey: ['trading', 'realized', 'rebuild'],
    fetchStatus: tradingApi.realizedRebuildStatus,
    resultKeys: [['trading', 'realized']],
    labels: { rebuild_realized: 'Recompute' },
    defaultLabel: 'Recompute',
  })
  const rebuild = useBackgroundJobStart(job, () => tradingApi.startRealizedRebuild())

  const summary = summaryQuery.data
  const waiting = !summary || summary.status.empty || summary.status.needs_rebuild
  const weekly = periodLongerThan90Days(period, summary?.status.cutover_t0)
  const points = profitSeries(summary?.by_day ?? [], weekly)

  const itemColumns = useMemo<ColumnDef<TradingRealizedItem, unknown>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220, cell: (info) => info.getValue() ?? '–' },
    { header: 'Units', accessorKey: 'units_sold', size: 100, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Avg buy landed', accessorKey: 'avg_buy_landed', size: 140, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Avg net sell', accessorKey: 'avg_net_sell', size: 140, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Profit', accessorKey: 'profit', size: 140, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Margin', accessorKey: 'margin', size: 110, cell: (info) => rate(info.getValue() as number | null) },
  ], [])

  const saleColumns = useMemo<ColumnDef<TradingRealizedSale, unknown>[]>(() => [
    { header: 'Date', accessorKey: 'sale_date', size: 180, cell: (info) => String(info.getValue() ?? '–').slice(0, 16) },
    { header: 'Location', accessorKey: 'location_name', size: 180, cell: (info) => info.getValue() ?? '–' },
    { header: 'Qty', accessorKey: 'quantity', size: 80, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Revenue', accessorKey: 'revenue', size: 120, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Cost', accessorKey: 'cost', size: 120, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Profit', accessorKey: 'profit', size: 120, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Margin', accessorKey: 'margin', size: 100, cell: (info) => rate(info.getValue() as number | null) },
  ], [])

  const lotColumns = useMemo<ColumnDef<TradingRealizedLot, unknown>[]>(() => [
    { header: 'Bought', accessorKey: 'layer_date', size: 160, cell: (info) => String(info.getValue() ?? '–').slice(0, 16) },
    { header: 'Where', accessorKey: 'location_name', size: 180, cell: (info) => info.getValue() ?? '–' },
    { header: 'Origin', accessorKey: 'origin', size: 110 },
    { header: 'Qty', accessorKey: 'quantity', size: 80, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Price', accessorKey: 'unit_price', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Freight', accessorKey: 'freight', size: 120, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Fees', accessorKey: 'fees', size: 120, cell: (info) => isk(Number(info.getValue())) },
  ], [])

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <SegmentedControl
          value={preset}
          onChange={(value) => { setPreset(value); setTypeId(null); setSaleRef(null) }}
          data={PERIOD_OPTIONS}
          aria-label="Period"
        />
        <Button onClick={() => rebuild.mutate()} loading={job.runningStatus || rebuild.isPending}>Recompute</Button>
      </Group>
      {job.runningStatus && (
        <JobProgress label={job.formatProgress(job.status?.progress, job.jobName)} progress={job.status?.progress} />
      )}

      {summaryQuery.isLoading && <Text size="sm" c="dimmed">Loading…</Text>}
      {summaryQuery.isError && <HintCard>Failed to load realized profit.</HintCard>}
      {waiting && summary && (
        <HintCard>{summary.status.hint || 'Recompute to build realized profit from the archive.'}</HintCard>
      )}

      {summary && !waiting && (
        <>
          <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }}>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Revenue</Text>
              <Title order={3}>{isk(summary.revenue)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Cost of goods sold</Text>
              <Title order={3}>{isk(summary.cost_of_goods_sold)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Realized profit</Text>
              <Title order={3}>{isk(summary.realized_profit)}</Title>
              <Text size="xs" c="dimmed">{qty(summary.sale_count)} sales · {qty(summary.units_sold)} units</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Margin</Text>
              <Title order={3}>{rate(summary.margin)}</Title>
              <CoverageBadge coverage={summary.coverage} />
            </Card>
          </SimpleGrid>

          <Title order={6} c="dimmed" tt="uppercase">
            {weekly ? 'Realized profit per week' : 'Realized profit per day'}
          </Title>
          {points.length === 0 ? <HintCard>No sales in this period.</HintCard> : (
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={points}>
                <CartesianGrid stroke={COLORS.border} />
                <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
                <YAxis stroke={COLORS.textDim} tick={{ fontSize: 11 }} tickFormatter={(value) => compactIsk(Number(value))} width={64} />
                <Tooltip
                  contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }}
                  formatter={(value, name) => [isk(Number(value)), name === 'cumulative' ? 'Cumulative' : 'Profit']}
                />
                <Bar dataKey="profit" fill={COLORS.accent} name="Profit" />
                <Line type="monotone" dataKey="cumulative" stroke={COLORS.info} dot={false} name="Cumulative" />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </>
      )}

      {!waiting && (
        <DataTable
          tableId="trading-realized-items"
          data={itemsQuery.data?.rows ?? []}
          columns={itemColumns}
          isLoading={itemsQuery.isLoading}
          isError={itemsQuery.isError}
          onRetry={() => { void itemsQuery.refetch() }}
          onRowClick={(row) => { setTypeId(row.type_id); setSaleRef(null) }}
          rowDetail={false}
          maxHeight={420}
          dataUpdatedAt={itemsQuery.dataUpdatedAt}
        />
      )}

      {typeId != null && detailQuery.data && (
        <Stack gap="xs">
          <Title order={6}>{detailQuery.data.item.type_name ?? `Type ${typeId}`}</Title>
          <DataTable
            tableId="trading-realized-sales"
            data={detailQuery.data.sales}
            columns={saleColumns}
            onRowClick={(row) => setSaleRef(row.sale_ref)}
            rowDetail={false}
            maxHeight={280}
          />
        </Stack>
      )}

      {saleRef != null && saleQuery.data && (
        <Stack gap="xs">
          <Title order={6}>Purchase lots</Title>
          <DataTable
            tableId="trading-realized-lots"
            data={saleQuery.data.lots}
            columns={lotColumns}
            rowDetail={false}
            maxHeight={240}
          />
        </Stack>
      )}

      {!waiting && (
        <>
          <Title order={6} c="dimmed" tt="uppercase">Inventory at cost</Title>
          {(inventoryQuery.data?.rows.length ?? 0) === 0
            ? <HintCard>No unsold Trading goods.</HintCard>
            : (
              <Stack gap={4}>
                {inventoryQuery.data?.rows.map((row) => (
                  <Text key={`${row.type_id}-${row.pool}`} size="sm">
                    {row.type_name ?? row.type_id}: {qty(row.quantity)} at {money(row.cost_per_unit)} ({isk(row.total)})
                  </Text>
                ))}
              </Stack>
            )}
          <Title order={6} c="dimmed" tt="uppercase">Warnings</Title>
          {(warningsQuery.data?.rows.length ?? 0) === 0
            ? <Text size="sm" c="dimmed">None</Text>
            : warningsQuery.data?.rows.map((row) => (
              <Text key={`${row.type_id}-${row.message}`} size="sm">
                {row.type_name ?? row.type_id}: {row.message}
              </Text>
            ))}
        </>
      )}
    </Stack>
  )
}
