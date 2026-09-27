import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Checkbox, Group, Stack, Text, TextInput } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { moduleReprocessingApi } from '../../api/client'
import type { DiscoveredModuleResult } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty, pct } from '../../format'

// The "Discover" action itself lives in ModuleReprocessingLayout's sidebar
// (visible from every tab, same as Ore & Minerals' own Add Candidates/
// Refresh Ore Shortlist buttons in OreLayout.tsx) - this page only reads
// its last result and lets the user pick which rows to track live.
export default function Discover() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['module_reprocessing', 'discover', 'results'], queryFn: moduleReprocessingApi.discoveredResults,
  })

  const addToShortlist = useAction('Add to Shortlist', moduleReprocessingApi.addToShortlist, [
    ['module_reprocessing', 'shortlist', 'items'], ['module_reprocessing', 'shortlist', 'snapshot'],
  ], { tier: 'local', effect: 'Adds the selected items to the live-tracked shortlist - no network call.' })

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    return (data ?? []).filter((r) => !search || r.item.toLowerCase().includes(search.toLowerCase()))
  }, [data, search])

  const toggle = (typeId: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(typeId)) next.delete(typeId)
      else next.add(typeId)
      return next
    })
  }

  const columns = useMemo<ColumnDef<DiscoveredModuleResult, any>[]>(() => [
    {
      header: '', id: 'select', size: 40, enableSorting: false,
      cell: (i) => (
        <Checkbox size="xs" checked={selected.has(i.row.original.type_id)}
          onChange={() => toggle(i.row.original.type_id)} aria-label={`Select ${i.row.original.item}`} />
      ),
    },
    { header: 'Item', accessorKey: 'item', size: 260 },
    { header: 'Volume (m³)', accessorKey: 'volume_m3', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Est. Cost', accessorKey: 'est_landed_cost', size: 120, cell: (i) => isk(i.getValue()) },
    { header: 'Est. Mineral Value', accessorKey: 'est_mineral_value', size: 150, cell: (i) => isk(i.getValue()) },
    { header: 'Est. Profit / Unit', accessorKey: 'est_profit_per_unit', size: 130, cell: (i) => isk(i.getValue()) },
    {
      header: 'Est. Margin', accessorKey: 'est_margin', size: 100,
      cell: (i) => {
        const v = i.getValue() as number | null
        if (v === null) return '–'
        return <Badge color={v > 0 ? 'accent' : 'warn'} variant="light">{pct(v)}</Badge>
      },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [selected])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (!data || data.length === 0) {
    return <HintCard>No Discover run yet this session (results aren&apos;t saved across restarts). Click <b>Discover</b> on the left.</HintCard>
  }

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <TextInput label="Search (item)" value={search} onChange={(e) => setSearch(e.currentTarget.value)} w={280} />
        <Button disabled={selected.size === 0} loading={addToShortlist.isPending}
          onClick={() => addToShortlist.mutate(Array.from(selected), {
            onSuccess: () => {
              setSelected(new Set())
              queryClient.invalidateQueries({ queryKey: ['module_reprocessing', 'shortlist'] })
            },
          })}>
          Add {selected.size > 0 ? selected.size : ''} to Shortlist
        </Button>
      </Group>

      <Text size="sm" c="dimmed">
        {filtered.length} of {data.length} discovered items - estimates only, from a Goonmetrics current-price
        snapshot. Pick items to track live on the Shortlist tab; nothing here is priced via ESI yet.
      </Text>

      <DataTable data={filtered} columns={columns} maxHeight={560} getRowId={(r) => String(r.type_id)} />
    </Stack>
  )
}
