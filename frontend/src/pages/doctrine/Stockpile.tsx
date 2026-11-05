import { useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Stack, Title, Badge, Text, Divider, Group, Select, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { AggregatedStockpileRow, StockpileRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty, pct } from '../../format'
import { ReserveHint } from './ReserveHint'

const SEVERITY_COLOR: Record<string, string> = { critical: 'danger', tolerable: 'warn' }

// Stock of an equivalent variant (same stats, Equivalents tab) counts too.
function availableCell(r: { available: number; variant_available?: number; variant_names?: string[] }) {
  const variants = r.variant_available ?? 0
  if (variants <= 0) return qty(r.available)
  return (
    <Tooltip label={`Includes ${qty(variants)} of ${(r.variant_names ?? []).join(', ')}`} multiline w={280}>
      <span>{qty(r.available)} <Text span size="xs" c="dimmed">({qty(variants)} variant)</Text></span>
    </Tooltip>
  )
}

function severityBadge(value: unknown) {
  const severity = value as string | null
  return severity ? <Badge size="xs" color={SEVERITY_COLOR[severity] ?? 'dimmed'} variant="light">{severity}</Badge> : null
}

function stockPctColumn<T extends { required_total: number; available: number }>(): ColumnDef<T, any> {
  return {
    header: 'Stock %', id: 'stock_pct', size: 90,
    accessorFn: (r) => (r.required_total > 0 ? r.available / r.required_total : null),
    cell: (i) => pct(i.getValue()),
  }
}

export default function Stockpile() {
  const [locationId, setLocationId] = useState<number | null>(null)
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['doctrine', 'stockpile', locationId],
    queryFn: () => doctrineApi.stockpile(undefined, locationId ?? undefined),
    placeholderData: keepPreviousData,
  })
  const locationOptions = (data?.locations ?? []).map((o) => ({
    value: String(o.location_id), label: o.name ?? String(o.location_id),
  }))

  const rows = data?.rows ?? []
  const aggregatedRows = data?.aggregated_rows ?? []
  const byDoctrine: Record<string, typeof rows> = {}
  for (const r of rows) {
    (byDoctrine[r.doctrine_id] ??= []).push(r)
  }

  const aggregatedColumns = useMemo<ColumnDef<AggregatedStockpileRow, any>[]>(() => [
    { header: 'Type', accessorKey: 'type_name', size: 220, meta: { copyable: true } },
    { header: 'Required', accessorKey: 'required_total', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Available', accessorKey: 'available', size: 130, cell: (i) => availableCell(i.row.original) },
    stockPctColumn(),
    {
      header: 'Shortfall', accessorKey: 'shortfall', size: 110,
      cell: (i) => (i.getValue() > 0 ? qty(i.getValue()) : '–'),
    },
    {
      header: 'Reserve', accessorKey: 'reserve_on_hand', size: 120, cell: (i) => <ReserveHint value={i.getValue()} />,
    },
    {
      header: 'Used in', accessorKey: 'fitting_count', size: 130,
      cell: (i) => `${i.getValue()} fitting${i.getValue() === 1 ? '' : 's'}`,
    },
    { header: 'Severity', accessorKey: 'severity', size: 110, cell: (i) => severityBadge(i.getValue()) },
  ], [])

  const doctrineColumns = useMemo<ColumnDef<StockpileRow, any>[]>(() => [
    { header: 'Fitting', accessorKey: 'fitting_name', size: 200 },
    { header: 'Type', accessorKey: 'type_name', size: 200, meta: { copyable: true } },
    { header: 'Section', accessorKey: 'slot_section', size: 110 },
    { header: 'Required', accessorKey: 'required_total', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Available', accessorKey: 'available', size: 130, cell: (i) => availableCell(i.row.original) },
    stockPctColumn(),
    {
      header: 'Shortfall', accessorKey: 'shortfall', size: 110,
      cell: (i) => (i.getValue() > 0 ? qty(i.getValue()) : '–'),
    },
    { header: 'Reserve', accessorKey: 'reserve_on_hand', size: 120, cell: (i) => <ReserveHint value={i.getValue()} /> },
    { header: 'Severity', accessorKey: 'severity', size: 110, cell: (i) => severityBadge(i.getValue()) },
  ], [])

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>Stockpile</Title>
        {locationOptions.length > 1 && (
          <Select placeholder="All stockpile locations" data={locationOptions} clearable w={320}
            value={locationId != null ? String(locationId) : null}
            onChange={(v) => setLocationId(v ? Number(v) : null)} />
        )}
      </Group>

      {locationId != null && (
        <HintCard>Required is the whole need of every fitting; Available counts only the stock at this location.</HintCard>
      )}

      {isError && (
        <DataTable
          rowDetail
          data={[]}
          columns={aggregatedColumns}
          isError
          onRetry={() => refetch()}
        />
      )}

      {!isLoading && data && !data.assets_available && (
        <HintCard>No asset data yet - share Assets with Doctrine on the Characters page and run <b>Refresh what I need</b>.</HintCard>
      )}

      {!isLoading && data?.assets_available && rows.length === 0 && (
        <Text c="dimmed">No stockpile targets configured (every fitting's stockpile target is 0).</Text>
      )}

      {(isLoading || aggregatedRows.length > 0) && (
        <div>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">
            Combined Shortfall (across every doctrine/fitting)
          </Title>
          <DataTable
            rowDetail
            data={aggregatedRows}
            columns={aggregatedColumns}
            tableId="doctrine-stockpile-aggregated"
            exportFilename="doctrine-stockpile-aggregated"
            getRowId={(r) => String(r.type_id)}
            isLoading={isLoading}
            dataUpdatedAt={dataUpdatedAt}
          />
        </div>
      )}

      {rows.length > 0 && <Divider label="By doctrine / fitting" labelPosition="left" />}

      {Object.entries(byDoctrine).map(([doctrineId, doctrineRows]) => (
        <div key={doctrineId}>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">{doctrineRows[0]?.doctrine_name}</Title>
          <DataTable
            rowDetail
            data={doctrineRows}
            columns={doctrineColumns}
            tableId={`doctrine-stockpile-${doctrineId}`}
            exportFilename={`doctrine-stockpile-${doctrineRows[0]?.doctrine_name ?? doctrineId}`}
            getRowId={(r) => `${r.fitting_id}-${r.type_id}`}
          />
        </div>
      ))}
    </Stack>
  )
}
