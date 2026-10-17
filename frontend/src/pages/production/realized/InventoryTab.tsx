import { Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import type { RealizedInventoryRow } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { HintCard } from '../../../components/HintCard'
import { isk } from '../../../format'
import { FlagMarkers } from './flags'
import { displayQty, originMixLabel } from './labels'

const columns: ColumnDef<RealizedInventoryRow, any>[] = [
  {
    header: 'Name', accessorKey: 'type_name', size: 220,
    cell: (info) => info.getValue() || `Type ${info.row.original.type_id}`,
  },
  { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (info) => displayQty(info.getValue()) },
  { header: 'Unit', accessorKey: 'unit', size: 80 },
  { header: 'Cost/unit', accessorKey: 'cost_per_unit', size: 130, cell: (info) => isk(info.getValue()) },
  { header: 'Total', accessorKey: 'total', size: 140, cell: (info) => isk(info.getValue()) },
  {
    header: 'Origin', id: 'origin', size: 280,
    accessorFn: (row) => originMixLabel(row.origin_mix, displayQty),
  },
  {
    header: 'ESI stock', accessorKey: 'esi_stock', size: 110,
    cell: (info) => displayQty(info.getValue()),
  },
  {
    header: 'Drift', accessorKey: 'drift', size: 110,
    cell: (info) => {
      const value = info.getValue() as number | null
      const marked = value != null && value !== 0
      return (
        <Text size="sm" c={marked ? 'danger' : undefined} fw={marked ? 700 : undefined} data-drift={value ?? ''}>
          {displayQty(value)}
        </Text>
      )
    },
  },
  {
    header: 'Flags', id: 'flags', size: 220,
    accessorFn: (row) => JSON.stringify(row.flags),
    cell: (info) => <FlagMarkers flags={info.row.original.flags} />,
  },
]

export function InventoryTab({ rows, needsRebuild, hint, typeId, isLoading, isError, onRetry, updatedAt }: {
  rows: RealizedInventoryRow[]
  needsRebuild: boolean
  hint: string
  typeId: number | null
  isLoading: boolean
  isError: boolean
  onRetry: () => void
  updatedAt?: number
}) {
  if (needsRebuild) return <HintCard>{hint}</HintCard>
  const data = typeId == null ? rows : rows.filter((row) => row.type_id === typeId)
  return (
    <DataTable
      tableId="production-realized-inventory"
      data={data}
      columns={columns}
      maxHeight={560}
      isLoading={isLoading}
      isError={isError}
      onRetry={onRetry}
      dataUpdatedAt={updatedAt}
      getRowId={(row) => `${row.type_id}:${row.unit}`}
    />
  )
}
