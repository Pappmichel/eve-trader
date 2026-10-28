import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, MultiSelect, Badge, NumberInput, SegmentedControl, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { AssetPlanJob } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { StalePlanAlert } from './StalePlanAlert'
import ReactionCapacityPanel from './ReactionCapacityPanel'
import { useAction } from '../../hooks/useAction'
import { duration, isk, pct, qty } from '../../format'
import { blockedRunsTitle } from './assetPlanBlockers'

const CATEGORY_UNKNOWN = 'no category'
const TECH_LEVEL_UNKNOWN = 'unknown'

type OrderFilter = 'all' | 'orders' | 'stock'

// A job row plus the run counts the Job Runs / Ready Now / Blocked columns show
// for the selected order filter. The job's own totals stay untouched because
// "Split Into" is per full job (one blueprint, one in-game job).
type PlanRow = AssetPlanJob & { shown_runs: number; shown_ready: number; shown_coverage: number | null }

function orderRuns(j: AssetPlanJob): number {
  return j.special_order_runs ?? 0
}

// orderCoverage: {type_id: claimed / ordered} for items that are themselves
// order lines (from plan.order_progress). "Orders only" shows that instead of
// the item's stock-target coverage; intermediates keep their pooled coverage.
function viewRow(j: AssetPlanJob, filter: OrderFilter, orderCoverage: Map<number, number>): PlanRow {
  const orderReady = j.special_order_ready_runs ?? 0
  if (filter === 'orders') {
    return {
      ...j, shown_runs: orderRuns(j), shown_ready: orderReady,
      shown_coverage: orderCoverage.get(j.type_id) ?? j.stock_coverage ?? null,
    }
  }
  if (filter === 'stock') {
    return {
      ...j,
      shown_runs: Math.max(0, j.job_runs - orderRuns(j)),
      shown_ready: Math.max(0, j.runs_ready_now - orderReady),
      shown_coverage: j.stock_coverage ?? null,
    }
  }
  return { ...j, shown_runs: j.job_runs, shown_ready: j.runs_ready_now, shown_coverage: j.stock_coverage ?? null }
}

function orderLabels(j: AssetPlanJob): string[] {
  const ids = j.special_order_ids ?? []
  const notes = j.special_order_notes ?? []
  return ids.map((id, idx) => notes[idx]?.trim() || id.slice(0, 8))
}

