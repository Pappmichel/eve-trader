import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ActionIcon, Badge, Group, MultiSelect, Stack, Text, TextInput, Tooltip } from '@mantine/core'
import { IconBan, IconRotateClockwise } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { moduleReprocessingApi } from '../../api/client'
import type { ModuleShortlistRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'

const ALL_DECISIONS = ['Inactive', 'No market data', 'Skip', 'Import']
const DECISION_COLOR: Record<string, string> = {
  Import: 'accent',
  'No market data': 'warn',
  Skip: 'warn',
  Inactive: 'danger',
}

export default function Shortlist() {
  const { data, isLoading } = useQuery({
    queryKey: ['module_reprocessing', 'shortlist', 'snapshot'], queryFn: moduleReprocessingApi.shortlistSnapshot,
  })

  // Reversible (activate below undoes it), so no confirmation prompt - same
  // reasoning as Ore & Minerals' own Ore Shortlist.
  const deactivate = useAction('Deactivate', (itemId: number) => moduleReprocessingApi.deactivateShortlistItems([itemId]), [
    ['module_reprocessing', 'shortlist', 'snapshot'],
  ])
  const activate = useAction('Activate', (itemId: number) => moduleReprocessingApi.activateShortlistItems([itemId]), [
    ['module_reprocessing', 'shortlist', 'snapshot'],
  ])

  const [selDecisions, setSelDecisions] = useState<string[]>(ALL_DECISIONS)
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    return (data ?? []).filter((r) => {
      if (!selDecisions.includes(r.decision)) return false
      if (search && !r.item.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [data, selDecisions, search])

  const columns = useMemo<ColumnDef<ModuleShortlistRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 260 },
    {
      header: 'Status', accessorKey: 'decision', size: 130,
      cell: (i) => <Badge color={DECISION_COLOR[i.getValue() as string] ?? 'gray'} variant="light">{i.getValue()}</Badge>,
    },
    { header: 'Yield %', accessorKey: 'yield_pct', size: 90, cell: (i) => pct(i.getValue()) },
    { header: 'Margin', accessorKey: 'margin', size: 90, cell: (i) => pct(i.getValue()) },
    { header: 'Profit / Unit', accessorKey: 'profit_per_unit', size: 120, cell: (i) => isk(i.getValue()) },
    { header: 'Profit / m³', accessorKey: 'profit_per_m3', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Cost', accessorKey: 'landed_cost', size: 120, cell: (i) => isk(i.getValue()) },
    { header: 'Mineral Value (C-J)', accessorKey: 'net_sell', size: 150, cell: (i) => isk(i.getValue()) },
    { header: 'Refining Tax', accessorKey: 'refining_tax', size: 110, cell: (i) => isk(i.getValue()) },
    { header: 'Listed Qty', accessorKey: 'sell_listed_qty', size: 110, cell: (i) => qty(i.getValue()) },
    {
      header: '', id: 'actions', size: 50, enableSorting: false,
      cell: (i) => {
        const row = i.row.original
        return row.active ? (
          <Tooltip label="Deactivate">
            <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Deactivate ${row.item}`}
              onClick={() => deactivate.mutate(row.item_id)} loading={deactivate.isPending}>
              <IconBan size={14} />
            </ActionIcon>
          </Tooltip>
        ) : (
          <Tooltip label="Activate">
            <ActionIcon size="sm" variant="subtle" color="accent" aria-label={`Activate ${row.item}`}
              onClick={() => activate.mutate(row.item_id)} loading={activate.isPending}>
              <IconRotateClockwise size={14} />
            </ActionIcon>
          </Tooltip>
        )
      },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [activate, deactivate])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (!data || data.length === 0) {
    return (
      <HintCard>No run yet. Click <b>Refresh Shortlist</b> on the left - it scans for candidates and prices them in one go.</HintCard>
    )
  }

  return (
    <Stack>
      <Group grow align="flex-end">
        <MultiSelect label="Status" data={ALL_DECISIONS} value={selDecisions} onChange={setSelDecisions} />
        <TextInput label="Search (item)" value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
      </Group>

      <Text size="sm" c="dimmed">{filtered.length} of {data.length} items</Text>

      {filtered.length === 0 ? (
        <HintCard>No items match the current filters.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} getRowId={(r) => String(r.item_id)} />
      )}
    </Stack>
  )
}
