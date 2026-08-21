import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Text, Stack, Card, Title, Group, MultiSelect } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { IndustryJobRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { dateTime, duration, isk, qty } from '../../format'

// Jobs finishing within this window get highlighted, so free slots waiting
// to be refilled don't go unnoticed.
const SOON_THRESHOLD_SECONDS = 2 * 60 * 60

// Value KPI cards are always these two + their sum, regardless of the
// Activity filter below (which is a separate, independent view of the
// table rows - Invention/Copying/TE-ME Research jobs have no product, so
// they never contribute a value either way, see IndustryJobRow.output_value).
function sumValue(jobs: IndustryJobRow[], activity: string): number {
  return jobs.filter((j) => j.activity === activity)
    .reduce((sum, j) => sum + (j.output_value ?? 0), 0)
}

export default function Jobs() {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['production', 'jobs'], queryFn: productionApi.jobs })
  const jobs = data ?? []

  const activities = useMemo(() => [...new Set(jobs.map((j) => j.activity))].sort(), [jobs])
  const [selActivities, setSelActivities] = useState<string[]>([])
  const filtered = useMemo(() => {
    if (selActivities.length === 0) return jobs
    return jobs.filter((j) => selActivities.includes(j.activity))
  }, [jobs, selActivities])

  const manufacturingValue = useMemo(() => sumValue(jobs, 'Manufacturing'), [jobs])
  const reactionValue = useMemo(() => sumValue(jobs, 'Reaction'), [jobs])

  const columns = useMemo<ColumnDef<IndustryJobRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    { header: 'Activity', accessorKey: 'activity', size: 130 },
    { header: 'Runs', accessorKey: 'runs', size: 90, cell: (i) => qty(i.getValue()) },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
    {
      header: 'Output Value', accessorKey: 'output_value', size: 140,
      cell: (i) => { const v = i.getValue() as number | null; return v === null ? '–' : isk(v) },
    },
    { header: 'Started', accessorKey: 'start_date', size: 150, cell: (i) => dateTime(i.getValue()) },
    { header: 'Finishes At', accessorKey: 'end_date', size: 150, cell: (i) => dateTime(i.getValue()) },
    {
      header: 'Remaining', accessorKey: 'remaining_seconds', size: 120,
      cell: (i) => {
        const seconds = i.getValue() as number | null
        if (seconds === null) return duration(seconds)
        if (seconds >= 0 && seconds <= SOON_THRESHOLD_SECONDS) {
          return <Badge color="warn" variant="light">{duration(seconds)}</Badge>
        }
        return duration(seconds)
      },
    },
    { header: 'Status', accessorKey: 'status', size: 100 },
    { header: 'Installer', accessorKey: 'installer_name', size: 150 },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
  if (!data || data.length === 0) {
    return <HintCard>No active industry jobs - or not synced yet ('Sync ESI Data' in the sidebar).</HintCard>
  }

  const soonCount = jobs.filter((j) => j.remaining_seconds !== null && j.remaining_seconds >= 0
    && j.remaining_seconds <= SOON_THRESHOLD_SECONDS).length

  return (
    <Stack>
      {soonCount > 0 && (
        <HintCard>{soonCount} {soonCount === 1 ? 'job finishes' : 'jobs finish'} within the next 2 hours.</HintCard>
      )}
      <Group align="flex-end" justify="space-between">
        <Group align="flex-end">
          <Card withBorder padding="sm" w={200}>
            <Text size="xs" c="dimmed" tt="uppercase">Manufacturing Value</Text>
            <Title order={3} c="accent">{isk(manufacturingValue)}</Title>
          </Card>
          <Card withBorder padding="sm" w={200}>
            <Text size="xs" c="dimmed" tt="uppercase">Reactions Value</Text>
            <Title order={3} c="accent">{isk(reactionValue)}</Title>
          </Card>
          <Card withBorder padding="sm" w={200}>
            <Text size="xs" c="dimmed" tt="uppercase">Combined</Text>
            <Title order={3} c="accent">{isk(manufacturingValue + reactionValue)}</Title>
          </Card>
        </Group>
        <MultiSelect
          label="Activity" data={activities} value={selActivities} onChange={setSelActivities}
          placeholder="All" clearable w={280}
        />
      </Group>
      <Text size="xs" c="dimmed">{filtered.length} of {jobs.length} jobs</Text>
      {filtered.length === 0 ? (
        <HintCard>No jobs match the selected activity.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} />
      )}
      <Text size="xs" c="dimmed">
        Every job shown individually, even if several jobs build the same item - sorted by 'finishes next'.
        "Output Value" is the finished output's quantity priced at its C-J sell quote (falling back to Jita) -
        same pricing "Stock Value" on the Stock Targets tab uses, "–" if there's no product (research/copying
        jobs) or no sell quote anywhere right now. The Manufacturing/Reactions/Combined value cards above always
        total *all* jobs of that activity, independent of the Activity filter, which only changes what the table
        below shows.
      </Text>
    </Stack>
  )
}
