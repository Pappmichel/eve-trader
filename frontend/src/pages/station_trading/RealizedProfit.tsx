import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Group, SegmentedControl, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ColumnDef } from '@tanstack/react-table'

import { stationTradingApi } from '../../api/client'
import type {
  StationRealizedFeeDay, StationRealizedItem, StationRealizedLot, StationRealizedPositionRow,
  StationRealizedSale,
} from '../../api/types'
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

function days(value: number | null | undefined): string {
  return value == null ? '–' : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} d`
}

function periodFromPreset(preset: string): RealizedPeriod {
  if (preset === 'since_t0') return { mode: 'since_t0' }
  return { mode: 'days', days: Number(preset) as 7 | 30 | 90 }
}

export default function RealizedProfit() {
  const [preset, setPreset] = useState('30')
  const [typeId, setTypeId] = useState<number | null>(null)
  const [saleRef, setSaleRef] = useState<string | null>(null)
  const period = periodFromPreset(preset)
  const periodQuery = realizedPeriodSearch(period)

  const summaryQuery = useQuery({
    queryKey: ['station-trading', 'realized', 'summary', periodQuery],
    queryFn: () => stationTradingApi.realizedSummary(periodQuery),
  })
  const itemsQuery = useQuery({
    queryKey: ['station-trading', 'realized', 'items', periodQuery],
    queryFn: () => stationTradingApi.realizedItems(periodQuery),
  })
  const positionQuery = useQuery({
    queryKey: ['station-trading', 'realized', 'position'],
    queryFn: stationTradingApi.realizedPosition,
  })
  const detailQuery = useQuery({
    queryKey: ['station-trading', 'realized', 'item', typeId, periodQuery],
    queryFn: () => stationTradingApi.realizedItem(typeId as number, periodQuery),
    enabled: typeId != null,
  })
  const saleQuery = useQuery({
    queryKey: ['station-trading', 'realized', 'sale', saleRef],
    queryFn: () => stationTradingApi.realizedSale(saleRef as string),
    enabled: saleRef != null,
  })

  const job = useBackgroundJob({
    queryKey: ['station-trading', 'realized', 'rebuild'],
    fetchStatus: stationTradingApi.realizedRebuildStatus,
    resultKeys: [['station-trading', 'realized']],
    labels: { rebuild_realized: 'Recompute' },
    defaultLabel: 'Recompute',
  })
  const rebuild = useBackgroundJobStart(job, () => stationTradingApi.startRealizedRebuild())

  const summary = summaryQuery.data
  const waiting = !summary || summary.status.empty || summary.status.needs_rebuild
  const weekly = periodLongerThan90Days(period, summary?.status.cutover_t0)
  const points = profitSeries(summary?.by_day ?? [], weekly)

  const itemColumns = useMemo<ColumnDef<StationRealizedItem, unknown>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 200, cell: (info) => info.getValue() ?? '–' },
    { header: 'Units', accessorKey: 'units_sold', size: 90, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Avg buy', accessorKey: 'avg_buy', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Avg sell net', accessorKey: 'avg_sell_net', size: 130, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Spread', accessorKey: 'spread', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Profit', accessorKey: 'profit', size: 120, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Margin', accessorKey: 'margin', size: 100, cell: (info) => rate(info.getValue() as number | null) },
    { header: 'Turnover', accessorKey: 'turnover_days', size: 110, cell: (info) => days(info.getValue() as number | null) },
  ], [])

  const saleColumns = useMemo<ColumnDef<StationRealizedSale, unknown>[]>(() => [
    { header: 'Date', accessorKey: 'sale_date', size: 170, cell: (info) => String(info.getValue() ?? '–').slice(0, 16) },
    { header: 'Qty', accessorKey: 'quantity', size: 80, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Revenue', accessorKey: 'revenue', size: 120, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Cost', accessorKey: 'cost', size: 120, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Profit', accessorKey: 'profit', size: 120, cell: (info) => isk(Number(info.getValue())) },
  ], [])

  const lotColumns = useMemo<ColumnDef<StationRealizedLot, unknown>[]>(() => [
    { header: 'Bought', accessorKey: 'layer_date', size: 160, cell: (info) => String(info.getValue() ?? '–').slice(0, 16) },
    { header: 'Where', accessorKey: 'location_name', size: 160, cell: (info) => info.getValue() ?? '–' },
    { header: 'Qty', accessorKey: 'quantity', size: 80, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Price', accessorKey: 'unit_price', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Freight', accessorKey: 'freight', size: 110, cell: (info) => isk(Number(info.getValue())) },
  ], [])

  const feeColumns = useMemo<ColumnDef<StationRealizedFeeDay, unknown>[]>(() => [
    { header: 'Day', accessorKey: 'date', size: 140, cell: (info) => String(info.getValue() ?? '–') },
    { header: 'Broker fee', accessorKey: 'broker_fee', size: 130, cell: (info) => isk(Number(info.getValue())) },
    { header: 'Sales tax', accessorKey: 'sales_tax', size: 130, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Total', accessorKey: 'total', size: 130, cell: (info) => isk(Number(info.getValue())) },
  ], [])

  const positionColumns = useMemo<ColumnDef<StationRealizedPositionRow, unknown>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220, cell: (info) => info.getValue() ?? '–' },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Cost / unit', accessorKey: 'cost_per_unit', size: 130, cell: (info) => money(info.getValue() as number | null) },
    { header: 'At cost', accessorKey: 'total', size: 130, cell: (info) => isk(Number(info.getValue())) },
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
              <Text size="xs" c="dimmed" tt="uppercase">Realized profit</Text>
              <Title order={3}>{isk(summary.realized_profit)}</Title>
              <Text size="xs" c="dimmed">{qty(summary.units_sold)} units</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Spread captured</Text>
              <Title order={3}>{money(summary.spread)}</Title>
              <Text size="xs" c="dimmed">Avg sell {money(summary.avg_sell_net)} · avg buy {money(summary.avg_buy)}</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Margin</Text>
              <Title order={3}>{rate(summary.margin)}</Title>
              <CoverageBadge coverage={summary.coverage} />
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Turnover</Text>
              <Title order={3}>{days(summary.turnover_days)}</Title>
              <Text size="xs" c="dimmed">Open position {money(summary.open_position_cost)}</Text>
            </Card>
          </SimpleGrid>
          {(typeof summary.freight_from_couriers === 'number' || typeof summary.freight_configured === 'number') && (
            <Stack gap={2}>
              {typeof summary.freight_from_couriers === 'number' && (
                <Text size="sm">Freight from couriers: {isk(summary.freight_from_couriers)}</Text>
              )}
              {typeof summary.freight_configured === 'number' && (
                <Text size="sm">Freight (configured rate): {isk(summary.freight_configured)}</Text>
              )}
            </Stack>
          )}

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

          <Title order={6} c="dimmed" tt="uppercase">Fees per day</Title>
          <DataTable
            tableId="station-realized-fees"
            data={summary.fees_by_day}
            columns={feeColumns}
            isLoading={false}
            isError={false}
            rowDetail={false}
            maxHeight={240}
          />
        </>
      )}

      {!waiting && (
        <DataTable
          tableId="station-realized-items"
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
            tableId="station-realized-sales"
            data={detailQuery.data.sales}
            columns={saleColumns}
            isLoading={detailQuery.isLoading}
            isError={detailQuery.isError}
            onRowClick={(row) => setSaleRef(row.sale_ref)}
            rowDetail={false}
            maxHeight={280}
          />
        </Stack>
      )}

      {saleRef != null && saleQuery.data && (
        <Stack gap="xs">
          <Title order={6}>Buy lots</Title>
          <DataTable
            tableId="station-realized-lots"
            data={saleQuery.data.lots}
            columns={lotColumns}
            isLoading={saleQuery.isLoading}
            isError={saleQuery.isError}
            rowDetail={false}
            maxHeight={220}
          />
        </Stack>
      )}

      {!waiting && (
        <Stack gap="xs">
          <Title order={6} c="dimmed" tt="uppercase">Open position</Title>
          <DataTable
            tableId="station-realized-position"
            data={positionQuery.data?.rows ?? []}
            columns={positionColumns}
            isLoading={positionQuery.isLoading}
            isError={positionQuery.isError}
            onRetry={() => { void positionQuery.refetch() }}
            rowDetail={false}
            maxHeight={280}
            dataUpdatedAt={positionQuery.dataUpdatedAt}
          />
        </Stack>
      )}
    </Stack>
  )
}
