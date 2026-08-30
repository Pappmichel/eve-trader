import { useMemo } from 'react'
import { Button, Stack, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { UndercutRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk } from '../../format'

export default function UndercutCheck() {
  const check = useAction('Check Undercut Orders', tradingApi.checkUndercut)
  const data = check.data

  const columns = useMemo<ColumnDef<UndercutRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 260 },
    { header: 'My Price', accessorKey: 'my_price', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Competitor Price', accessorKey: 'competitor_price', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: 'Difference', accessorKey: 'difference', size: 130,
      cell: (i) => <Text c="danger" fw={600}>{isk(i.getValue())}</Text>,
    },
  ], [])

  return (
    <Stack>
      <HintCard>Checks your open sell orders at the structure against the live order book and flags any beaten by a cheaper competitor.</HintCard>

      <Button w={280} onClick={() => check.mutate()} loading={check.isPending}>
        Check Undercut Orders
      </Button>

      {data && data.length === 0 && (
        <HintCard>None of your sell orders are currently undercut.</HintCard>
      )}

      {data && data.length > 0 && (
        <>
          <Text size="sm" c="dimmed">{data.length} orders currently undercut</Text>
          <DataTable data={data} columns={columns} maxHeight={560} />
        </>
      )}
    </Stack>
  )
}
