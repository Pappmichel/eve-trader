import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ActionIcon, Badge, Button, Card, Group, MultiSelect, NumberInput, Select, Stack, Text, Title } from '@mantine/core'
import { DateTimePicker } from '@mantine/dates'
import { modals } from '@mantine/modals'
import { IconCheck, IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { IndustryJobRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { LocationPicker } from '../../components/LocationPicker'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { dateTime, duration, isk, qty } from '../../format'

const JOBS_KEY = [['production', 'jobs']]

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
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'jobs'], queryFn: productionApi.jobs })
  const jobs = data ?? []

  const activities = useMemo(() => [...new Set(jobs.map((j) => j.activity))].sort(), [jobs])
  const [selActivities, setSelActivities] = useState<string[]>([])
  const filtered = useMemo(() => {
    if (selActivities.length === 0) return jobs
    return jobs.filter((j) => selActivities.includes(j.activity))
  }, [jobs, selActivities])

  const manufacturingValue = useMemo(() => sumValue(jobs, 'Manufacturing'), [jobs])
  const reactionValue = useMemo(() => sumValue(jobs, 'Reaction'), [jobs])

  const [pendingCompleteId, setPendingCompleteId] = useState<number | null>(null)
  const completeManual = useAction(
    'Complete Manual Job',
    (args: { manualId: number; locationId: number | null }) =>
      productionApi.completeManualIndustryJob(args.manualId, args.locationId),
    JOBS_KEY,
  )
  const [pendingRemoveId, setPendingRemoveId] = useState<number | null>(null)
  const removeManual = useAction('Remove Manual Job', productionApi.removeManualIndustryJob, JOBS_KEY)

  const columns = useMemo<ColumnDef<IndustryJobRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 200 },
    { header: 'Activity', accessorKey: 'activity', size: 120 },
    {
      header: 'Source', accessorKey: 'source', size: 90,
      cell: (i) => <Badge color={i.getValue() === 'manual' ? 'warn' : 'gray'} variant="light">{i.getValue()}</Badge>,
    },
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
    {
      header: 'Status', accessorKey: 'status', size: 100,
      cell: (i) => (i.getValue() === 'ready'
        ? <Badge color="accent" variant="light">Done</Badge>
        : i.getValue()),
    },
    { header: 'Installer', accessorKey: 'installer_name', size: 150 },
    {
      header: '', id: 'actions', size: 130, enableSorting: false,
      cell: (i) => {
        if (i.row.original.source !== 'manual' || i.row.original.manual_id === null) return null
        const manualId = i.row.original.manual_id
        return (
          <Group gap={4} wrap="nowrap">
            <Button size="xs" variant="light" color="accent"
              loading={completeManual.isPending && pendingCompleteId === manualId}
              onClick={() => {
                let overrideLocation: number | null = null
                modals.openConfirmModal({
                  title: 'Complete manual job',
                  children: (
                    <Stack gap="xs">
                      <Text size="sm">
                        Deletes the job and adds its quantity to manual stock. Defaults to the job&apos;s own
                        output location - pick a different one only if the goods actually ended up elsewhere.
                      </Text>
                      <LocationPicker label="Target location (optional override)" value={null}
                        onChange={(v) => { overrideLocation = v }} allowNone />
                    </Stack>
                  ),
                  labels: { confirm: 'Complete', cancel: 'Cancel' },
                  confirmProps: { color: 'accent' },
                  onConfirm: () => {
                    setPendingCompleteId(manualId)
                    completeManual.mutate({ manualId, locationId: overrideLocation })
                  },
                })
              }}>
              <IconCheck size={14} />
            </Button>
            <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Remove manual job for ${i.row.original.type_name}`}
              onClick={() => modals.openConfirmModal({
                title: 'Remove manual job',
                children: <Text size="sm">Remove this manual job entry for {i.row.original.type_name}?</Text>,
                labels: { confirm: 'Remove', cancel: 'Cancel' },
                confirmProps: { color: 'danger' },
                onConfirm: () => { setPendingRemoveId(manualId); removeManual.mutate(manualId) },
              })}
              loading={removeManual.isPending && pendingRemoveId === manualId}>
              <IconTrash size={14} />
            </ActionIcon>
          </Group>
        )
      },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [completeManual, pendingCompleteId, removeManual, pendingRemoveId])

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
      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
      ) : !data || data.length === 0 ? (
        <HintCard>No active industry jobs - or not synced yet (&apos;Refresh what I need&apos; in the sidebar).</HintCard>
      ) : (
        <>
          <Text size="xs" c="dimmed">{filtered.length} of {jobs.length} jobs</Text>
          {filtered.length === 0 ? (
            <HintCard>No jobs match the selected activity.</HintCard>
          ) : (
            <DataTable data={filtered} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt}
              getRowId={(r) => `${r.source}:${r.job_id}`} />
          )}
        </>
      )}
      <Text size="xs" c="dimmed">
        Every job shown individually, even if several jobs build the same item - sorted by 'finishes next'.
        "Output Value" is the finished output's quantity priced at its C-J sell quote (falling back to Jita) -
        same pricing "Stock Value" on the Stock Targets tab uses, "–" if there's no product (research/copying
        jobs) or no sell quote anywhere right now. The Manufacturing/Reactions/Combined value cards above always
        total *all* jobs of that activity, independent of the Activity filter, which only changes what the table
        below shows.
      </Text>

      <ManualIndustryJobFormSection />
    </Stack>
  )
}

// docs/MANUAL_TRACKING_PLAN.md phase 6 - a job not tracked via ESI, entered
// as either a run count (converted server-side to a quantity via the
// product's own qty-per-run) or a raw quantity directly. The resulting row
// shows up in the table above (source: 'manual'), not in a separate table.
function ManualIndustryJobFormSection() {
  const addManual = useAction(
    'Add Manual Job',
    (args: {
      itemName: string; quantity: number | null; runs: number | null
      locationId: number; readyAt: string | null
    }) => productionApi.addManualIndustryJob({
      item_name: args.itemName, quantity: args.quantity, runs: args.runs,
      location_id: args.locationId, ready_at: args.readyAt,
    }),
    JOBS_KEY,
  )

  const { data: itemNameOptions } = useItemNameOptions()
  const jobItemOptions = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [itemId, setItemId] = useState<string | null>(null)
  const [mode, setMode] = useState<'runs' | 'units'>('runs')
  const [amount, setAmount] = useState<number | ''>('')
  const [locationId, setLocationId] = useState<number | null>(0)
  const [readyAt, setReadyAt] = useState<Date | null>(null)

  const canAdd = itemId && amount !== ''

  return (
    <div>
      <Title order={5} mb="xs">Manual Industry Jobs</Title>
      <Text size="sm" c="dimmed" mb="sm">
        A running job not tracked via ESI - enter it as a run count (converted to a quantity automatically) or a
        raw unit quantity directly.
      </Text>

      <Card withBorder mb="sm">
        <Group grow align="flex-end">
          <SearchableSelect label="Item" placeholder="Search item…" data={jobItemOptions} value={itemId} onChange={setItemId} />
          <Select label="Enter as" data={[{ value: 'runs', label: 'Runs' }, { value: 'units', label: 'Units' }]}
            value={mode} onChange={(v) => setMode((v as 'runs' | 'units') ?? 'runs')} allowDeselect={false} />
          <NumberInput label={mode === 'runs' ? 'Runs' : 'Quantity'} value={amount}
            onChange={(v) => setAmount(v === '' ? '' : Number(v))} min={1} />
          <LocationPicker label="Output location" value={locationId} onChange={setLocationId} allowNone />
          <DateTimePicker label="Ready at (optional)" value={readyAt} onChange={(v) => setReadyAt(v ? new Date(v) : null)} clearable />
          <Button
            disabled={!canAdd}
            loading={addManual.isPending}
            onClick={() => addManual.mutate(
              {
                itemName: jobItemOptions.find((o) => o.value === itemId)?.label ?? '',
                quantity: mode === 'units' ? Number(amount) : null,
                runs: mode === 'runs' ? Number(amount) : null,
                locationId: locationId ?? 0,
                readyAt: readyAt ? readyAt.toISOString() : null,
              },
              { onSuccess: () => { setItemId(null); setAmount(''); setLocationId(0); setReadyAt(null) } },
            )}
          >
            Add
          </Button>
        </Group>
      </Card>
    </div>
  )
}
