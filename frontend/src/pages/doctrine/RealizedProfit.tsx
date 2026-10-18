import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Group, SegmentedControl, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type {
  DoctrineRealizedContract, DoctrineRealizedFitting, DoctrineRealizedLine,
} from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { JobProgress } from '../../components/JobProgress'
import { isk, pct, qty } from '../../format'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'
import { COLORS } from '../../theme'
import { CostChainTree } from '../production/realized/CostChainTree'
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

function originLabel(kind: string, location: string | null): string {
  if (kind === 'produced') return 'Own production'
  if (kind === 'market') return location ? `Market · ${location}` : 'Market purchases'
  if (kind === 'contract') return 'Contract purchases'
  if (kind === 'opening') return 'Opening stock'
  if (kind === 'unknown') return 'Unknown stock'
  if (kind === 'broker_fees') return 'Broker fees'
  return kind
}

export default function DoctrineRealizedProfit() {
  const [preset, setPreset] = useState('30')
  const [saleRef, setSaleRef] = useState<string | null>(null)
  const period = periodFromPreset(preset)
  const periodQuery = realizedPeriodSearch(period)

  const summaryQuery = useQuery({
    queryKey: ['doctrine', 'realized', 'summary', periodQuery],
    queryFn: () => doctrineApi.realizedSummary(periodQuery),
  })
  const fittingsQuery = useQuery({
    queryKey: ['doctrine', 'realized', 'fittings', periodQuery],
    queryFn: () => doctrineApi.realizedFittings(periodQuery),
  })
  const contractsQuery = useQuery({
    queryKey: ['doctrine', 'realized', 'contracts', periodQuery],
    queryFn: () => doctrineApi.realizedContracts(periodQuery),
  })
  const detailQuery = useQuery({
    queryKey: ['doctrine', 'realized', 'contract', saleRef],
    queryFn: () => doctrineApi.realizedContract(saleRef as string),
    enabled: saleRef != null,
  })

  const job = useBackgroundJob({
    queryKey: ['doctrine', 'realized', 'rebuild'],
    fetchStatus: doctrineApi.realizedRebuildStatus,
    resultKeys: [['doctrine', 'realized']],
    labels: { rebuild_realized: 'Recompute' },
    defaultLabel: 'Recompute',
  })
  const rebuild = useBackgroundJobStart(job, () => doctrineApi.startRealizedRebuild())

  const summary = summaryQuery.data
  const waiting = !summary || summary.status.empty || summary.status.needs_rebuild
  const weekly = periodLongerThan90Days(period, summary?.status.cutover_t0)
  const points = profitSeries(summary?.by_day ?? [], weekly)

  const fittingColumns = useMemo<ColumnDef<DoctrineRealizedFitting, unknown>[]>(() => [
    {
      header: 'Fitting',
      accessorKey: 'fitting_name',
      size: 220,
      cell: (info) => info.getValue() ?? 'Unmatched',
    },
    { header: 'Contracts', accessorKey: 'contracts', size: 100 },
    { header: 'Avg price', accessorKey: 'avg_price', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Avg cost', accessorKey: 'avg_cost', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Avg profit', accessorKey: 'avg_profit', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Produced', accessorKey: 'produced_share', size: 110, cell: (info) => rate(info.getValue() as number | null) },
    { header: 'Bought', accessorKey: 'bought_share', size: 110, cell: (info) => rate(info.getValue() as number | null) },
  ], [])

  const contractColumns = useMemo<ColumnDef<DoctrineRealizedContract, unknown>[]>(() => [
    { header: 'Date', accessorKey: 'sale_date', size: 160, cell: (info) => String(info.getValue() ?? '–').slice(0, 16) },
    { header: 'Fitting', accessorKey: 'fitting_name', size: 180, cell: (info) => info.getValue() ?? 'Unmatched' },
    { header: 'Buyer', accessorKey: 'buyer_name', size: 160, cell: (info) => info.getValue() ?? '–' },
    { header: 'Price', accessorKey: 'price', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Cost', accessorKey: 'cost', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Profit', accessorKey: 'profit', size: 120, cell: (info) => money(info.getValue() as number | null) },
    {
      header: 'Production saving',
      accessorKey: 'production_saving',
      size: 150,
      cell: (info) => money(info.getValue() as number | null),
    },
  ], [])

  const lineColumns = useMemo<ColumnDef<DoctrineRealizedLine, unknown>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 200, cell: (info) => info.getValue() ?? '–' },
    { header: 'Qty', accessorKey: 'quantity', size: 80, cell: (info) => qty(Number(info.getValue())) },
    { header: 'Origin', accessorKey: 'origin', size: 120 },
    { header: 'Unit cost', accessorKey: 'unit_cost', size: 120, cell: (info) => money(info.getValue() as number | null) },
    { header: 'Cost', accessorKey: 'cost', size: 120, cell: (info) => isk(Number(info.getValue())) },
  ], [])

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={3}>Realized profit</Title>
        <Button onClick={() => rebuild.mutate()} loading={rebuild.isPending || job.runningStatus}>Recompute</Button>
      </Group>
      <JobProgress label={job.formatProgress(job.status?.progress, job.jobName)} progress={job.status?.progress} />
      <SegmentedControl
        value={preset}
        onChange={(value) => { setPreset(value); setSaleRef(null) }}
        data={PERIOD_OPTIONS}
      />

      {waiting ? (
        <HintCard>{summary?.status.hint || 'Recompute to build realized profit from the archive.'}</HintCard>
      ) : (
        <>
          <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Contracts</Text>
              <Title order={3}>{summary.contracts}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Revenue</Text>
              <Title order={3}>{isk(summary.revenue)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Realized profit</Text>
              <Title order={3}>{isk(summary.realized_profit)}</Title>
              <CoverageBadge coverage={summary.coverage} />
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Margin</Text>
              <Title order={3}>{rate(summary.margin)}</Title>
              <Text size="xs" c="dimmed">Production saving {money(summary.production_saving)}</Text>
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

          <Title order={6} c="dimmed" tt="uppercase">Cost by origin</Title>
          <Stack gap={2}>
            {summary.cost_by_origin.map((row) => (
              <Text key={`${row.kind}-${row.location_id ?? ''}`} size="sm">
                {originLabel(row.kind, row.location_name)}: {isk(row.cost)}
              </Text>
            ))}
          </Stack>

          <Title order={6} c="dimmed" tt="uppercase">
            {weekly ? 'Realized profit per week' : 'Realized profit per day'}
          </Title>
          {points.length === 0 ? <HintCard>No contracts in this period.</HintCard> : (
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
        <>
          <Title order={6} c="dimmed" tt="uppercase">Per fitting</Title>
          <DataTable
            tableId="doctrine-realized-fittings"
            data={fittingsQuery.data?.rows ?? []}
            columns={fittingColumns}
            isLoading={fittingsQuery.isLoading}
            isError={fittingsQuery.isError}
            onRetry={() => { void fittingsQuery.refetch() }}
            rowDetail={false}
            maxHeight={320}
            dataUpdatedAt={fittingsQuery.dataUpdatedAt}
          />
          <Title order={6} c="dimmed" tt="uppercase">Contracts</Title>
          <DataTable
            tableId="doctrine-realized-contracts"
            data={contractsQuery.data?.rows ?? []}
            columns={contractColumns}
            isLoading={contractsQuery.isLoading}
            isError={contractsQuery.isError}
            onRetry={() => { void contractsQuery.refetch() }}
            onRowClick={(row) => { if (row.sale_ref) setSaleRef(row.sale_ref) }}
            rowDetail={false}
            maxHeight={360}
            dataUpdatedAt={contractsQuery.dataUpdatedAt}
          />
        </>
      )}

      {(contractsQuery.data?.before_archive.length ?? 0) > 0 && (
        <Stack gap="xs">
          <Title order={6} c="dimmed" tt="uppercase">Before the archive</Title>
          <HintCard>These finished contracts have no cost in the ledger, so they are not realized profit.</HintCard>
          <DataTable
            tableId="doctrine-realized-before"
            data={contractsQuery.data?.before_archive ?? []}
            columns={contractColumns}
            rowDetail={false}
            maxHeight={240}
          />
        </Stack>
      )}

      {saleRef != null && detailQuery.data && (
        <Stack gap="xs">
          <Title order={6}>
            {detailQuery.data.fitting_name ?? 'Unmatched'} · {detailQuery.data.buyer_name ?? '–'}
          </Title>
          <Text size="sm">Production saving {money(detailQuery.data.production_saving)}</Text>
          <DataTable
            tableId="doctrine-realized-lines"
            data={detailQuery.data.lines}
            columns={lineColumns}
            rowDetail={false}
            maxHeight={280}
          />
          {detailQuery.data.lines.filter((line) => line.chain.length > 0).map((line) => (
            <Stack key={line.type_id} gap={4}>
              <Text size="sm" fw={600}>{line.type_name ?? `Type ${line.type_id}`} production chain</Text>
              {line.chain.map((node, index) => (
                <CostChainTree key={`${line.type_id}-${index}`} node={node} />
              ))}
            </Stack>
          ))}
        </Stack>
      )}
    </Stack>
  )
}
