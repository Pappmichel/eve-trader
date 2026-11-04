import { useQuery } from '@tanstack/react-query'
import { Badge, Stack, Text, Title } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../../api/client'
import type { RealizedReprocessRow } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { dateTime, isk } from '../../../format'
import { FlagMarkers } from './flags'
import { displayQty } from './labels'

function outputsLabel(row: RealizedReprocessRow): string {
  if (row.outputs.length === 0) return '–'
  return row.outputs
    .map((out) => {
      const name = out.type_name || `Type ${out.type_id}`
      const cost = out.unit_cost == null ? '' : ` @ ${isk(out.unit_cost)}`
      return `${name} ${displayQty(out.quantity)}${cost}`
    })
    .join(' · ')
}

const columns: ColumnDef<RealizedReprocessRow, any>[] = [
  { header: 'Date', accessorKey: 'date', size: 150, cell: (info) => (info.getValue() ? dateTime(info.getValue()) : '–') },
  {
    header: 'Kind', accessorKey: 'kind', size: 110,
    cell: (info) => (
      <Badge size="xs" variant="light" color={info.getValue() === 'compress' ? 'gray' : 'blue'}>
        {info.getValue() === 'compress' ? 'compressed' : 'reprocessed'}
      </Badge>
    ),
  },
  {
    header: 'Input', accessorKey: 'type_name', size: 200,
    cell: (info) => info.getValue() || `Type ${info.row.original.type_id}`,
  },
  { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (info) => displayQty(info.getValue()) },
  { header: 'Cost', accessorKey: 'cost', size: 140, cell: (info) => isk(info.getValue()) },
  { header: 'Cost/unit', accessorKey: 'cost_per_unit', size: 120, cell: (info) => isk(info.getValue()) },
  { header: 'Tax', accessorKey: 'tax', size: 110, cell: (info) => isk(info.getValue()) },
  { header: 'Outputs', id: 'outputs', size: 360, accessorFn: outputsLabel },
  {
    header: 'Flags', id: 'flags', size: 160,
    accessorFn: (row) => JSON.stringify(row.flags),
    cell: (info) => <FlagMarkers flags={info.row.original.flags} />,
  },
]

/** Reprocessing the latest rebuild detected from stock observations. */
export function ReprocessingSection() {
  const query = useQuery({
    queryKey: ['production', 'realized', 'reprocessing'],
    queryFn: productionApi.realizedReprocessing,
  })
  if (query.data?.needs_rebuild) return null
  const since = query.data?.observed_since
  return (
    <Stack gap="xs" mt="lg" data-section="reprocessing">
      <Title order={6} c="dimmed" tt="uppercase">Reprocessing</Title>
      <Text size="sm" c="dimmed">
        Ore, ice and Module Reprocessing shortlist items that disappear between two asset syncs count as
        reprocessed (compressed, if the compressed ore appears instead). Their cost moves to the output
        minerals by market value. Output quantities use the yield from the Ore &amp; Minerals and Module
        Reprocessing settings; a different real yield shows up as inventory drift.
      </Text>
      <Text size="sm" c="dimmed" data-observed-since={since ?? ''}>
        {since
          ? `Stock observed since ${dateTime(since)}.`
          : 'No stock observation yet: detection starts with the next asset sync.'}
      </Text>
      <DataTable
        tableId="production-realized-reprocessing"
        data={query.data?.rows ?? []}
        columns={columns}
        maxHeight={420}
        isLoading={query.isLoading}
        isError={query.isError}
        onRetry={() => { void query.refetch() }}
        dataUpdatedAt={query.dataUpdatedAt}
        getRowId={(row) => row.ref}
      />
    </Stack>
  )
}
