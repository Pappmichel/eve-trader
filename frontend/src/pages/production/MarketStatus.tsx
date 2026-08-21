import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Text, Stack } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { MarketStatusRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

export default function MarketStatus() {
  const { data, isLoading, dataUpdatedAt } = useQuery({ queryKey: ['production', 'market-status'], queryFn: productionApi.marketStatus })

  const columns = useMemo<ColumnDef<MarketStatusRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    { header: 'Backup Target', accessorKey: 'backup_target', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'Backup Current', accessorKey: 'backup_current', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'Home Market Target', accessorKey: 'home_target', size: 150, cell: (i) => qty(i.getValue()) },
    { header: 'Home Listed', accessorKey: 'home_listed', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Jita Market Target', accessorKey: 'jita_target', size: 140, cell: (i) => qty(i.getValue()) },
    { header: 'Jita Listed', accessorKey: 'jita_listed', size: 110, cell: (i) => qty(i.getValue()) },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (!data || data.length === 0) return <HintCard>No stock targets configured yet.</HintCard>

  return (
    <Stack>
      <DataTable data={data} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      <Text size="xs" c="dimmed">
        'Current'/'listed' come from the last 'Sync ESI Data' run (assets + personal and corp sell orders); without a sync they stay at 0.
      </Text>
    </Stack>
  )
}
