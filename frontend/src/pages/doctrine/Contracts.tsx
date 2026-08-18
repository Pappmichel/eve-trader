import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Badge, Group, Select } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { DoctrineContractRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { isk, dateTime } from '../../format'

const STATUS_OPTIONS = ['valid', 'tolerable', 'invalid', 'unmatched']
const STATUS_COLOR: Record<string, string> = { valid: 'accent', tolerable: 'warn', invalid: 'danger', unmatched: 'dimmed' }

export default function Contracts() {
  const [status, setStatus] = useState<string | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['doctrine', 'contracts', status],
    queryFn: () => doctrineApi.contracts(undefined, status ?? undefined),
  })

  const columns = useMemo<ColumnDef<DoctrineContractRow, any>[]>(() => [
    { header: 'Contract', accessorKey: 'title', size: 220, cell: (i) => i.getValue() ?? `#${i.row.original.contract_id}` },
    { header: 'Status', accessorKey: 'validation_status', size: 180,
      cell: (i) => (
        <Group gap={4} wrap="nowrap">
          <Badge color={STATUS_COLOR[i.getValue() as string] ?? 'dimmed'} variant="light">{i.getValue()}</Badge>
          {/* Doesn't disappear from the snapshot on expiry anymore (see
              constants.SYNCABLE_CONTRACT_STATUSES's own comment) - this is
              what makes that visible instead of the contract just vanishing
              with no explanation. */}
          {i.row.original.status === 'expired' && <Badge color="dimmed" variant="outline">expired</Badge>}
        </Group>
      ) },
    { header: 'Source', accessorKey: 'source_role', size: 140 },
    { header: 'Corp?', accessorKey: 'for_corporation', size: 80, cell: (i) => (i.getValue() ? 'Yes' : 'No') },
    { header: 'Price', accessorKey: 'price', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Expires', accessorKey: 'date_expired', size: 180, cell: (i) => dateTime(i.getValue()) },
    { header: 'Match Score', accessorKey: 'match_score', size: 120, cell: (i) => (i.getValue() != null ? (i.getValue() as number).toFixed(2) : '–') },
  ], [])

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>Contracts</Title>
        <Select placeholder="All statuses" data={STATUS_OPTIONS} value={status} onChange={setStatus} clearable w={200} />
      </Group>
      <DataTable data={data ?? []} columns={columns} isLoading={isLoading} maxHeight={640} />
    </Stack>
  )
}
