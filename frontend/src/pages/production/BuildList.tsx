import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, MultiSelect } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { BuildJobEntry } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'

const CATEGORY_UNKNOWN = 'no category'

export default function BuildList() {
  const { data: plan, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'plan'], queryFn: productionApi.plan })
  const buildList = plan?.build_list ?? []

  const refreshPlan = useAction('Refresh Production', productionApi.refreshPlan, [
    ['production', 'plan'], ['production', 'stock-targets'], ['production', 'logistics'],
  ])

  const categories = useMemo(
    () => [...new Set(buildList.map((e) => e.job_category ?? CATEGORY_UNKNOWN))].sort(), [buildList],
  )
  const [selCategories, setSelCategories] = useState<string[]>([])
  const filtered = useMemo(() => {
    if (selCategories.length === 0) return buildList
    return buildList.filter((e) => selCategories.includes(e.job_category ?? CATEGORY_UNKNOWN))
  }, [buildList, selCategories])

  const totalHours = useMemo(() => filtered.reduce((sum, e) => sum + e.job_time_seconds / 3600, 0), [filtered])

  const columns = useMemo<ColumnDef<BuildJobEntry, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 240 },
    { header: 'Category', accessorKey: 'job_category', size: 160, cell: (i) => i.getValue() ?? '–' },
    { header: 'Activity', accessorKey: 'activity', size: 130 },
    { header: 'Job Runs', accessorKey: 'job_runs', size: 100, cell: (i) => qty(i.getValue()) },
    { header: 'Quantity (Output)', accessorKey: 'quantity', size: 140, cell: (i) => qty(i.getValue()) },
    { header: 'Job Time (h)', id: 'hours', size: 120, accessorFn: (r) => r.job_time_seconds / 3600, cell: (i) => (i.getValue() as number).toFixed(2) },
    { header: 'Modeled Unit Cost', accessorKey: 'unit_build_cost', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: 'Margin', accessorKey: 'margin', size: 110,
      cell: (i) => {
        const v = i.getValue()
        return v === null ? '–' : <Text c={v > 0 ? 'accent' : undefined}>{pct(v)}</Text>
      },
    },
    { header: 'Decryptor', accessorKey: 'decryptor', size: 130, cell: (i) => i.getValue() ?? '–' },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
  if (!plan) {
    return (
      <Stack align="flex-start">
        <HintCard>No computation yet.</HintCard>
        <Button onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
          Compute Buy/Build List
        </Button>
      </Stack>
    )
  }
  if (buildList.length === 0) return <HintCard>Nothing to build - all stock targets are bought or covered.</HintCard>

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <Card withBorder padding="sm" w={280}>
            <Text size="xs" c="dimmed" tt="uppercase">Total Job Time</Text>
            <Title order={3} c="accent">{totalHours.toFixed(1)} h</Title>
          </Card>
          <MultiSelect
            label="Category" data={categories} value={selCategories} onChange={setSelCategories}
            placeholder="All" clearable w={280}
          />
        </Group>
        <Button variant="default" onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
          Recompute
        </Button>
      </Group>
      <Text size="xs" c="dimmed">{filtered.length} of {buildList.length} jobs</Text>
      {filtered.length === 0 ? (
        <HintCard>No jobs in the selected categories.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      )}
      <Text size="xs" c="dimmed">
        Decryptor 'None' means: no decryptor is the best choice (not 'no computation'). '–' = Tech I/Reaction
        (assumes perfect ME) or no invention recipe found. Override per item in the Stock Targets tab.
      </Text>
    </Stack>
  )
}
