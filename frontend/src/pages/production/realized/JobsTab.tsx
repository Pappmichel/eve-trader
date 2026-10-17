import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Button, Group, SegmentedControl, Stack, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../../api/client'
import type { RealizedJobGroup, RealizedJobRow, RealizedJobTotals } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { HintCard } from '../../../components/HintCard'
import { dateTime, isk, pct, qty } from '../../../format'
import { FlagMarkers } from './flags'
import { INVENTION_ACTIVITY_ID, activityLabel, displayQty } from './labels'
import { jobsSearch, type JobState } from './period'

type JobView = 'groups' | 'jobs'

function when(iso: string | null): string {
  return iso ? dateTime(iso) : '–'
}

function money(value: number | null | undefined): string {
  return value == null ? '–' : isk(value)
}

const jobColumns: ColumnDef<RealizedJobRow, any>[] = [
  {
    header: 'Product', accessorKey: 'product_name', size: 200,
    cell: (info) => (
      <>
        {info.getValue() || info.row.original.blueprint_name || `Type ${info.row.original.product_type_id ?? info.row.original.blueprint_type_id}`}
        {info.row.original.contract_input && <Badge size="xs" variant="light" ml={6}>contract</Badge>}
      </>
    ),
  },
  {
    header: 'Owner', id: 'owner', size: 150,
    accessorFn: (row) => row.owner_name ?? `${row.owner_type} ${row.owner_id}`,
  },
  {
    // Filter and sort by the label the cell shows ("Invention"), not the id.
    header: 'Activity', id: 'activity', size: 130,
    accessorFn: (row) => activityLabel(row.activity_id),
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
  { header: 'Unit cost', accessorKey: 'unit_cost', size: 120, cell: (info) => money(info.getValue()) },
  { header: 'Planned unit cost', accessorKey: 'planned_unit_cost', size: 150, cell: (info) => money(info.getValue()) },
  {
    header: 'Cost / BPC run', accessorKey: 'cost_per_bpc_run', size: 140,
    cell: (info) => money(info.getValue()),
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

const groupColumns: ColumnDef<RealizedJobGroup, any>[] = [
  {
    header: 'Product', accessorKey: 'type_name', size: 220,
    cell: (info) => info.getValue() || `Type ${info.row.original.type_id}`,
  },
  { header: 'Activity', id: 'activity', size: 130, accessorFn: (row) => activityLabel(row.activity_id) },
  { header: 'Jobs', accessorKey: 'jobs', size: 80, cell: (info) => qty(info.getValue()) },
  {
    header: 'Runs', accessorKey: 'runs', size: 150,
    cell: (info) => {
      const row = info.row.original
      return row.activity_id === INVENTION_ACTIVITY_ID
        ? `${qty(row.runs)} attempts · ${qty(row.successful_runs)} ok`
        : qty(row.runs)
    },
  },
  {
    header: 'Output', accessorKey: 'output_quantity', size: 130,
    cell: (info) => `${displayQty(info.getValue())}${info.row.original.output_unit === 'bpc_run' ? ' runs' : ''}`,
  },
  { header: 'Unit cost', accessorKey: 'unit_cost', size: 130, cell: (info) => money(info.getValue()) },
  { header: 'Planned', accessorKey: 'planned_unit_cost', size: 130, cell: (info) => money(info.getValue()) },
  {
    header: 'Difference', accessorKey: 'planned_difference_per_unit', size: 120,
    cell: (info) => money(info.getValue()),
  },
  { header: 'Cost / BPC run', accessorKey: 'cost_per_bpc_run', size: 140, cell: (info) => money(info.getValue()) },
  { header: 'Total cost', accessorKey: 'total_cost', size: 140, cell: (info) => isk(info.getValue()) },
  { header: 'Job cost', accessorKey: 'job_cost', size: 120, cell: (info) => isk(info.getValue()) },
  {
    header: 'Share sold', accessorKey: 'sold_share', size: 110,
    cell: (info) => info.getValue() == null ? '–' : pct(info.getValue()),
  },
  {
    header: 'Notes', id: 'notes', size: 220,
    accessorFn: (row) => [
      row.running ? `${row.running} running` : '',
      row.failed ? `${row.failed} failed` : '',
      row.cancelled ? `${row.cancelled} cancelled` : '',
    ].filter(Boolean).join(', '),
    cell: (info) => {
      const row = info.row.original
      return (
        <Group gap={4} wrap="wrap">
          {info.getValue() && <Text size="xs" c="dimmed">{info.getValue()}</Text>}
          {row.estimated && <Badge size="xs" color="warn" variant="outline">estimated</Badge>}
          {row.assumed && <Badge size="xs" color="warn" variant="outline">assumed ME/decryptor</Badge>}
        </Group>
      )
    },
  },
]

function TotalsLine({ totals }: { totals: RealizedJobTotals }) {
  const extra = [
    totals.running ? `${qty(totals.running)} running` : '',
    totals.failed ? `${qty(totals.failed)} failed` : '',
    totals.cancelled ? `${qty(totals.cancelled)} cancelled` : '',
  ].filter(Boolean).join(' · ')
  return (
    <Text size="sm" c="dimmed">
      {qty(totals.jobs)} jobs · Total cost {isk(totals.total_cost)} · Job cost {isk(totals.job_cost)}
      {extra ? ` · ${extra}` : ''}
    </Text>
  )
}

export function JobsTab({ periodQuery, hint, jobRef }: {
  periodQuery: string
  hint: string
  jobRef: string | null
}) {
  const [state, setState] = useState<JobState>('finished')
  const [view, setView] = useState<JobView>('groups')
  const [product, setProduct] = useState<RealizedJobGroup | null>(null)
  const focused = jobRef != null
  const search = jobsSearch(periodQuery, state, focused)
  const query = useQuery({
    queryKey: ['production', 'realized', 'jobs', search],
    queryFn: () => productionApi.realizedJobs(search),
  })
  if (query.data?.needs_rebuild) return <HintCard>{hint}</HintCard>
  const rows = query.data?.rows ?? []
  const groups = query.data?.groups ?? []
  const shownView: JobView = focused || product ? 'jobs' : view
  let jobs = rows
  if (focused) jobs = rows.filter((row) => row.job_ref === jobRef || String(row.job_id) === jobRef)
  else if (product) {
    jobs = rows.filter((row) => row.activity_id === product.activity_id
      && (row.product_type_id ?? row.blueprint_type_id) === product.type_id)
  }
  const tableState = {
    isLoading: query.isLoading, isError: query.isError,
    onRetry: () => { void query.refetch() }, dataUpdatedAt: query.dataUpdatedAt,
  }

  return (
    <Stack gap="sm">
      {!focused && (
        <Group justify="space-between" align="center">
          <Group gap="sm">
            <SegmentedControl size="xs" value={state} onChange={(value) => setState(value as JobState)} data={[
              { value: 'finished', label: 'Finished in period' },
              { value: 'running', label: 'Running' },
              { value: 'all', label: 'All' },
            ]} />
            {!product && (
              <SegmentedControl size="xs" value={view} onChange={(value) => setView(value as JobView)} data={[
                { value: 'groups', label: 'By product' },
                { value: 'jobs', label: 'Jobs' },
              ]} />
            )}
          </Group>
          {query.data?.totals && <TotalsLine totals={query.data.totals} />}
        </Group>
      )}
      {product && (
        <Group gap="xs">
          <Badge variant="light" color="info">
            {product.type_name || `Type ${product.type_id}`} · {activityLabel(product.activity_id)}
          </Badge>
          <Button size="compact-xs" variant="subtle" onClick={() => setProduct(null)}>Back to products</Button>
        </Group>
      )}
      {shownView === 'groups' ? (
        <DataTable
          tableId="production-realized-job-groups"
          data={groups}
          columns={groupColumns}
          maxHeight={560}
          {...tableState}
          getRowId={(row) => `${row.activity_id}:${row.type_id}`}
          onRowClick={(row) => setProduct(row)}
          rowDetail={false}
        />
      ) : (
        <DataTable
          tableId="production-realized-jobs"
          data={jobs}
          columns={jobColumns}
          maxHeight={560}
          {...tableState}
          getRowId={(row) => row.job_ref}
        />
      )}
    </Stack>
  )
}
