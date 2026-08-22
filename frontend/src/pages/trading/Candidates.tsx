import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { SimpleGrid, Card, Text, Title } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { Candidate } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

export default function Candidates() {
  const { data: universe, isLoading: universeLoading, isError: universeError, refetch: refetchUniverse, dataUpdatedAt: universeUpdatedAt } =
    useQuery({ queryKey: ['trading', 'candidates', 'universe'], queryFn: tradingApi.candidateUniverse })
  const { data: focused, isLoading: focusedLoading, isError: focusedError, refetch: refetchFocused, dataUpdatedAt: focusedUpdatedAt } =
    useQuery({ queryKey: ['trading', 'candidates', 'focused'], queryFn: tradingApi.focusedCandidates })
  const isLoading = universeLoading || focusedLoading
  // Both must fail before showing the full-page error - these are two
  // independent sources with a graceful one-way fallback (`display` below):
  // if only `focused` fails, `universe` still renders fine (and vice versa).
  // OR-ing the two error flags together used to hide a successfully-loaded
  // source's data behind the error state the moment the *other* source
  // failed - a real regression, not just an incomplete fix (confirmed in
  // code review).
  const isError = universeError && focusedError
  const refetch = () => { refetchUniverse(); refetchFocused() }

  const display = focused && focused.length > 0 ? focused : universe ?? []
  const displayUpdatedAt = focused && focused.length > 0 ? focusedUpdatedAt : universeUpdatedAt

  const columns = useMemo<ColumnDef<Candidate, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 220 },
    { header: 'Category', accessorKey: 'category', size: 120 },
    { header: 'Meta Level', accessorKey: 'meta_level', size: 90, cell: (i) => i.getValue() ?? '–' },
    { header: 'Volume (m³)', accessorKey: 'volume_m3', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Market Group Path', accessorKey: 'market_group_path', size: 380 },
  ], [])

  return (
    <>
      <SimpleGrid cols={2} mb="md">
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed" tt="uppercase">Candidate Universe</Text>
          <Title order={3}>{universe?.length ?? 0}</Title>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed" tt="uppercase">Focused Candidates</Text>
          <Title order={3} c="accent">{focused?.length ?? 0}</Title>
        </Card>
      </SimpleGrid>

      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={refetch} maxHeight={560} />
      ) : display.length === 0 ? (
        <HintCard>No candidates loaded yet. Click <b>Load Market Groups</b> on the left, then <b>Filter Candidates</b>.</HintCard>
      ) : (
        <DataTable data={display} columns={columns} maxHeight={560} dataUpdatedAt={displayUpdatedAt} />
      )}
    </>
  )
}
