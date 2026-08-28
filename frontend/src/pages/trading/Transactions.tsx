import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Group, Select, SegmentedControl } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { WalletTransaction } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { dateTime, isk, qty } from '../../format'

const LOOKBACK_OPTIONS = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '180', label: '180 days' },
]

type SideFilter = 'all' | 'buy' | 'sell'

export default function Transactions() {
  const { data: characters } = useQuery({ queryKey: ['trading', 'transaction-characters'], queryFn: tradingApi.transactionCharacters })
  const [roleKey, setRoleKey] = useState<string | null>(null)
  const [lookbackDays, setLookbackDays] = useState<string>('30')
  const [side, setSide] = useState<SideFilter>('all')

  useEffect(() => {
    if (!roleKey && characters && characters.length > 0) setRoleKey(characters[0].role_key)
  }, [characters, roleKey])

  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['trading', 'wallet-transactions', roleKey, lookbackDays],
    queryFn: () => tradingApi.walletTransactions(roleKey as string, Number(lookbackDays)),
    enabled: !!roleKey,
  })

  const filtered = useMemo(() => {
    return (data ?? []).filter((t) => {
      if (side === 'buy' && !t.is_buy) return false
      if (side === 'sell' && t.is_buy) return false
      return true
    })
  }, [data, side])

  const columns = useMemo<ColumnDef<WalletTransaction, any>[]>(() => [
    { header: 'Date', accessorKey: 'date', size: 160, cell: (i) => dateTime(i.getValue()) },
    { header: 'Item', accessorKey: 'item', size: 220 },
    {
      header: 'Side', accessorKey: 'is_buy', size: 90,
      cell: (i) => <Badge color={i.getValue() ? 'info' : 'accent'}>{i.getValue() ? 'Buy' : 'Sell'}</Badge>,
    },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Unit Price', accessorKey: 'unit_price', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'Total', accessorKey: 'total', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Location', accessorKey: 'location_name', size: 220, meta: { mobileHide: true },
      cell: (i) => i.getValue() ?? '–' },
  ], [])

  if (!characters || characters.length === 0) {
    return <HintCard>No buyer/seller characters logged in yet. Log in a character on the Overview page to see its wallet transactions here.</HintCard>
  }

  return (
    <>
      <Group mb="md" align="flex-end">
        <Select
          label="Character"
          data={characters.map((c) => ({ value: c.role_key, label: c.character_name }))}
          value={roleKey}
          onChange={setRoleKey}
          w={240}
        />
        <Select
          label="Lookback"
          data={LOOKBACK_OPTIONS}
          value={lookbackDays}
          onChange={(v) => v && setLookbackDays(v)}
          w={140}
        />
        <SegmentedControl
          value={side}
          onChange={(v) => setSide(v as SideFilter)}
          data={[
            { value: 'all', label: 'All' },
            { value: 'buy', label: 'Buy' },
            { value: 'sell', label: 'Sell' },
          ]}
        />
      </Group>

      <DataTable
        data={filtered}
        columns={columns}
        maxHeight={560}
        tableId="trading-transactions"
        isLoading={isLoading}
        isError={isError}
        onRetry={() => refetch()}
        dataUpdatedAt={dataUpdatedAt}
        emptyLabel="No wallet transactions in this window."
      />
    </>
  )
}
