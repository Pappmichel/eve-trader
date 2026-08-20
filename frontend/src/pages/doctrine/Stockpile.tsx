import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Badge, Text, Divider } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { AggregatedStockpileRow, StockpileRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

const SEVERITY_COLOR: Record<string, string> = { critical: 'danger', tolerable: 'warn' }

function severityBadge(value: unknown) {
  const severity = value as string | null
  return severity ? <Badge size="xs" color={SEVERITY_COLOR[severity] ?? 'dimmed'} variant="light">{severity}</Badge> : null
}

export default function Stockpile() {
  const { data, isLoading } = useQuery({ queryKey: ['doctrine', 'stockpile'], queryFn: () => doctrineApi.stockpile() })

  const rows = data?.rows ?? []
  const aggregatedRows = data?.aggregated_rows ?? []
  const byDoctrine: Record<string, typeof rows> = {}
  for (const r of rows) {
    (byDoctrine[r.doctrine_id] ??= []).push(r)
  }

  const aggregatedColumns = useMemo<ColumnDef<AggregatedStockpileRow, any>[]>(() => [
    { header: 'Type', accessorKey: 'type_name', size: 220 },
    { header: 'Required', accessorKey: 'required_total', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Available', accessorKey: 'available', size: 110, cell: (i) => qty(i.getValue()) },
    {
      header: 'Shortfall', accessorKey: 'shortfall', size: 110,
      cell: (i) => (i.getValue() > 0 ? qty(i.getValue()) : '–'),
    },
    {
      header: 'Used in', accessorKey: 'fitting_count', size: 130,
      cell: (i) => `${i.getValue()} fitting${i.getValue() === 1 ? '' : 's'}`,
    },
    { header: 'Severity', accessorKey: 'severity', size: 110, cell: (i) => severityBadge(i.getValue()) },
  ], [])

  const doctrineColumns = useMemo<ColumnDef<StockpileRow, any>[]>(() => [
    { header: 'Fitting', accessorKey: 'fitting_name', size: 200 },
    { header: 'Type', accessorKey: 'type_name', size: 200 },
    { header: 'Section', accessorKey: 'slot_section', size: 110 },
    { header: 'Required', accessorKey: 'required_total', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Available', accessorKey: 'available', size: 110, cell: (i) => qty(i.getValue()) },
    {
      header: 'Shortfall', accessorKey: 'shortfall', size: 110,
      cell: (i) => (i.getValue() > 0 ? qty(i.getValue()) : '–'),
    },
    { header: 'Severity', accessorKey: 'severity', size: 110, cell: (i) => severityBadge(i.getValue()) },
  ], [])

  return (
    <Stack>
      <Title order={4}>Stockpile</Title>

      {!isLoading && data && !data.assets_available && (
        <HintCard>
          No asset data available yet - add an asset-scanning character under "Asset-Scanning Characters" in the
          sidebar and run "Sync Assets" to populate stock levels here.
        </HintCard>
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
            data={aggregatedRows}
            columns={aggregatedColumns}
            tableId="doctrine-stockpile-aggregated"
            exportFilename="doctrine-stockpile-aggregated"
            getRowId={(r) => String(r.type_id)}
            isLoading={isLoading}
          />
        </div>
      )}

      {rows.length > 0 && <Divider label="By doctrine / fitting" labelPosition="left" />}

      {Object.entries(byDoctrine).map(([doctrineId, doctrineRows]) => (
        <div key={doctrineId}>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">{doctrineRows[0]?.doctrine_name}</Title>
          <DataTable
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