export default function AssetPlanList() {
  const { data: plan, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'asset-plan'], queryFn: productionApi.assetPlan })
  const { data: settings } = useQuery({ queryKey: ['production', 'settings'], queryFn: productionApi.settings })
  const jobs = plan?.jobs ?? []

  const refreshAssetPlan = useAction('Refresh Asset Build List', productionApi.refreshAssetPlan, [
    ['production', 'asset-plan'], ['production', 'logistics'],
  ], { tier: 'live', effect: 'Recomputes the asset-optimized Buy/Build list with current Home/Jita prices - can take a while.' })
  const saveDaysTarget = useAction(
    'Slot target saved',
    async (value: number | null) => {
      if (!settings) return
      return productionApi.updateSettings({ ...settings, asset_plan_slot_days_target: value })
    },
    [['production', 'settings']],
  )
  // Local draft state so typing a multi-digit number doesn't fire a save
  // request per keystroke - only persisted on blur, same "edit locally,
  // commit explicitly" shape as ProductionSettings.tsx's form (just without
  // a separate Save button, since this is a single inline field).
  const [daysTargetDraft, setDaysTargetDraft] = useState<number | ''>('')
  useEffect(() => {
    setDaysTargetDraft(settings?.asset_plan_slot_days_target ?? '')
  }, [settings?.asset_plan_slot_days_target])

  const categories = useMemo(
    () => [...new Set(jobs.map((j) => j.job_category ?? CATEGORY_UNKNOWN))].sort(), [jobs],
  )
  const [selCategories, setSelCategories] = useState<string[]>([])
  const techLevels = useMemo(
    () => [...new Set(jobs.map((j) => j.tech_level ?? TECH_LEVEL_UNKNOWN))].sort(), [jobs],
  )
  const [selTechLevels, setSelTechLevels] = useState<string[]>([])
  const [orderFilter, setOrderFilter] = useState<OrderFilter>('all')
  const orderCoverage = useMemo(() => {
    const ordered = new Map<number, number>()
    const covered = new Map<number, number>()
    for (const r of plan?.order_progress ?? []) {
      ordered.set(r.type_id, (ordered.get(r.type_id) ?? 0) + r.ordered)
      covered.set(r.type_id, (covered.get(r.type_id) ?? 0) + r.covered)
    }
    const m = new Map<number, number>()
    for (const [typeId, total] of ordered) if (total > 0) m.set(typeId, (covered.get(typeId) ?? 0) / total)
    return m
  }, [plan])
  const hasOrderJobs = useMemo(() => jobs.some((j) => orderRuns(j) > 0), [jobs])
  const filtered = useMemo(() => jobs
    .filter((j) =>
      (selCategories.length === 0 || selCategories.includes(j.job_category ?? CATEGORY_UNKNOWN))
      && (selTechLevels.length === 0 || selTechLevels.includes(j.tech_level ?? TECH_LEVEL_UNKNOWN))
      && (orderFilter === 'all'
        || (orderFilter === 'orders' ? orderRuns(j) > 0 : j.job_runs - orderRuns(j) > 0)),
    )
    .map((j) => viewRow(j, orderFilter, orderCoverage)),
  [jobs, selCategories, selTechLevels, orderFilter, orderCoverage])

  const readyHours = useMemo(
    () => filtered.reduce((sum, j) => sum + (j.job_runs > 0 ? (j.job_time_seconds * j.shown_ready) / j.job_runs : 0), 0) / 3600,
    [filtered],
  )
  const totalHours = useMemo(
    () => filtered.reduce((sum, j) => sum + (j.job_runs > 0 ? (j.job_time_seconds * j.shown_runs) / j.job_runs : 0), 0) / 3600,
    [filtered],
  )
  const readyJobs = useMemo(() => filtered.filter((j) => j.shown_ready > 0).length, [filtered])

  const runsSuffix = orderFilter === 'orders' ? 'orders' : orderFilter === 'stock' ? 'stock targets' : 'total'
  const showOrderColumn = orderFilter !== 'stock' && hasOrderJobs

  const columns = useMemo<ColumnDef<PlanRow, any>[]>(() => [
    {
      header: 'Item', accessorKey: 'type_name', size: 220,
      cell: (i) => {
        const row = i.row.original
        return (
          <Stack gap={2}>
            <Text>{row.type_name}</Text>
            {row.recipe_source === 'alchemy' && (
              <Text size="xs" c="dimmed" style={{ whiteSpace: 'normal' }}>
                ⚗ Alchemy recipe - cheaper than the normal reaction; reprocess the output to get the target material
              </Text>
            )}
            {row.activity === 'Reaction' && (row.formulas_owned != null || row.recipe_source != null) && (
              <Text size="xs" c="dimmed">
                {row.recipe_source === 'alchemy' ? 'alchemy' : 'normal'}
                {row.formulas_owned != null && row.formulas_needed != null
                  ? ` · formulas ${row.formulas_owned}/${row.formulas_needed}`
                  : ''}
              </Text>
            )}
          </Stack>
        )
      },
    },
    { header: 'Category', accessorKey: 'job_category', size: 160, cell: (i) => i.getValue() ?? '–' },
    { header: 'Activity', accessorKey: 'activity', size: 130 },
    { header: 'Tech Level', accessorKey: 'tech_level', size: 120, cell: (i) => i.getValue() ?? '–' },
    { header: `Job Runs (${runsSuffix})`, accessorKey: 'shown_runs', size: 160, cell: (i) => qty(i.getValue()) },
    {
      header: 'Ready Now', accessorKey: 'shown_ready', size: 120,
      cell: (i) => <Text c={i.getValue() > 0 ? 'accent' : 'dimmed'} fw={i.getValue() > 0 ? 600 : 400}>{qty(i.getValue())}</Text>,
    },
    {
      header: 'Blocked', id: 'blocked', size: 110, accessorFn: (r) => r.shown_runs - r.shown_ready,
      meta: { cellTitle: (row) => blockedRunsTitle(row, row.shown_runs - row.shown_ready) },
      cell: (i) => (i.getValue() as number) > 0
        ? <Text c="warn">{qty(i.getValue() as number)}</Text>
        : qty(0),
    },
    ...(showOrderColumn ? [{
      header: 'Special orders', id: 'special_orders', size: 220,
      accessorFn: (r: PlanRow) => orderLabels(r).join(', '),
      cell: (i: any) => {
        const label = i.getValue() as string
        return label ? <Text size="sm" style={{ whiteSpace: 'normal' }}>{label}</Text> : '–'
      },
    }] : []),
    {
      header: 'Stock Coverage', accessorKey: 'shown_coverage', size: 140,
      cell: (i) => {
        const v = i.getValue() as number | null
        if (v === null) return '–'
        const color = v >= 0.5 ? 'accent' : v > 0 ? 'warn' : 'danger'
        return <Badge color={color} variant="light">{(v * 100).toFixed(0)}%</Badge>
      },
    },
    {
      header: 'Split Into', accessorKey: 'recommended_slots', size: 220,
      cell: (i) => {
        const row = i.row.original
        const n = i.getValue() as number | null
        if (n === null || n <= 0) return '–'
        const perSlot = row.runs_per_slot ?? Math.ceil(row.runs_ready_now / n)
        const waiting = Math.max(0, row.runs_ready_now - n * perSlot)
        const days = row.days_to_complete_at_recommended_slots
        const daysText = days == null ? '' : `, ~${days.toFixed(1)}d`
        const target = settings?.asset_plan_slot_days_target
        const missed = waiting > 0 || (target != null && days != null && days > target + 0.05)
        const waitingText = waiting > 0 ? `, ${qty(waiting)} runs wait` : ''
        const unlockText = row.unlock_time_seconds > 0 ? `, unlocks ~${duration(row.unlock_time_seconds)}` : ''
        return (
          <Text size="sm" c={missed ? 'warn' : undefined}>
            {n} slot{n === 1 ? '' : 's'} (~{qty(perSlot)} each{daysText}{waitingText}{unlockText})
          </Text>
        )
      },
    },
    {
      header: 'Quantity (Output)', id: 'quantity', size: 140,
      accessorFn: (r) => (r.job_runs > 0 ? (r.quantity * r.shown_runs) / r.job_runs : 0),
      cell: (i) => qty(i.getValue()),
    },
    { header: 'Job Time (h, total)', id: 'hours', size: 140, accessorFn: (r) => (r.job_runs > 0 ? (r.job_time_seconds * r.shown_runs) / r.job_runs : 0) / 3600, cell: (i) => (i.getValue() as number).toFixed(2) },
    { header: 'Modeled Unit Cost', accessorKey: 'unit_build_cost', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: 'Margin', accessorKey: 'margin', size: 110,
      cell: (i) => {
        const v = i.getValue()
        return v === null ? '–' : <Text c={v > 0 ? 'accent' : undefined}>{pct(v)}</Text>
      },
    },
    { header: 'Decryptor', accessorKey: 'decryptor', size: 130, cell: (i) => i.getValue() ?? '–' },
  ], [settings?.asset_plan_slot_days_target, runsSuffix, showOrderColumn])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
  if (!plan) {
    return (
      <Stack align="flex-start">
        <HintCard>No computation yet.</HintCard>
        <Tooltip label={refreshAssetPlan.tooltip} disabled={!refreshAssetPlan.tooltip} multiline w={280}>
          <Button leftSection={refreshAssetPlan.tierIcon} onClick={() => refreshAssetPlan.mutate()} loading={refreshAssetPlan.isPending}>
            Compute Asset Build List
          </Button>
        </Tooltip>
      </Stack>
    )
  }
  if (jobs.length === 0) {
    return (
      <Stack>
        <StalePlanAlert stale={plan.stale} />
        <HintCard>Nothing to build - all stock targets are bought or covered.</HintCard>
      </Stack>
    )
  }

  return (
    <Stack>
      <StalePlanAlert stale={plan.stale} />
      <ReactionCapacityPanel block={plan.reaction_capacity} />
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <Card withBorder padding="sm" w={220}>
            <Text size="xs" c="dimmed" tt="uppercase">Job Time Ready Now (h)</Text>
            <Title order={3} c="accent">{readyHours.toFixed(1)} / {totalHours.toFixed(1)}</Title>
          </Card>
          <Card withBorder padding="sm" w={220}>
            <Text size="xs" c="dimmed" tt="uppercase">Jobs With Runs Ready Now</Text>
            <Title order={3} c="accent">{readyJobs} / {filtered.length}</Title>
          </Card>
          <MultiSelect
            label="Category" data={categories} value={selCategories} onChange={setSelCategories}
            placeholder="All" clearable w={280}
          />
          <MultiSelect
            label="Tech Level" data={techLevels} value={selTechLevels} onChange={setSelTechLevels}
            placeholder="All" clearable w={220}
          />
          <div>
            <Text size="sm" fw={500} mb={4}>Special orders</Text>
            <SegmentedControl
              value={orderFilter}
              onChange={(v) => setOrderFilter(v as OrderFilter)}
              data={[
                { label: 'All', value: 'all' },
                { label: 'Orders only', value: 'orders' },
                { label: 'Without orders', value: 'stock' },
              ]}
            />
          </div>
          <NumberInput
            label="Slot target (max days per slot)"
            description="Empty = off. Click Recompute after saving."
            placeholder="Off"
            value={daysTargetDraft}
            min={0}
            step={1}
            w={280}
            disabled={!settings}
            onChange={(v) => setDaysTargetDraft(v === '' ? '' : Number(v))}
            onBlur={() => saveDaysTarget.mutate(daysTargetDraft === '' ? null : Number(daysTargetDraft))}
          />
        </Group>
        <Tooltip label={refreshAssetPlan.tooltip} disabled={!refreshAssetPlan.tooltip} multiline w={280}>
          <Button variant="default" leftSection={refreshAssetPlan.tierIcon} onClick={() => refreshAssetPlan.mutate()} loading={refreshAssetPlan.isPending}>
            Recompute
          </Button>
        </Tooltip>
      </Group>
      <Text size="xs" c="dimmed">{filtered.length} of {jobs.length} jobs</Text>
      {filtered.length === 0 ? (
        <HintCard>No jobs match the selected filters.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      )}
      <Text size="xs" c="dimmed">
        Unlike the regular build list, this one checks the actual asset stock at <b>every</b> level of the build
        chain (not just the end product) - including materials several jobs need at the same time. If a material
        is scarce, the jobs with the smallest requirement get fully restocked first, so as many jobs as possible
        are completely (not just partially) ready to start right away. "Ready now" = how many runs of this job you
        can queue in-game right now without waiting on another intermediate product - the rest of "Job Runs (total)"
        is still blocked (hover the Blocked number to see which direct materials are short, and by how much). "Stock Coverage" is how much of <i>this item itself</i> is already covered relative to
        what's currently wanted - for a stock target that's its backup/home/Jita goal (units you already have listed
        on the market count as covered, the same way the job size nets them out), for a pure intermediate
        component (no goal of its own) it's this round's pooled demand instead. Distinct from "Blocked", which is
        about whether <i>this item's own materials</i> are available to build it, not about this item's own stock.
        "Orders only" shows just the share of each job that serves open special orders included in the build lists,
        with the order notes in the "Special orders" column (Job Runs, Ready Now, Blocked, Quantity and Job Time
        are the order share; Stock Coverage of an ordered item is how much of the ordered quantity is already in the
        hangar or in running jobs); "Without orders" shows the stock-target share. "Split Into" always covers the
        whole job. Click the column header to sort by it if you want to see what's closest to running out first; it doesn't
        affect the list's own sort order or which jobs get queued. "Split Into" (Reactions/Advanced Components/
        Capital Components only) recommends how many of your currently-free character job slots to queue this
        job's ready runs across in parallel, chosen so all ready jobs together finish as early as possible: every
        job gets one slot, then each extra slot goes to whichever job would currently take longest per slot (never
        more slots than ready runs). If you have fewer free slots than jobs, jobs that unlock blocked work elsewhere
        in the plan start first ("unlocks ~Xh" in the cell), then the longest ones; the rest show "–" and wait for
        a slot to free up. Every free slot is used. The Slot target is a maximum per slot: it never leaves slots
        free, but each slot only gets as many runs as fit into the target ("~Nd" = how long one slot runs). If your
        free slots can't fit all ready runs within the target, the rest is shown orange as "N runs wait" for the
        next round.
      </Text>
    </Stack>
  )
}
