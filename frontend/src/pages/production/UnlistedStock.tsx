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
    { header: 'Listed Qty (C-J)', accessorKey: 'sell_volume', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'Margin (Home)', accessorKey: 'margin', size: 110, cell: (i) => pct(i.getValue()) },
  ], [])

  return (
    <Stack>
      <HintCard>
        Live-queries ESI for the assets and open sell orders of every registered producer character <b>and</b> their
        corporations (personal orders <b>and</b> corp orders - corp-hangar stock is often listed via a corp order,
        not a personal one) at the configured location (Settings -&gt; structure/location ID), and shows stock
        targets that sit there but currently have <b>no</b> sell order at all - always fresh, never from the last
        ESI sync snapshot. Corp orders need the Accountant or Trader role (separate from the Director role assets
        need) on at least one registered character; characters added before this existed need to be re-added once.
        Only considers targets that actually have a <b>Home Market Target</b> or <b>Jita Market Target</b> set - a
        target with only a Backup Target is a personal/component buffer you never intended to list, so it's
        excluded here (see the Stock Targets tab).
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
