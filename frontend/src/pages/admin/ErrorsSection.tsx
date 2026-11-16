import { useMemo, useState } from 'react'
import { SegmentedControl, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'

import { adminApi } from '../../api/client'
import type { ErrorLogGroup, ErrorLogRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { dateTime } from '../../format'

// GitHub issue #88: self-hosted error tracking - ErrorBoundary.tsx and
// main.tsx's global error/unhandled-rejection listeners report here, this
// is the only place that ever reads it back out. Cross-tenant (error_log
// is deliberately unscoped, see docs/observability_schema.sql). Reports are
// tenant-supplied text: everything below renders as plain React text.
export function ErrorsSection() {
  const [view, setView] = useState<'grouped' | 'raw'>('grouped')
  const { data: groups, isLoading: groupsLoading } = useQuery({
    queryKey: ['admin', 'error-groups'], queryFn: () => adminApi.errorGroups(), enabled: view === 'grouped',
  })
  const { data: errorRows, isLoading: rowsLoading } = useQuery({
    queryKey: ['admin', 'errors'], queryFn: () => adminApi.errors(), enabled: view === 'raw',
  })

  const groupColumns = useMemo<ColumnDef<ErrorLogGroup, any>[]>(() => [
    { header: 'Last seen', accessorKey: 'last_seen', size: 170, cell: (i) => dateTime(i.getValue()) },
    { header: 'First seen', accessorKey: 'first_seen', size: 170, cell: (i) => dateTime(i.getValue()) },
    { header: 'Count', accessorKey: 'count', size: 80 },
    { header: 'Tenants', accessorKey: 'tenant_count', size: 80 },
    { header: 'Source', accessorKey: 'source', size: 150 },
    { header: 'Message', accessorKey: 'message', size: 360 },
    { header: 'Latest page', accessorKey: 'path', size: 200, cell: (i) => i.getValue() ?? '—' },
    { header: 'Latest detail', accessorKey: 'detail', size: 300, cell: (i) => i.getValue() ?? '—' },
  ], [])

  const rowColumns = useMemo<ColumnDef<ErrorLogRow, any>[]>(() => [
    { header: 'When', accessorKey: 'created_at', size: 170, cell: (i) => dateTime(i.getValue()) },
    { header: 'Source', accessorKey: 'source', size: 170 },
    { header: 'Message', accessorKey: 'message', size: 360 },
    { header: 'Page', accessorKey: 'path', size: 200, cell: (i) => i.getValue() ?? '—' },
    { header: 'Tenant', accessorKey: 'tenant_id', size: 280, cell: (i) => i.getValue() ?? '—' },
  ], [])

  const grouped = view === 'grouped'
  const isLoading = grouped ? groupsLoading : rowsLoading
  const count = (grouped ? groups : errorRows)?.length ?? 0

  return (
    <div>
      <Title order={4} mb="xs">Recent Errors</Title>
      <Text size="sm" c="dimmed" mb="sm">
        Frontend errors reported across every tenant (a render crash, an uncaught exception, or an unhandled
        promise rejection) - best-effort, never blocks the page that hit the error. Grouped: the same message
        with ids and numbers ignored counts once. Each tenant keeps its latest 500 reports.
      </Text>
      <SegmentedControl
        size="xs" mb="sm" value={view} onChange={(v) => setView(v as 'grouped' | 'raw')}
        data={[{ value: 'grouped', label: 'Grouped' }, { value: 'raw', label: 'Latest 200' }]}
      />
      {!isLoading && count === 0 && <Text c="dimmed" size="sm">No errors reported yet.</Text>}
      {(isLoading || count > 0) && grouped && (
        <DataTable
          data={groups ?? []} columns={groupColumns} tableId="admin-error-groups" exportFilename="error-groups"
          getRowId={(r) => `${r.source}|${r.message}`} isLoading={isLoading} maxHeight={400}
        />
      )}
      {(isLoading || count > 0) && !grouped && (
        <DataTable
          data={errorRows ?? []} columns={rowColumns} tableId="admin-errors" exportFilename="errors"
          getRowId={(r) => String(r.id)} isLoading={isLoading} maxHeight={400}
        />
      )}
    </div>
  )
}
