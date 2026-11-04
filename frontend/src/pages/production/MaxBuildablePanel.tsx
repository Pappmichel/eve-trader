import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Badge, Button, Card, Group, NumberInput, SegmentedControl, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { MaxBuildableBlueprint, MaxBuildableMode, MaxBuildableResult, MaxBuildableRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { qty } from '../../format'

// A request from elsewhere on the page (an order's line item) to check one item.
// `nonce` makes a repeated click on the same item run again.
export interface MaxBuildablePrefill {
  typeId: number
  nonce: number
}

const REASON_TEXT: Record<NonNullable<MaxBuildableRow['reason']>, string> = {
  material: 'not enough on hand',
  no_recipe: 'cannot be built',
  no_blueprint: 'no blueprint owned',
  blueprint_runs: 'not enough blueprint runs',
  not_built_direct_mode: 'not on hand (direct mode builds no components)',
}

function blueprintText(bp: MaxBuildableBlueprint): string {
  if (bp.source === 'original') return 'original owned'
  if (bp.source === 'copies') return `copies only, ${qty(bp.copy_runs)} runs`
  return 'not owned'
}

function reasonText(row: MaxBuildableRow): string {
  if (!row.reason) return ''
  const base = REASON_TEXT[row.reason]
  if (row.reason === 'blueprint_runs' && row.blueprint?.copy_runs != null) {
    return `${base} (${qty(row.blueprint.copy_runs)} available)`
  }
  return base
}

function RequirementsTable({ rows }: { rows: MaxBuildableRow[] }) {
  const columns = useMemo<ColumnDef<MaxBuildableRow, any>[]>(() => [
    {
      header: 'Item', accessorKey: 'type_name', size: 280, meta: { copyable: true },
      cell: (i) => (
        <Text size="sm" pl={i.row.original.depth * 14} fw={i.row.original.depth === 0 ? 700 : 400}>
          {i.getValue()}
        </Text>
      ),
    },
    { header: 'Needed', accessorKey: 'needed', size: 120, cell: (i) => qty(i.getValue()) },
    {
      header: 'On Hand', accessorKey: 'on_hand', size: 120,
      cell: (i) => (i.row.original.depth === 0 ? '–' : qty(i.getValue())),
    },
    { header: 'From Stock', accessorKey: 'from_stock', size: 120, cell: (i) => qty(i.getValue()) },
    {
      header: 'Built', accessorKey: 'built', size: 140,
      cell: (i) => (i.row.original.runs > 0 ? `${qty(i.getValue())} (${qty(i.row.original.runs)} runs)` : '–'),
    },
    {
      header: 'Short', accessorKey: 'short', size: 110,
      cell: (i) => (i.getValue() > 0 ? <Text size="sm" c="danger" fw={600}>{qty(i.getValue())}</Text> : '–'),
    },
    { header: 'Note', id: 'reason', size: 260, enableSorting: false, cell: (i) => reasonText(i.row.original) },
  ], [])
  return (
    <DataTable data={rows} columns={columns} maxHeight={420} getRowId={(row) => String(row.type_id)} />
  )
}

function ResultView({ result }: { result: MaxBuildableResult }) {
  const atMax = result.requirements_quantity === Math.max(result.max_quantity, 1) && result.max_quantity > 0
  return (
    <Stack gap="sm">
      <Group gap="sm" align="baseline">
        <Title order={2}>{qty(result.max_quantity)}</Title>
        <Text size="lg">× {result.type_name}</Text>
        <Badge variant="light" color={result.blueprint.source === 'none' ? 'danger' : 'gray'}>
          Blueprint: {blueprintText(result.blueprint)}
        </Badge>
        <Badge variant="outline" color="gray">{result.mode === 'full' ? 'full tree' : 'direct inputs'}</Badge>
      </Group>
      {result.material_max_quantity != null && result.material_max_quantity !== result.max_quantity && (
        <Text size="sm">
          Materials alone would allow <b>{qty(result.material_max_quantity)}</b> - the blueprint is what limits.
        </Text>
      )}
      {result.capped && (
        <Text size="sm" c="dimmed">Search limit reached - nothing on hand stops this quantity.</Text>
      )}

      {result.bottlenecks.length > 0 && (
        <Alert color="danger" variant="light" title={`Bottleneck - what stops unit ${qty(result.bottleneck_quantity)}`}>
          <Stack gap={4}>
            {result.bottlenecks.map((row) => (
              <Text size="sm" key={row.type_id}>
                <b>{row.type_name}</b>: {qty(row.short)} short of {qty(row.needed)}
                {row.depth > 0 && <> (on hand {qty(row.on_hand)})</>}
                {row.reason && <Text span c="dimmed" size="sm"> - {reasonText(row)}</Text>}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}

      <Text size="sm" fw={600}>
        Total requirement for {qty(result.requirements_quantity)}
        {atMax ? ' (the maximum)' : ''}
        {!result.requirements_feasible && <Text span c="danger" size="sm" fw={600}> - not buildable right now</Text>}
      </Text>
      <RequirementsTable rows={result.requirements} />
    </Stack>
  )
}

export function MaxBuildablePanel({ prefill }: { prefill: MaxBuildablePrefill | null }) {
  const { data: itemNameOptions } = useItemNameOptions()
  const options = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [typeId, setTypeId] = useState<string | null>(null)
  const [mode, setMode] = useState<MaxBuildableMode>('full')
  const [target, setTarget] = useState<number | ''>('')
  const [result, setResult] = useState<MaxBuildableResult | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  const compute = useAction('Max Buildable',
    (args: { item: string; mode: MaxBuildableMode; target: number | null }) =>
      productionApi.maxBuildable(args.item, args.mode, args.target), [],
    { tier: 'local', effect: 'Checks stock on hand and owned blueprints. Reads synced data only, buys nothing.' })

  const run = (item: string, runMode: MaxBuildableMode, runTarget: number | '') => {
    compute.mutate(
      { item, mode: runMode, target: runTarget === '' ? null : Number(runTarget) },
      { onSuccess: (r) => setResult(r) },
    )
  }

  useEffect(() => {
    if (!prefill) return
    const id = String(prefill.typeId)
    setTypeId(id)
    setTarget('')
    cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    run(id, mode, '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill])

  return (
    <Card withBorder ref={cardRef}>
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">Max Buildable Now</Title>
      <Text size="xs" c="dimmed" mb="sm">
        How many of one item you can build right now from stock on hand (running jobs not counted). This item gets
        top priority: stock the Build List or other orders would reserve counts as free. Nothing is bought. Owned
        blueprint copies limit the result when no original is owned.
      </Text>
      <Group align="flex-end">
        <SearchableSelect label="Item name" placeholder="Search item…" data={options}
          value={typeId} onChange={setTypeId} w={320} />
        <Stack gap={4}>
          <Text size="sm" fw={500}>Components</Text>
          <SegmentedControl
            value={mode}
            onChange={(v) => setMode(v as MaxBuildableMode)}
            data={[
              { label: 'Build from inputs', value: 'full' },
              { label: 'Only on hand', value: 'direct' },
            ]}
          />
        </Stack>
        <NumberInput label="Requirements for (optional)" placeholder="max" value={target} min={1} w={190}
          onChange={(v) => setTarget(v === '' ? '' : Number(v))} />
        <Tooltip label={compute.tooltip} disabled={!compute.tooltip} multiline w={280}>
          <Button leftSection={compute.tierIcon} loading={compute.isPending} disabled={!typeId}
            onClick={() => typeId && run(typeId, mode, target)}>
            Compute
          </Button>
        </Tooltip>
      </Group>
      {result && <Stack mt="md"><ResultView result={result} /></Stack>}
    </Card>
  )
}
