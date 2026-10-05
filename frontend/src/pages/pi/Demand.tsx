import { useQuery } from '@tanstack/react-query'
import { Alert, Badge, Stack } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { ApiError, piApi } from '../../api/client'
import type { PiDemandRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { isk, qty } from '../../format'
import { num } from './common'

const EMPTY: PiDemandRow[] = []

const columns: ColumnDef<PiDemandRow, any>[] = [
  { header: 'Item', accessorKey: 'name', size: 200, meta: { exportRole: 'item' } },
  { header: 'Quantity', accessorKey: 'quantity', size: 100, cell: (i) => qty(i.getValue<number>()), meta: { exportRole: 'qty' } },
  { header: 'Buy price', accessorKey: 'buy_price', size: 110, cell: (i) => isk(i.getValue<number | null>()) },
  { header: 'Buy total', accessorKey: 'buy_total', size: 130, cell: (i) => isk(i.getValue<number | null>()) },
  { header: 'PI unit cost', accessorKey: 'pi_unit_cost', size: 120, cell: (i) => isk(i.getValue<number | null>()) },
  { header: 'Chain', accessorFn: (r) => r.chain ?? '–', size: 90 },
  { header: 'Saving', accessorKey: 'saving', size: 130, cell: (i) => isk(i.getValue<number | null>()) },
  {
    header: 'Colonies needed', accessorKey: 'colonies_needed', size: 120,
    cell: (i) => num(i.getValue<number | null>(), 2),
  },
  {
    header: 'Make via PI', accessorKey: 'make_via_pi', size: 110,
    cell: (i) => (i.getValue<boolean>()
      ? <Badge color="accent" variant="light">Make via PI</Badge>
      : <Badge color="gray" variant="light">Buy</Badge>),
  },
]

export default function Demand() {
  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['pi', 'demand'], queryFn: piApi.productionDemand, retry: false,
  })

  if (error instanceof ApiError && error.status === 403) {
    return (
      <Alert color="warn" title="Production access needed">
        This view reads Production&apos;s buy list, so it needs the Production tool as well.
        Ask an admin to grant it to your character.
      </Alert>
    )
  }

  return (
    <Stack>
      <HintCard>
        PI commodities on Production&apos;s latest buy list: what buying costs against making them on your own planets.
        {data ? ` Colonies needed cover the list within ${data.days} days.` : ''}
      </HintCard>
      <DataTable
        data={data?.rows ?? EMPTY}
        columns={columns}
        isLoading={isLoading}
        isError={isError}
        errorMessage={error instanceof Error ? error.message : undefined}
        onRetry={() => refetch()}
        dataUpdatedAt={dataUpdatedAt}
        maxHeight={600}
        tableId="pi-demand"
        exportFilename="pi-demand"
        emptyLabel="No PI commodities on the buy list"
        getRowId={(r) => String(r.type_id)}
      />
    </Stack>
  )
}
