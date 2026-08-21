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
  const { data: universe, isLoading: universeLoading, dataUpdatedAt: universeUpdatedAt } =
    useQuery({ queryKey: ['trading', 'candidates', 'universe'], queryFn: tradingApi.candidateUniverse })
  const { data: focused, isLoading: focusedLoading, dataUpdatedAt: focusedUpdatedAt } =
    useQuery({ queryKey: ['trading', 'candidates', 'focused'], queryFn: tradingApi.focusedCandidates })
  const isLoading = universeLoading || focusedLoading

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
      ) : display.length === 0 ? (
        <HintCard>No candidates loaded yet. Click <b>Load Market Groups</b> on the left, then <b>Filter Candidates</b>.</HintCard>
      ) : (
        <DataTable data={display} columns={columns} maxHeight={560} dataUpdatedAt={displayUpdatedAt} />
      )}
    </>
  )
}
