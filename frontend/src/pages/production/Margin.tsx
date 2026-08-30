import { useMemo, useState } from 'react'
import { Badge, Button, Group, Paper, Stack, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'
import { useQuery } from '@tanstack/react-query'

import { productionApi } from '../../api/client'
import type { ShipMarginRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
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
  const { data: itemNameOptions } = useItemNameOptions()
  const options = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [itemId, setItemId] = useState<string | null>(null)
  const search = useAction('Search Item Margin', (name: string) => productionApi.itemMargin(name))

  return (
    <Stack>
      <Group align="flex-end">
        <SearchableSelect label="Item name" placeholder="Search item…" data={options} value={itemId} onChange={setItemId} w={320} />
        <Button loading={search.isPending} disabled={!itemId}
          onClick={() => search.mutate(options.find((o) => o.value === itemId)?.label ?? '')}>
          Search
        </Button>
      </Group>
      {search.data && <MarginDetailCard row={search.data} />}
    </Stack>
  )
}

export default function Margin() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'margins'], queryFn: productionApi.shipMargins })

  const columns = useMemo<ColumnDef<ShipMarginRow, any>[]>(() => [
    { header: 'Ship', accessorKey: 'type_name', size: 240 },
    { header: 'Home Price', accessorKey: 'home_price', size: 130, cell: (i) => i.getValue() === null ? '–' : isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Jita Price', accessorKey: 'jita_price', size: 130, cell: (i) => i.getValue() === null ? '–' : isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Build Cost', accessorKey: 'build_cost', size: 130, cell: (i) => i.getValue() === null ? '–' : isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Margin (Home)', accessorKey: 'margin_home', size: 130, cell: (i) => i.getValue() === null ? '–' : pct(i.getValue()) },
    { header: 'Margin (Jita)', accessorKey: 'margin_jita', size: 130, cell: (i) => i.getValue() === null ? '–' : pct(i.getValue()), meta: { mobileHide: true } },
  ], [])

  return (
    <Stack>
      <HintCard>
        Home/Jita sell price, build cost, and margin for every ship - a pure information view, not filtered by
        margin like Build Candidates. "–" means no real order book, not a margin of zero. Jita margin is
        informational only; Production still only sells at C-J.
      </HintCard>

      <ItemSearch />

      {isLoading && <Text c="dimmed" size="sm">Loading…</Text>}
      {isError && <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />}
      {data && data.length === 0 && <HintCard>No ships found - Refresh SDE (Admin tool) first?</HintCard>}
      {data && data.length > 0 && <DataTable data={data} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />}
    </Stack>
  )
}
