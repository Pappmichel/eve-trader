import { useMemo, useState } from 'react'
import { Badge, Button, Group, Paper, Stack, Text, TextInput } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'
import { useQuery } from '@tanstack/react-query'

import { productionApi } from '../../api/client'
import type { ShipMarginRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, pct } from '../../format'

function MarginDetailCard({ row }: { row: ShipMarginRow }) {
  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="xs">
        <Text fw={700}>{row.type_name}</Text>
        <Badge size="sm" variant="light">{row.activity}</Badge>
      </Group>
      <Group gap="xl">
        <div>
          <Text size="xs" c="dimmed">Home Price</Text>
          <Text>{row.home_price === null ? '–' : isk(row.home_price)}</Text>
        </div>
        <div>
          <Text size="xs" c="dimmed">Jita Price</Text>
          <Text>{row.jita_price === null ? '–' : isk(row.jita_price)}</Text>
        </div>
        <div>
          <Text size="xs" c="dimmed">Build Cost</Text>
          <Text>{row.build_cost === null ? '–' : isk(row.build_cost)}</Text>
        </div>
        <div>
          <Text size="xs" c="dimmed">Margin (Home)</Text>
          <Text c={row.margin_home !== null && row.margin_home > 0 ? 'accent' : undefined}>
            {row.margin_home === null ? '–' : pct(row.margin_home)}
          </Text>
        </div>
        <div>
          <Text size="xs" c="dimmed">Margin (Jita)</Text>
          <Text c={row.margin_jita !== null && row.margin_jita > 0 ? 'accent' : undefined}>
            {row.margin_jita === null ? '–' : pct(row.margin_jita)}
          </Text>
        </div>
      </Group>
    </Paper>
  )
}

function ItemSearch() {
  const [itemName, setItemName] = useState('')
  const search = useAction('Search Item Margin', (name: string) => productionApi.itemMargin(name))

  return (
    <Stack>
      <Group align="flex-end">
        <TextInput
          label="Item name (exact)" placeholder="e.g. Rifter" value={itemName}
          onChange={(e) => setItemName(e.currentTarget.value)} w={320}
        />
        <Button loading={search.isPending} disabled={!itemName} onClick={() => search.mutate(itemName)}>
          Search
        </Button>
      </Group>
      {search.data && <MarginDetailCard row={search.data} />}
    </Stack>
  )
}

export default function Margin() {
  const { data, isLoading } = useQuery({ queryKey: ['production', 'margins'], queryFn: productionApi.shipMargins })

  const columns = useMemo<ColumnDef<ShipMarginRow, any>[]>(() => [
    { header: 'Ship', accessorKey: 'type_name', size: 240 },
    { header: 'Home Price', accessorKey: 'home_price', size: 130, cell: (i) => i.getValue() === null ? '–' : isk(i.getValue()) },
    { header: 'Jita Price', accessorKey: 'jita_price', size: 130, cell: (i) => i.getValue() === null ? '–' : isk(i.getValue()) },
    { header: 'Build Cost', accessorKey: 'build_cost', size: 130, cell: (i) => i.getValue() === null ? '–' : isk(i.getValue()) },
    { header: 'Margin (Home)', accessorKey: 'margin_home', size: 130, cell: (i) => i.getValue() === null ? '–' : pct(i.getValue()) },
    { header: 'Margin (Jita)', accessorKey: 'margin_jita', size: 130, cell: (i) => i.getValue() === null ? '–' : pct(i.getValue()) },
  ], [])

  return (
    <Stack>
      <HintCard>
        Home/Jita sell price, build cost, and margin for every ship - a pure information view, not a candidate
        filter like Build Candidates: every ship with a real Manufacturing/Reaction/Invention recipe appears here
        regardless of margin or whether it's already a configured stock target. "–" means no real order book on
        that side right now, not a margin of zero. Jita margin is informational (what selling there would net after
        the export haul cost) - Production still only sells at C-J, this doesn't change that.
      </HintCard>

      <ItemSearch />

      {isLoading && <Text c="dimmed" size="sm">Loading…</Text>}
      {data && data.length === 0 && <HintCard>No ships found - Refresh SDE (Admin tool) first?</HintCard>}
      {data && data.length > 0 && <DataTable data={data} columns={columns} maxHeight={560} />}
    </Stack>
  )
}
