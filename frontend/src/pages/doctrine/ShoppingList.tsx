import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Badge, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { ShoppingListRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { qty, isk } from '../../format'

const SOURCE_COLOR: Record<string, string> = { Build: 'accent', 'C-J': 'warn', Jita: 'dimmed' }

export default function ShoppingList() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['doctrine', 'shopping-list'], queryFn: () => doctrineApi.shoppingList(),
  })
  const rows = data?.rows ?? []

  const columns = useMemo<ColumnDef<ShoppingListRow, any>[]>(() => [
    { header: 'Type', accessorKey: 'type_name', size: 220 },
    { header: 'Shortfall', accessorKey: 'shortfall', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Build', accessorKey: 'build_cost', size: 130, cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–') },
    { header: 'C-J', accessorKey: 'cj_price', size: 130, cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–') },
    {
      header: 'Jita (landed)', accessorKey: 'jita_landed_price', size: 140,
      cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–'),
    },
    {
      header: 'Recommended', accessorKey: 'recommended_source', size: 130,
      cell: (i) => {
        const source = i.getValue() as string | null
        return source ? <Badge size="sm" color={SOURCE_COLOR[source] ?? 'dimmed'} variant="light">{source}</Badge> : null
      },
    },
    { header: 'Total Cost', accessorKey: 'total_cost', size: 140, cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–') },
  ], [])

  return (
    <Stack>
      <Title order={4}>Shopping List</Title>
      <Text size="sm" c="dimmed">
        Everything currently short across every doctrine (same combined shortfall as the Stockpile tab), with
        whichever of Build / buy at C-J / buy at Jita (landed, including import cost) is cheapest right now.
      </Text>

      {!isLoading && !isError && rows.length === 0 && <Text c="dimmed">Nothing short right now.</Text>}

      {(isLoading || isError || rows.length > 0) && (
        <DataTable
          data={rows}
          columns={columns}
          tableId="doctrine-shopping-list"
          exportFilename="doctrine-shopping-list"
          getRowId={(r) => String(r.type_id)}
          isLoading={isLoading}
          isError={isError}
          onRetry={() => refetch()}
        />
      )}
    </Stack>
  )
}
