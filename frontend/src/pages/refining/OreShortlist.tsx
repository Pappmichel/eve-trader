import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Group, MultiSelect, Stack, Text, TextInput } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { refiningApi } from '../../api/client'
import type { OreShortlistRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { isk, pct, qty } from '../../format'

const ALL_DECISIONS = ['Inactive', 'No market data', 'Skip', 'Import']
const DECISION_COLOR: Record<string, string> = {
  Import: 'accent',
  'No market data': 'warn',
  Skip: 'warn',
  Inactive: 'danger',
}

export default function OreShortlist() {
  const { data, isLoading } = useQuery({
    queryKey: ['refining', 'shortlist', 'snapshot'], queryFn: refiningApi.shortlistSnapshot,
  })

  const [selFamilies, setSelFamilies] = useState<string[]>([])
  const [selDecisions, setSelDecisions] = useState<string[]>(ALL_DECISIONS)
  const [search, setSearch] = useState('')

  const families = useMemo(() => [...new Set((data ?? []).map((r) => r.family))].sort(), [data])
  const effectiveFamilies = selFamilies.length ? selFamilies : families

  const filtered = useMemo(() => {
    return (data ?? []).filter((r) => {
      if (!effectiveFamilies.includes(r.family)) return false
      if (!selDecisions.includes(r.decision)) return false
      if (search && !r.item.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [data, effectiveFamilies, selDecisions, search])

  const columns = useMemo<ColumnDef<OreShortlistRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 200 },
    { header: 'Family', accessorKey: 'family', size: 120, meta: { mobileHide: true } },
    {
      header: 'Type', accessorKey: 'is_ice', size: 80, meta: { mobileHide: true },
      cell: (i) => (i.getValue() ? 'Ice' : 'Ore'),
    },
    {
      header: 'Status', accessorKey: 'decision', size: 130,
      cell: (i) => <Badge color={DECISION_COLOR[i.getValue() as string] ?? 'gray'} variant="light">{i.getValue()}</Badge>,
    },
    { header: 'Yield %', accessorKey: 'yield_pct', size: 90, cell: (i) => pct(i.getValue()) },
    { header: 'Margin', accessorKey: 'margin', size: 90, cell: (i) => pct(i.getValue()) },
    { header: 'Profit / Unit', accessorKey: 'profit_per_unit', size: 120, cell: (i) => isk(i.getValue()) },
    { header: 'Profit / m³', accessorKey: 'profit_per_m3', size: 110, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
    { header: 'Cost (Jita)', accessorKey: 'landed_cost', size: 120, cell: (i) => isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Mineral Value (C-J)', accessorKey: 'net_sell', size: 150, cell: (i) => isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Refining Tax', accessorKey: 'refining_tax', size: 110, cell: (i) => isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Jita Listed Qty', accessorKey: 'sell_listed_qty', size: 130, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (!data || data.length === 0) {
    return (
      <HintCard>
        No run yet. Click <b>Add Candidates</b> then <b>Refresh Ore Shortlist</b> on the left to import the
        compressed ore/ice universe and compute profit.
      </HintCard>
    )
  }

  return (
    <Stack>
      <Group grow align="flex-end">
        <MultiSelect label="Family" data={families} value={selFamilies} onChange={setSelFamilies} placeholder="All" clearable />
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
