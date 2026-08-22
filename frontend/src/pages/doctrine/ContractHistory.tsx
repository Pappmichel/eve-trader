import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { ContractHistoryRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { isk, dateTime } from '../../format'

// GitHub issue #19 - "which contracts were sold to who and when". Separate
// page/table from Contracts.tsx (the *active* snapshot) since a finished
// contract drops out of that one the moment it's accepted (see
// doctrine_contracts' own "no separate cleanup step needed" behavior) -
// this reads the independent, append-only doctrine_contract_history table
// instead, populated by the same Sync Contracts action in the sidebar.
export default function ContractHistory() {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['doctrine', 'contract-history'], queryFn: doctrineApi.contractHistory })

  const columns = useMemo<ColumnDef<ContractHistoryRow, any>[]>(() => [
    { header: 'Contract', accessorKey: 'title', size: 220, cell: (i) => i.getValue() ?? `#${i.row.original.contract_id}` },
    { header: 'Hull', accessorKey: 'hull_name', size: 160, cell: (i) => i.getValue() ?? '–' },
    { header: 'Fitting', accessorKey: 'fitting_name', size: 200, cell: (i) => i.getValue() ?? '–' },
    // source_role is an ESI-token role key ("doctrine:<character_id>") -
    // same resolved-name-with-raw-key-fallback pattern as Contracts.tsx.
    { header: 'Sold By', accessorKey: 'source_role', size: 140,
      cell: (i) => i.row.original.source_character_name ?? i.getValue() },
    { header: 'Sold To', accessorKey: 'acceptor_name', size: 160,
      cell: (i) => i.getValue() ?? (i.row.original.acceptor_id ? `#${i.row.original.acceptor_id}` : '–') },
    { header: 'Price', accessorKey: 'price', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Issued', accessorKey: 'date_issued', size: 180, cell: (i) => dateTime(i.getValue()) },
    { header: 'Completed', accessorKey: 'date_completed', size: 180, cell: (i) => dateTime(i.getValue()) },
  ], [])

  return (
    <Stack>
      <Title order={4}>Contract History</Title>
      <Text size="xs" c="dimmed">
        Every Doctrine contract that's actually been accepted, recorded permanently the moment Sync Contracts
        first sees it finish - unlike the Contracts tab's active snapshot, a completed sale never disappears
        from here.
      </Text>
      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={640} />
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={640} />
      ) : !data || data.length === 0 ? (
        <HintCard>No completed contracts recorded yet - they're added automatically the next time Sync Contracts runs after one finishes.</HintCard>
      ) : (
        <DataTable data={data} columns={columns} maxHeight={640} tableId="contract-history" exportFilename="contract-history" />
      )}
    </Stack>
  )
}
