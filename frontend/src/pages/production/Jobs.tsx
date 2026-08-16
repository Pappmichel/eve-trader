import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Text, Stack } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { IndustryJobRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { dateTime, duration, qty } from '../../format'

// Jobs finishing within this window get highlighted, so free slots waiting
// to be refilled don't go unnoticed.
const SOON_THRESHOLD_SECONDS = 2 * 60 * 60

export default function Jobs() {
  const { data, isLoading } = useQuery({ queryKey: ['production', 'jobs'], queryFn: productionApi.jobs })

  const columns = useMemo<ColumnDef<IndustryJobRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    { header: 'Activity', accessorKey: 'activity', size: 130 },
    { header: 'Runs', accessorKey: 'runs', size: 90, cell: (i) => qty(i.getValue()) },
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
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
  if (!data || data.length === 0) {
    return <HintCard>No active industry jobs - or not synced yet ('Sync ESI Data' in the sidebar).</HintCard>
  }

  const soonCount = data.filter((j) => j.remaining_seconds !== null && j.remaining_seconds >= 0
    && j.remaining_seconds <= SOON_THRESHOLD_SECONDS).length

  return (
    <Stack>
      {soonCount > 0 && (
        <HintCard>{soonCount} {soonCount === 1 ? 'job finishes' : 'jobs finish'} within the next 2 hours.</HintCard>
      )}
      <DataTable data={data} columns={columns} maxHeight={560} />
      <Text size="xs" c="dimmed">
        Every job shown individually, even if several jobs build the same item - sorted by 'finishes next'.
      </Text>
    </Stack>
  )
}
