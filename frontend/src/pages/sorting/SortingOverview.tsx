import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Group, Stack, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { sortingApi } from '../../api/client'
import type { SortingRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

const TOOL_LABELS: Record<string, string> = {
  markt: 'Markt',
  material: 'Material',
  doctrine: 'Doctrine',
  ore_minerals: 'Ore & Minerals',
}

export default function SortingOverview() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['sorting', 'sorting-list'], queryFn: sortingApi.sortingList,
  })

  const columns = useMemo<ColumnDef<SortingRow, unknown>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    {
      header: 'In Wareneingang', accessorKey: 'intake_qty', size: 140,
      cell: (i) => qty(i.getValue() as number),
    },
    {
      header: 'By source', id: 'by_source', size: 280, enableSorting: false,
      cell: (i) => {
        const row = i.row.original
        if (row.by_source.length === 0) return <Text size="sm" c="dimmed">–</Text>
        return (
          <Group gap={4} wrap="wrap">
            {row.by_source.map((s, idx) => (
              <Badge key={`${s.source_label}-${idx}`} color="gray" variant="light">
                {s.source_label}: {qty(s.qty)}
              </Badge>
            ))}
          </Group>
        )
      },
    },
    {
      header: 'Wanted by', id: 'wanted_by_tool', size: 320, enableSorting: false,
      cell: (i) => {
        const row = i.row.original
        if (row.unclaimed) return <Badge color="gray" variant="light">nobody</Badge>
        return (
          <Group gap={4} wrap="wrap">
            {row.wanted_by_tool.map((w) => (
              <Badge key={w.tool} color="accent" variant="light">
                {TOOL_LABELS[w.tool] ?? w.tool}: {qty(w.wanted_qty)}
              </Badge>
            ))}
          </Group>
        )
      },
    },
    {
      header: 'Rest', id: 'rest', size: 100,
      accessorFn: (row) => Math.max(0, row.intake_qty - row.wanted_by_tool.reduce((sum, w) => sum + w.wanted_qty, 0)),
      cell: (i) => qty(i.getValue() as number),
    },
  ], [])

  return (
    <Stack>
      <HintCard>
        Read-only. EVE has no concept of reserving a stack for a tool — if several pots want more than is
        sitting in the intake, you still decide by hand where each unit goes. Configure intake hangars on
        Settings.
      </HintCard>
      <DataTable
        tableId="sorting-overview"
        data={data?.rows ?? []}
        columns={columns}
        isLoading={isLoading}
        isError={isError}
        onRetry={() => refetch()}
        dataUpdatedAt={dataUpdatedAt}
        emptyLabel="Nothing to sort right now — either no intake sources are configured (Settings), or they are currently empty."
      />
    </Stack>
  )
}
