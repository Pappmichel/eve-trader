import type { ColumnDef } from '@tanstack/react-table'

import type { RealizedJobRow } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { HintCard } from '../../../components/HintCard'
import { dateTime, isk, pct, qty } from '../../../format'
import { FlagMarkers } from './flags'
import { INVENTION_ACTIVITY_ID, activityLabel } from './labels'

function when(iso: string | null): string {
  return iso ? dateTime(iso) : '–'
}

const columns: ColumnDef<RealizedJobRow, any>[] = [
  {
    header: 'Product', accessorKey: 'product_name', size: 200,
    cell: (info) => info.getValue() || info.row.original.blueprint_name || `Type ${info.row.original.product_type_id ?? info.row.original.blueprint_type_id}`,
  },
  {
    header: 'Activity', accessorKey: 'activity_id', size: 130,
    cell: (info) => activityLabel(info.getValue()),
  },
  {
    header: 'Runs', accessorKey: 'runs', size: 120,
    cell: (info) => {
      const row = info.row.original
      return row.activity_id === INVENTION_ACTIVITY_ID ? `${qty(row.runs)} attempts` : qty(row.runs)
    },
  },
  {
    header: 'Successful runs', accessorKey: 'successful_runs', size: 140,
    cell: (info) => info.getValue() == null ? '–' : qty(info.getValue()),
  },
  { header: 'Start', accessorKey: 'start_date', size: 150, cell: (info) => when(info.getValue()) },
  { header: 'End', accessorKey: 'end_date', size: 150, cell: (info) => when(info.getValue()) },
  { header: 'State', accessorKey: 'state', size: 110 },
  { header: 'Input cost', accessorKey: 'input_cost', size: 130, cell: (info) => isk(info.getValue()) },
  { header: 'Job cost', accessorKey: 'job_cost', size: 120, cell: (info) => isk(info.getValue()) },
  { header: 'Unit cost', accessorKey: 'unit_cost', size: 120, cell: (info) => isk(info.getValue()) },
  {
    header: 'Cost / BPC run', accessorKey: 'cost_per_bpc_run', size: 140,
    cell: (info) => isk(info.getValue()),
  },
  {
    header: 'Share sold', accessorKey: 'sold_share', size: 110,
    cell: (info) => info.getValue() == null ? '–' : pct(info.getValue()),
  },
  {
    header: 'Flags', id: 'flags', size: 260,
    accessorFn: (row) => JSON.stringify(row.flags),
    cell: (info) => <FlagMarkers flags={info.row.original.flags} job />,
  },
]

export function JobsTab({ rows, needsRebuild, hint, jobRef, isLoading, isError, onRetry, updatedAt }: {
  rows: RealizedJobRow[]
  needsRebuild: boolean
  hint: string
  jobRef: string | null
  isLoading: boolean
  isError: boolean
  onRetry: () => void
  updatedAt?: number
}) {
  if (needsRebuild) return <HintCard>{hint}</HintCard>
  const data = jobRef == null ? rows : rows.filter((row) => row.job_ref === jobRef || String(row.job_id) === jobRef)
  return (
    <DataTable
      tableId="production-realized-jobs"
      data={data}
      columns={columns}
      maxHeight={560}
      isLoading={isLoading}
      isError={isError}
      onRetry={onRetry}
      dataUpdatedAt={updatedAt}
      getRowId={(row) => row.job_ref}
    />
  )
}
