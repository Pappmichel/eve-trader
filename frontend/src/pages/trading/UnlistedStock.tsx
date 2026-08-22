import { useMemo } from 'react'
import { Button, Stack, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { UnlistedStockRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { pct, qty } from '../../format'

export default function UnlistedStock() {
  const check = useAction('Check Structure Stock Without Order', tradingApi.checkSellerUnlistedStock)
  const data = check.data

  const columns = useMemo<ColumnDef<UnlistedStockRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 260 },
    { header: 'Stock on Hand', accessorKey: 'asset_quantity', size: 140, cell: (i) => qty(i.getValue()) },
    { header: 'Listed Qty (Structure)', accessorKey: 'sell_volume', size: 150, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
    { header: 'Margin', accessorKey: 'margin', size: 90, cell: (i) => pct(i.getValue()) },
  ], [])

  return (
    <Stack>
      <HintCard>
        Shows shortlist items (including deactivated/hidden ones - you can still sell what you already have) that
        the seller character physically has at the structure, but for which <b>no</b> sell order currently exists
        at all. Needs esi-assets.read_assets.v1 on the seller token; if the seller was logged in before this
        feature existed, log in again once.
      </HintCard>

      <Button w={280} onClick={() => check.mutate()} loading={check.isPending}>
        Check Structure Stock Without Order
      </Button>

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
