import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Anchor, Badge, Group, NumberInput, Stack, Text, Title } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'
import { useNavigate } from 'react-router-dom'

import { doctrineApi } from '../../api/client'
import type { ReserveFittingRow, ReserveMissingRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { qty } from '../../format'

const QUERY_KEY = ['doctrine', 'strategic-reserve']

function TargetInput({ row }: { row: ReserveFittingRow }) {
  const save = useAction(
    'Set reserve target',
    (target: number) => doctrineApi.updateFitting(row.fitting_id, { reserve_target: target }),
    [QUERY_KEY],
  )
  return (
    <NumberInput size="xs" w={90} min={0} allowDecimal={false} defaultValue={row.reserve_target}
      aria-label={`Reserve target for ${row.fitting_name}`}
      onBlur={(e) => {
        const next = Number(e.currentTarget.value.replace(/,/g, '') || 0)
        if (Number.isInteger(next) && next >= 0 && next !== row.reserve_target) save.mutate(next)
      }} />
  )
}

export default function StrategicReserve() {
  const navigate = useNavigate()
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: QUERY_KEY, queryFn: doctrineApi.strategicReserve,
  })

  const fittingColumns = useMemo<ColumnDef<ReserveFittingRow, any>[]>(() => [
    { header: 'Doctrine', accessorKey: 'doctrine_name', size: 180 },
    { header: 'Fitting', accessorKey: 'fitting_name', size: 240, meta: { copyable: true } },
    { header: 'Hull', accessorKey: 'hull_name', size: 150 },
    {
      header: 'Target', accessorKey: 'reserve_target', size: 110,
      cell: (i) => <TargetInput key={i.row.original.reserve_target} row={i.row.original} />,
    },
    {
      header: 'In Reserve', accessorKey: 'complete_sets', size: 110,
      cell: (i) => {
        const { complete_sets: sets, reserve_target: target } = i.row.original
        if (sets === null) return '–'
        return <Text size="sm" c={sets < target ? 'danger' : undefined} fw={sets < target ? 600 : undefined}>{qty(sets)}</Text>
      },
    },
    {
      header: 'Missing Units', accessorKey: 'missing_units', size: 120,
      cell: (i) => (i.getValue() ? qty(i.getValue()) : '–'),
    },
  ], [])

  const missingColumns = useMemo<ColumnDef<ReserveMissingRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 280, meta: { copyable: true, exportRole: 'item' as const } },
    {
      header: 'Missing', accessorKey: 'missing', size: 110, meta: { exportRole: 'qty' as const },
      cell: (i) => <Text size="sm" c="danger" fw={600}>{qty(i.getValue())}</Text>,
    },
    { header: 'Required', accessorKey: 'required', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'On Hand', accessorKey: 'on_hand', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Used In', accessorKey: 'fitting_count', size: 110, cell: (i) => `${i.getValue()} fitting${i.getValue() === 1 ? '' : 's'}` },
  ], [])

  const hangar = data?.hangar
  // Fittings with a target first; the sort keeps doctrine order inside each part.
  const fittings = useMemo(() => {
    const all = data?.fittings ?? []
    return [...all.filter((f) => f.reserve_target > 0), ...all.filter((f) => f.reserve_target === 0)]
  }, [data])
  return (
    <Stack>
      <Title order={4}>Strategic Reserve</Title>
      <Text size="sm" c="dimmed">
        Fits to hold in one hangar. Production and Trading treat everything in that hangar as if it did not exist, and
        it does not count as Doctrine stockpile. Fittings draw on it in doctrine order, so a shared module counts once.
      </Text>

      {data?.state === 'not_configured' && (
        <HintCard>
          No reserve hangar set yet.{' '}
          <Anchor size="sm" onClick={() => navigate('/doctrine/settings')}>Choose one in Settings</Anchor>.
          Targets you enter below are kept.
        </HintCard>
      )}
      {data?.state === 'not_shared' && (
        <Alert color="warn" variant="light" title="Hangar owner not shared">
          The owner of this hangar does not share Assets with Doctrine, so nothing can be counted. Share it on the
          Characters page. Production and Trading already ignore the hangar.
        </Alert>
      )}
      {hangar && (
        <Group gap="xs">
          <Badge variant="outline" color="gray" style={{ textTransform: 'none' }}>
            {hangar.hangar_flag} at {hangar.location_name ?? hangar.location_id}
          </Badge>
          <Badge variant="outline" color="gray" style={{ textTransform: 'none' }}>
            Hulls {data?.include_hulls ? 'included' : 'not included'}
          </Badge>
        </Group>
      )}

      {(data?.below_target.length ?? 0) > 0 && (
        <Alert color="warn" variant="light" title="Below target">
          <Stack gap={2}>
            {data?.below_target.map((row) => (
              <Text size="sm" key={row.fitting_id}>
                <b>{row.fitting_name}</b>: {qty(row.complete_sets)} of {qty(row.reserve_target)} fits
              </Text>
            ))}
          </Stack>
        </Alert>
      )}
      {(data?.assembled_ships_not_counted ?? 0) > 0 && (
        <Text size="xs" c="dimmed">
          {qty(data?.assembled_ships_not_counted)} assembled ship(s) in this hangar are not counted, only packaged hulls and loose items are.
        </Text>
      )}

      <DataTable
        rowDetail
        data={fittings}
        columns={fittingColumns}
        tableId="doctrine-strategic-reserve"
        exportFilename="doctrine-strategic-reserve"
        getRowId={(r) => r.fitting_id}
        isLoading={isLoading}
        isError={isError}
        onRetry={() => refetch()}
        dataUpdatedAt={dataUpdatedAt}
      />

      {(data?.missing.length ?? 0) > 0 && (
        <div>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">Missing Across All Targets</Title>
          <DataTable
            rowDetail
            data={data?.missing ?? []}
            columns={missingColumns}
            tableId="doctrine-strategic-reserve-missing"
            exportFilename="doctrine-strategic-reserve-missing"
            getRowId={(r) => String(r.type_id)}
          />
        </div>
      )}
    </Stack>
  )
}
