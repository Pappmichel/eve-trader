import { useMemo } from 'react'
import { Button, Stack, Text, Title } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { stationTradingApi } from '../../api/client'
import type { StationTradingUndercutRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk } from '../../format'

function useUndercutColumns(competitorHeader: string) {
  return useMemo<ColumnDef<StationTradingUndercutRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'name', size: 260 },
    { header: 'My Price', accessorKey: 'my_price', size: 130, cell: (i) => isk(i.getValue()) },
    { header: competitorHeader, accessorKey: 'competitor_price', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: 'Difference', accessorKey: 'difference', size: 130,
      cell: (i) => <Text c="danger" fw={600}>{isk(i.getValue())}</Text>,
    },
  ], [competitorHeader])
}

export default function UndercutCheck() {
  const check = useAction('Check Undercut Orders', stationTradingApi.checkUndercut)
  const result = check.data

  const sellColumns = useUndercutColumns('Competitor Sell Price')
  const buyColumns = useUndercutColumns('Competitor Bid')

  return (
    <Stack>
      <HintCard>Checks your open orders at Jita against the live order book - sell orders undercut and buy orders outbid.</HintCard>

      <Button w={280} onClick={() => check.mutate()} loading={check.isPending}>
        Check Undercut Orders
      </Button>

      {result && (
        <>
          <div>
            <Title order={5} mb="xs">Sell Orders</Title>
            {result.sell.length === 0 ? (
              <HintCard>None of your sell orders are currently undercut.</HintCard>
            ) : (
              <>
                <Text size="sm" c="dimmed" mb="xs">{result.sell.length} sell orders currently undercut</Text>
                <DataTable data={result.sell} columns={sellColumns} maxHeight={400} />
              </>
            )}
          </div>

          <div>
            <Title order={5} mb="xs">Buy Orders</Title>
            {result.buy.length === 0 ? (
              <HintCard>None of your buy orders are currently outbid.</HintCard>
            ) : (
              <>
                <Text size="sm" c="dimmed" mb="xs">{result.buy.length} buy orders currently outbid</Text>
                <DataTable data={result.buy} columns={buyColumns} maxHeight={400} />
              </>
            )}
          </div>
        </>
      )}
    </Stack>
  )
}
