import { useQuery } from '@tanstack/react-query'
import { Stack, Text } from '@mantine/core'

import { sortingApi } from '../../api/client'
import type { SortingRow } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { SortingTable } from './SortingTable'

const EMPTY_ROWS: SortingRow[] = []

export default function SortingOverview() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['sorting', 'sorting-list'], queryFn: sortingApi.sortingList,
  })
  const rows = data?.rows ?? EMPTY_ROWS

  return (
    <Stack>
      <HintCard>
        Read-only. EVE has no concept of reserving a stack for a tool — if several pots want more than is
        sitting in the intake, you still decide by hand where each unit goes. Configure intake hangars on
        Settings. &quot;Reprocess&quot; marks stacks worth more reprocessed than sold as-is at C-J (same check
        as Ore &amp; Minerals&apos; Reprocessing page).
      </HintCard>
      {data?.reprocess_error && (
        <Text size="xs" c="warn">Reprocess check unavailable: {data.reprocess_error}</Text>
      )}
      <SortingTable
        rows={rows}
        tableId="sorting-overview"
        exportFilename="sorting-overview"
        isLoading={isLoading}
        isError={isError}
        onRetry={() => refetch()}
        dataUpdatedAt={dataUpdatedAt}
        emptyLabel="Nothing to sort right now — either no intake sources are configured (Settings), or they are currently empty."
      />
    </Stack>
  )
}
