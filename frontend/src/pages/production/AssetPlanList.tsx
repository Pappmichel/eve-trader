import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, MultiSelect, Badge } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { AssetPlanJob } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'

const CATEGORY_UNKNOWN = 'no category'

export default function AssetPlanList() {
  const { data: plan, isLoading, dataUpdatedAt } = useQuery({ queryKey: ['production', 'asset-plan'], queryFn: productionApi.assetPlan })
  const jobs = plan?.jobs ?? []

  const refreshAssetPlan = useAction('Refresh Asset Build List', productionApi.refreshAssetPlan, [
    ['production', 'asset-plan'],
  ])

  const categories = useMemo(
    () => [...new Set(jobs.map((j) => j.job_category ?? CATEGORY_UNKNOWN))].sort(), [jobs],
  )
  const [selCategories, setSelCategories] = useState<string[]>([])
  const filtered = useMemo(() => {
    if (selCategories.length === 0) return jobs
    return jobs.filter((j) => selCategories.includes(j.job_category ?? CATEGORY_UNKNOWN))
  }, [jobs, selCategories])

  const readyRuns = useMemo(() => filtered.reduce((sum, j) => sum + j.runs_ready_now, 0), [filtered])
  const totalRuns = useMemo(() => filtered.reduce((sum, j) => sum + j.job_runs, 0), [filtered])
  const readyJobs = useMemo(() => filtered.filter((j) => j.runs_ready_now > 0).length, [filtered])

  const columns = useMemo<ColumnDef<AssetPlanJob, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 220 },
    { header: 'Category', accessorKey: 'job_category', size: 160, cell: (i) => i.getValue() ?? '–' },
    { header: 'Activity', accessorKey: 'activity', size: 130 },
    { header: 'Job Runs (total)', accessorKey: 'job_runs', size: 140, cell: (i) => qty(i.getValue()) },
    {
      header: 'Ready Now', accessorKey: 'runs_ready_now', size: 120,
      cell: (i) => <Text c={i.getValue() > 0 ? 'accent' : 'dimmed'} fw={i.getValue() > 0 ? 600 : 400}>{qty(i.getValue())}</Text>,
    },
    {
      header: 'Blocked', id: 'blocked', size: 110, accessorFn: (r) => r.job_runs - r.runs_ready_now,
      cell: (i) => (i.getValue() as number) > 0
        ? <Text c="warn">{qty(i.getValue() as number)}</Text>
        : qty(0),
    },
    {
      header: 'Stock Coverage', accessorKey: 'stock_coverage', size: 140,
      cell: (i) => {
        const v = i.getValue() as number | null
        if (v === null) return '–'
        const color = v >= 0.5 ? 'accent' : v > 0 ? 'warn' : 'danger'
        return <Badge color={color} variant="light">{(v * 100).toFixed(0)}%</Badge>
      },
    },
    {
      header: 'Split Into', accessorKey: 'recommended_slots', size: 170,
      cell: (i) => {
        const row = i.row.original
        const n = i.getValue() as number | null
        if (n === null || n <= 0) return '–'
        const perSlot = Math.ceil(row.runs_ready_now / n)
        return <Text size="sm">{n} slot{n === 1 ? '' : 's'} (~{qty(perSlot)} each)</Text>
      },
    },
    { header: 'Quantity (Output)', accessorKey: 'quantity', size: 140, cell: (i) => qty(i.getValue()) },
    { header: 'Job Time (h, total)', id: 'hours', size: 140, accessorFn: (r) => r.job_time_seconds / 3600, cell: (i) => (i.getValue() as number).toFixed(2) },
    { header: 'Modeled Unit Cost', accessorKey: 'unit_build_cost', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: 'Margin', accessorKey: 'margin', size: 110,
      cell: (i) => {
        const v = i.getValue()
        return v === null ? '–' : <Text c={v > 0 ? 'accent' : undefined}>{pct(v)}</Text>
      },
    },
    { header: 'Decryptor', accessorKey: 'decryptor', size: 130, cell: (i) => i.getValue() ?? '–' },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (!plan) {
    return (
      <Stack align="flex-start">
        <HintCard>No computation yet.</HintCard>
        <Button onClick={() => refreshAssetPlan.mutate()} loading={refreshAssetPlan.isPending}>
          Compute Asset Build List
        </Button>
      </Stack>
    )
  }
  if (jobs.length === 0) return <HintCard>Nothing to build - all stock targets are bought or covered.</HintCard>

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <Card withBorder padding="sm" w={220}>
            <Text size="xs" c="dimmed" tt="uppercase">Runs Ready Now</Text>
            <Title order={3} c="accent">{readyRuns} / {totalRuns}</Title>
          </Card>
          <Card withBorder padding="sm" w={220}>
            <Text size="xs" c="dimmed" tt="uppercase">Jobs With Runs Ready Now</Text>
            <Title order={3} c="accent">{readyJobs} / {filtered.length}</Title>
          </Card>
          <MultiSelect
            label="Category" data={categories} value={selCategories} onChange={setSelCategories}
            placeholder="All" clearable w={280}
          />
        </Group>
        <Button variant="default" onClick={() => refreshAssetPlan.mutate()} loading={refreshAssetPlan.isPending}>
          Recompute
        </Button>
      </Group>
      <Text size="xs" c="dimmed">{filtered.length} of {jobs.length} jobs</Text>
      {filtered.length === 0 ? (
        <HintCard>No jobs in the selected categories.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      )}
      <Text size="xs" c="dimmed">
        Unlike the regular build list, this one checks the actual asset stock at <b>every</b> level of the build
        chain (not just the end product) - including materials several jobs need at the same time. If a material
        is scarce, the jobs with the smallest requirement get fully restocked first, so as many jobs as possible
        are completely (not just partially) ready to start right away. "Ready now" = how many runs of this job you
        can queue in-game right now without waiting on another intermediate product - the rest of "Job Runs (total)"
        is still blocked. "Stock Coverage" is how much of <i>this item itself</i> is already on hand relative to
        what's currently wanted - for a stock target that's its backup/home/Jita goal, for a pure intermediate
        component (no goal of its own) it's this round's pooled demand instead. Distinct from "Blocked", which is
        about whether <i>this item's own materials</i> are available to build it, not about this item's own stock.
        Click the column header to sort by it if you want to see what's closest to running out first; it doesn't
        affect the list's own sort order or which jobs get queued. "Split Into" (Reactions/Advanced Components/
        Capital Components only) recommends how many of your currently-free character job slots to queue this
        job's ready runs across in parallel, instead of one long serial batch - your free slots for a category are
        split across every ready job sharing that category at once, weighted by how much job time each job's ready
        runs actually need (not just how many runs), so a job with fewer but much longer runs gets more slots than
        a job with lots of quick ones - the numbers across all of them add up to your real total free slots, not
        each job assuming it gets the whole pool to itself.
      </Text>
    </Stack>
  )
}
