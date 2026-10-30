import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Badge, Text, Alert } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { ShoppingListRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { qty, isk, pct } from '../../format'
import { ALL_HUBS, hubLabel } from '../../tradingHubs'

const SOURCE_COLOR: Record<string, string> = { Build: 'accent', 'C-J': 'warn', Jita: 'dimmed' }

export default function ShoppingList() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['doctrine', 'shopping-list'], queryFn: () => doctrineApi.shoppingList(),
  })
  const buyers = data?.buyers_configured ?? false
  const warnings = data?.warnings ?? []
  // Fully covered rows (nothing left to buy) go to the end.
  const rows = useMemo(() => {
    const all = data?.rows ?? []
    const covered = (r: ShoppingListRow) => r.to_buy <= 0
    return [...all.filter((r) => !covered(r)), ...all.filter(covered)]
  }, [data])
  const { data: settings } = useQuery({ queryKey: ['doctrine', 'settings'], queryFn: doctrineApi.settings })
  const hub = hubLabel(settings?.hub_region_id)
  const allHubs = settings?.hub_region_id === ALL_HUBS

  const columns = useMemo<ColumnDef<ShoppingListRow, any>[]>(() => [
    { header: 'Type', accessorKey: 'type_name', size: 220, meta: { copyable: true } },
    {
      header: 'Stock %', id: 'stock_pct', size: 90,
      accessorFn: (r) => (r.required_total > 0 ? r.available / r.required_total : null),
      cell: (i) => pct(i.getValue()),
    },
    { header: 'Shortfall', accessorKey: 'shortfall', size: 110, cell: (i) => qty(i.getValue()), meta: buyers ? undefined : { exportRole: 'qty' } },
    ...(buyers ? [
      { header: 'Jita 4-4', accessorKey: 'jita_hangar', size: 100, cell: (i: any) => (i.getValue() > 0 ? qty(i.getValue()) : '–') },
      { header: 'On order', accessorKey: 'on_order', size: 100, cell: (i: any) => (i.getValue() > 0 ? qty(i.getValue()) : '–') },
      {
        header: 'To buy', accessorKey: 'to_buy', size: 110, meta: { exportRole: 'qty' },
        cell: (i: any) => (i.getValue() > 0
          ? qty(i.getValue())
          : <Badge size="sm" color="dimmed" variant="light">covered</Badge>),
      },
    ] : []),
    { header: 'Build', accessorKey: 'build_cost', size: 130, cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–') },
    { header: 'C-J', accessorKey: 'cj_price', size: 130, cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–') },
    {
      header: `${hub} (landed)`, accessorKey: 'jita_landed_price', size: 140,
      cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–'),
    },
    ...(allHubs ? [{
      header: 'Best hub', accessorKey: 'hub_name', size: 110,
      cell: (i: any) => (i.getValue() ?? '–'),
    }] : []),
    {
      header: 'Recommended', accessorKey: 'recommended_source', size: 130,
      cell: (i) => {
        const source = i.getValue() as string | null
        return source ? <Badge size="sm" color={SOURCE_COLOR[source] ?? 'dimmed'} variant="light">{source}</Badge> : null
      },
    },
    { header: 'Total Cost', accessorKey: 'total_cost', size: 140, cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–') },
  ], [hub, allHubs, buyers])

  return (
    <Stack>
      <Title order={4}>Shopping List</Title>
      <Text size="sm" c="dimmed">
        Everything currently short across every doctrine (same combined shortfall as the Stockpile tab), with
        whichever of Build / buy at C-J / buy at {allHubs ? 'the best hub' : hub} (landed, including freight) is cheapest right now.
        {buyers && " The buyer characters' Jita 4-4 hangar and open Jita 4-4 buy orders cover the shortfall first; Total Cost prices only what is left to buy."}
      </Text>
      {warnings.length > 0 && (
        <Alert color="warn" variant="light" title="Buyer sources incomplete">
          {warnings.map((w) => <Text key={w} size="sm">{w}</Text>)}
        </Alert>
      )}

      {!isLoading && !isError && rows.length === 0 && <Text c="dimmed">Nothing short right now.</Text>}

      {(isLoading || isError || rows.length > 0) && (
        <DataTable
          rowDetail
          data={rows}
          columns={columns}
          tableId="doctrine-shopping-list"
          exportFilename="doctrine-shopping-list"
          getRowId={(r) => String(r.type_id)}
          isLoading={isLoading}
          isError={isError}
          onRetry={() => refetch()}
          dataUpdatedAt={dataUpdatedAt}
        />
      )}
    </Stack>
  )
}
