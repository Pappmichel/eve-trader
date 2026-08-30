import { useMemo } from 'react'
import { Button, Stack, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { ProductionUnlistedStockRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { pct, qty } from '../../format'

export default function UnlistedStock() {
  const check = useAction('Check Structure Stock Without Order', productionApi.checkUnlistedStock)
  const data = check.data

  const columns = useMemo<ColumnDef<ProductionUnlistedStockRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260 },
    { header: 'Stock in C-J Hangar', accessorKey: 'stock_quantity', size: 170, cell: (i) => qty(i.getValue()) },
    { header: 'Listed Qty (C-J)', accessorKey: 'sell_volume', size: 130, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
    { header: 'Margin (Home)', accessorKey: 'margin', size: 110, cell: (i) => pct(i.getValue()) },
  ], [])

  return (
    <Stack>
      <HintCard>
        Live-checks stock targets at the configured location (Settings) that have no sell order yet - personal
        <b>and</b> corp orders/hangars. Corp orders need the Accountant or Trader role on at least one registered
        character. Only targets with a <b>Home</b> or <b>Jita Market Target</b> are considered.
      </HintCard>

      <Button w={280} onClick={() => check.mutate()} loading={check.isPending}>
        Check Structure Stock Without Order
      </Button>

      {data && data.length === 0 && (
        <HintCard>All listing-target stock in the C-J hangar is listed.</HintCard>
      )}

      {data && data.length > 0 && (
        <>
          <Text size="sm" c="dimmed">{data.length} listing-target stock items in the C-J hangar without a sell order</Text>
          <DataTable data={data} columns={columns} maxHeight={560} />
        </>
      )}
    </Stack>
  )
}
