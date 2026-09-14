import { useMemo } from 'react'
import { Button, Stack, Text, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { UnlistedStockRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { pct, qty } from '../../format'

export default function UnlistedStock() {
  const check = useAction('Check Structure Stock Without Order', tradingApi.checkSellerUnlistedStock, [],
    { tier: 'live', effect: 'Compares your ESI asset stock live against currently open sell orders.' })
  const data = check.data

  const columns = useMemo<ColumnDef<UnlistedStockRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 260 },
    { header: 'Stock on Hand', accessorKey: 'asset_quantity', size: 140, cell: (i) => qty(i.getValue()) },
    { header: 'Listed Qty (Structure)', accessorKey: 'sell_volume', size: 150, cell: (i) => qty(i.getValue()) },
    { header: 'Margin', accessorKey: 'margin', size: 90, cell: (i) => pct(i.getValue()) },
  ], [])

  return (
    <Stack>
      <HintCard>
        Shows shortlist items (including hidden ones - you can still sell what you already have) that the seller
        physically has at the structure but for which <b>no</b> sell order currently exists.
      </HintCard>

      <Tooltip label={check.tooltip} disabled={!check.tooltip} multiline w={280}>
        <Button w={280} leftSection={check.tierIcon} onClick={() => check.mutate()} loading={check.isPending}>
          Check Structure Stock Without Order
        </Button>
      </Tooltip>

      {data && data.length === 0 && (
        <HintCard>All shortlist items with stock on hand are listed.</HintCard>
      )}

      {data && data.length > 0 && (
        <>
          <Text size="sm" c="dimmed">{data.length} shortlist items without a sell order</Text>
          <DataTable data={data} columns={columns} maxHeight={560} />
        </>
      )}
    </Stack>
  )
}
