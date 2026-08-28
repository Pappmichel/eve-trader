import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ActionIcon, Group, MultiSelect, Text, TextInput, Tooltip } from '@mantine/core'
import { IconBan, IconRotateClockwise } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { stationTradingApi } from '../../api/client'
import type { StationTradingShortlistRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'

export default function Shortlist() {
  const { data, isLoading } = useQuery({
    queryKey: ['station-trading', 'shortlist'], queryFn: stationTradingApi.shortlist,
  })

  // Reversible (activate below undoes it), so no confirmation prompt - same
  // "not truly destructive" reasoning already applied to Ore Shortlist's
  // own activate/deactivate pair.
  const deactivate = useAction('Deactivate', (typeId: number) => stationTradingApi.deactivateShortlistItems([typeId]), [
    ['station-trading', 'shortlist'],
  ])
  const activate = useAction('Activate', (typeId: number) => stationTradingApi.activateShortlistItems([typeId]), [
    ['station-trading', 'shortlist'],
  ])

  const [search, setSearch] = useState('')
  const [selCategories, setSelCategories] = useState<string[]>([])

  const categories = useMemo(() => [...new Set((data ?? []).map((r) => r.category))].sort(), [data])
  const effectiveCategories = selCategories.length ? selCategories : categories

  const filtered = useMemo(() => {
    return (data ?? []).filter((r) => {
      if (!effectiveCategories.includes(r.category)) return false
      if (search && !r.name.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [data, effectiveCategories, search])

  const columns = useMemo<ColumnDef<StationTradingShortlistRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'name', size: 220 },
    { header: 'Category', accessorKey: 'category', size: 130, meta: { mobileHide: true } },
    { header: 'Spread', accessorKey: 'spread_pct', size: 90, cell: (i) => pct(i.getValue()) },
    { header: 'Avg Daily Volume', accessorKey: 'avg_daily_volume', size: 150, cell: (i) => qty(i.getValue()) },
    { header: 'Live Buy', accessorKey: 'live_buy', size: 120, cell: (i) => isk(i.getValue()) },
    { header: 'Live Sell', accessorKey: 'live_sell', size: 120, cell: (i) => isk(i.getValue()) },
    {
      header: 'Profit / Unit', accessorKey: 'profit_per_unit', size: 130,
      cell: (i) => {
        const v = i.getValue()
        return <Text c={v != null && v < 0 ? 'danger' : undefined}>{isk(v)}</Text>
      },
    },
    { header: 'Margin', accessorKey: 'margin', size: 100, cell: (i) => pct(i.getValue()), meta: { mobileHide: true } },
    { header: 'Discovered', accessorKey: 'discovered_at', size: 150, meta: { mobileHide: true } },
    {
      header: '', id: 'actions', size: 50, enableSorting: false,
      cell: (i) => {
        const row = i.row.original
        return row.active ? (
          <Tooltip label="Deactivate">
            <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Deactivate ${row.name}`}
              onClick={() => deactivate.mutate(row.type_id)} loading={deactivate.isPending}>
              <IconBan size={14} />
            </ActionIcon>
          </Tooltip>
        ) : (
          <Tooltip label="Activate">
            <ActionIcon size="sm" variant="subtle" color="accent" aria-label={`Activate ${row.name}`}
              onClick={() => activate.mutate(row.type_id)} loading={activate.isPending}>
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
      <HintCard>
        No candidates yet. Click <b>Refresh Shortlist</b> on the left to scan Jita for wide bid-ask spreads.
      </HintCard>
    )
  }

  return (
    <>
      <Group mb="md" grow align="flex-end">
        <MultiSelect label="Category" data={categories} value={selCategories} onChange={setSelCategories} placeholder="All" clearable />
        <TextInput label="Search (item)" value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
      </Group>
      <Text size="sm" c="dimmed" mb="xs">{filtered.length} of {data.length} candidates</Text>
      {filtered.length === 0 ? (
        <HintCard>No items match the current filters.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} getRowId={(r) => String(r.type_id)} />
      )}
    </>
  )
}
