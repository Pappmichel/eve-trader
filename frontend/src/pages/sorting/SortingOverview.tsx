import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Group, MultiSelect, Stack, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { sortingApi } from '../../api/client'
import type { SortingRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'
import { filterSortingRows, UNCLAIMED } from './sortingFilters'

const TOOL_LABELS: Record<string, string> = {
  trading: 'Trading',
  markt: 'Markt',
  material: 'Material',
  doctrine: 'Doctrine',
  ore_minerals: 'Ore & Minerals',
}

function restQty(row: SortingRow): number {
  return Math.max(0, row.intake_qty - row.wanted_by_tool.reduce((sum, w) => sum + w.wanted_qty, 0))
}

function sourceSummary(row: SortingRow): string {
  return row.by_source.map((s) => `${s.source_label}: ${qty(s.qty)}`).join(', ')
}

function wantedSummary(row: SortingRow): string {
  if (row.unclaimed) return 'nobody'
  return row.wanted_by_tool.map((w) => `${TOOL_LABELS[w.tool] ?? w.tool}: ${qty(w.wanted_qty)}`).join(', ')
}

const EMPTY_ROWS: SortingRow[] = []

export default function SortingOverview() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['sorting', 'sorting-list'], queryFn: sortingApi.sortingList,
  })
  const rows = data?.rows ?? EMPTY_ROWS

  const toolOptions = useMemo(() => {
    const seen = new Set<string>()
    for (const row of rows) {
      if (row.unclaimed) seen.add(UNCLAIMED)
      for (const w of row.wanted_by_tool) seen.add(w.tool)
    }
    return [...seen].sort().map((value) => ({
      value,
      label: value === UNCLAIMED ? 'Nobody' : (TOOL_LABELS[value] ?? value),
    }))
  }, [rows])

  const sourceOptions = useMemo(
    () => [...new Set(rows.flatMap((r) => r.by_source.map((s) => s.source_label)))].sort(),
    [rows],
  )

  const [selTools, setSelTools] = useState<string[]>([])
  const [selSources, setSelSources] = useState<string[]>([])
  const filtered = useMemo(
    () => filterSortingRows(rows, selTools, selSources),
    [rows, selTools, selSources],
  )

  const columns = useMemo<ColumnDef<SortingRow, unknown>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    {
      header: 'In Wareneingang', accessorKey: 'intake_qty', size: 140,
      cell: (i) => qty(i.getValue() as number),
    },
    {
      header: 'By source', id: 'by_source', size: 280,
      accessorFn: sourceSummary,
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
      meta: { mobileHide: true },
    },
    {
      header: 'Wanted by', id: 'wanted_by_tool', size: 320,
      accessorFn: wantedSummary,
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
      accessorFn: restQty,
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
      {(isLoading || isError || rows.length > 0) && (
        <Group align="flex-end">
          <MultiSelect
            label="Wanted by" data={toolOptions} value={selTools} onChange={setSelTools}
            placeholder="All" clearable searchable w={280}
          />
          <MultiSelect
            label="Source" data={sourceOptions} value={selSources} onChange={setSelSources}
            placeholder="All" clearable searchable w={280}
          />
        </Group>
      )}
      {!isLoading && !isError && rows.length > 0 && (
        <Text size="xs" c="dimmed">{filtered.length} of {rows.length} items</Text>
      )}
      {!isLoading && !isError && rows.length > 0 && filtered.length === 0 ? (
        <HintCard>No items match the current filters.</HintCard>
      ) : (
        <DataTable
          tableId="sorting-overview"
          exportFilename="sorting-overview"
          data={filtered}
          columns={columns}
          maxHeight={560}
          getRowId={(r) => String(r.type_id)}
          isLoading={isLoading}
          isError={isError}
          onRetry={() => refetch()}
          dataUpdatedAt={dataUpdatedAt}
          emptyLabel="Nothing to sort right now — either no intake sources are configured (Settings), or they are currently empty."
        />
      )}
    </Stack>
  )
}
