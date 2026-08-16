import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Stack } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { OwnedBlueprintRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

export default function Blueprints() {
  const { data, isLoading } = useQuery({ queryKey: ['production', 'blueprints'], queryFn: productionApi.ownedBlueprints })

  const columns = useMemo<ColumnDef<OwnedBlueprintRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260 },
    {
      header: 'Type', accessorKey: 'is_original', size: 100,
      cell: (i) => <Badge color={i.getValue() ? 'accent' : 'info'} variant="light">{i.getValue() ? 'BPO' : 'BPC'}</Badge>,
    },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'ME', accessorKey: 'material_efficiency', size: 80 },
    { header: 'TE', accessorKey: 'time_efficiency', size: 80 },
    { header: 'Runs', accessorKey: 'runs', size: 100, cell: (i) => (i.getValue() === null ? '∞' : qty(i.getValue())) },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (!data || data.length === 0) {
    return <HintCard>No blueprints found - or not synced yet ('Sync ESI Data' in the sidebar).</HintCard>
  }

  return (
    <Stack>
      <DataTable data={data} columns={columns} maxHeight={560} />
    </Stack>
  )
}
