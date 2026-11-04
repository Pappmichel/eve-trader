import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Group, Stack, Text, Title, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi, gateApi, productionApi } from '../../api/client'
import type { AssemblableRow, MaxBuildableResult } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

type ProductionCheck =
  | { state: 'loading' }
  | { state: 'done'; result: MaxBuildableResult }
  | { state: 'error'; message: string }

// Production's own Max buildable for one bottleneck item, on demand. The
// Production route checks its own grant; the button only shows with it.
function ProductionHint({ row }: { row: AssemblableRow }) {
  const [check, setCheck] = useState<ProductionCheck | null>(null)
  const run = async () => {
    setCheck({ state: 'loading' })
    try {
      const target = Math.max(1, Math.ceil(row.short_for_next))
      const result = await productionApi.maxBuildable(String(row.type_id), 'full', target)
      setCheck({ state: 'done', result })
    } catch (e) {
      setCheck({ state: 'error', message: e instanceof Error ? e.message : String(e) })
    }
  }
  if (check?.state === 'done') {
    const { result } = check
    const stop = result.bottlenecks[0]?.type_name
    return (
      <Tooltip label={stop ? `Production bottleneck: ${stop}` : 'Nothing on hand stops it'} disabled={!stop}>
        <Text span size="sm" c={result.max_quantity >= row.short_for_next ? 'accent' : 'dimmed'}>
          {' '}- Production can build {qty(result.max_quantity)} now
        </Text>
      </Tooltip>
    )
  }
  if (check?.state === 'error') {
    return <Text span size="sm" c="danger"> - {check.message}</Text>
  }
  return (
    <Button size="compact-xs" variant="subtle" ml={6} loading={check?.state === 'loading'} onClick={run}>
      Check Production
    </Button>
  )
}

function RequirementsTable({ rows }: { rows: AssemblableRow[] }) {
  const columns = useMemo<ColumnDef<AssemblableRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260, meta: { copyable: true } },
    { header: 'Section', accessorKey: 'slot_section', size: 200 },
    { header: 'Per Fit', accessorKey: 'per_set', size: 90, cell: (i) => qty(i.getValue()) },
    {
      header: 'On Hand', accessorKey: 'on_hand', size: 140,
      cell: (i) => {
        const variants = i.row.original.variant_on_hand ?? 0
        return variants > 0
          ? <span>{qty(i.getValue())} <Text span size="xs" c="dimmed">({qty(variants)} variant)</Text></span>
          : qty(i.getValue())
      },
    },
    { header: 'Fits', accessorKey: 'sets', size: 80, cell: (i) => qty(i.getValue()) },
    {
      header: 'Short For Next', accessorKey: 'short_for_next', size: 130,
      cell: (i) => (i.getValue() > 0 ? <Text size="sm" c="danger" fw={600}>{qty(i.getValue())}</Text> : '–'),
    },
    {
      header: 'Also Needed By', id: 'also_needed_by', size: 240, enableSorting: false,
      accessorFn: (r) => r.also_needed_by.join(', '),
    },
  ], [])
  return <DataTable data={rows} columns={columns} maxHeight={420} getRowId={(r) => String(r.type_id)} />
}

export function AssemblablePanel({ fittingId }: { fittingId: string }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['doctrine', 'assemblable', fittingId],
    queryFn: () => doctrineApi.assemblable(fittingId),
  })
  const gateStatus = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const tools = gateStatus.data?.tools
  const canProduction = tools === undefined || tools.includes('production')

  return (
    <Card withBorder>
      <Group justify="space-between" mb="sm">
        <Title order={6} c="dimmed" tt="uppercase">Assemblable now</Title>
        <Button size="compact-xs" variant="subtle" onClick={() => refetch()}>Recalculate</Button>
      </Group>
      {isLoading && <Text size="sm" c="dimmed">Counting stockpile…</Text>}
      {isError && <Text size="sm" c="danger">Could not count the stockpile.</Text>}
      {data && !data.assets_available && (
        <HintCard>No asset data yet - share Assets with Doctrine on the Characters page and run <b>Refresh what I need</b>.</HintCard>
      )}
      {data && data.assets_available && data.max_sets !== null && (
        <Stack gap="sm">
          <Group gap="sm" align="baseline">
            <Title order={2}>{qty(data.max_sets)}</Title>
            <Text size="lg">× {data.fitting_name}</Text>
            <Badge variant="outline" color="gray">
              Stockpile: {data.stockpile_locations.map((l) => l.name ?? String(l.location_id)).join(', ') || 'not set'}
            </Badge>
          </Group>
          <Text size="xs" c="dimmed">
            Complete fits from the stockpile alone, consumables at full quantity. This fitting gets every
            item first, even ones other fittings would claim. Nothing is built or bought.
          </Text>
          {data.bottlenecks.length > 0 && (
            <Alert color="danger" variant="light" title={`What stops fit ${qty(data.next_sets)}`}>
              <Stack gap={4}>
                {data.bottlenecks.map((row) => (
                  <Text size="sm" key={row.type_id}>
                    <b>{row.type_name}</b>: {qty(row.short_for_next)} short (on hand {qty(row.on_hand)},
                    {' '}{qty(row.per_set)} per fit)
                    {row.also_needed_by.length > 0 && (
                      <Text span size="sm" c="dimmed"> - also needed by {row.also_needed_by.join(', ')}</Text>
                    )}
                    {row.buildable && canProduction && <ProductionHint row={row} />}
                  </Text>
                ))}
              </Stack>
            </Alert>
          )}
          <RequirementsTable rows={data.requirements} />
        </Stack>
      )}
    </Card>
  )
}
