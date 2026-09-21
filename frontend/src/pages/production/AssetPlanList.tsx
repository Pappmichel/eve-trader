import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, MultiSelect, Badge, NumberInput, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { AssetPlanJob } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { duration, isk, pct, qty } from '../../format'
import { blockedRunsTitle } from './assetPlanBlockers'

const CATEGORY_UNKNOWN = 'no category'

export default function AssetPlanList() {
  const { data: plan, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'asset-plan'], queryFn: productionApi.assetPlan })
  const { data: settings } = useQuery({ queryKey: ['production', 'settings'], queryFn: productionApi.settings })
  const jobs = plan?.jobs ?? []

  const refreshAssetPlan = useAction('Refresh Asset Build List', productionApi.refreshAssetPlan, [
    ['production', 'asset-plan'],
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
  const filtered = useMemo(() => {
    if (selCategories.length === 0) return jobs
    return jobs.filter((j) => selCategories.includes(j.job_category ?? CATEGORY_UNKNOWN))
  }, [jobs, selCategories])

  const readyHours = useMemo(
    () => filtered.reduce((sum, j) => sum + (j.job_runs > 0 ? (j.job_time_seconds * j.runs_ready_now) / j.job_runs : 0), 0) / 3600,
    [filtered],
  )
  const totalHours = useMemo(() => filtered.reduce((sum, j) => sum + j.job_time_seconds, 0) / 3600, [filtered])
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
      meta: { cellTitle: (row) => blockedRunsTitle(row) },
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
      header: 'Split Into', accessorKey: 'recommended_slots', size: 220,
      cell: (i) => {
        const row = i.row.original
        const n = i.getValue() as number | null
        if (n === null || n <= 0) return '–'
        const perSlot = Math.ceil(row.runs_ready_now / n)
        const days = row.days_to_complete_at_recommended_slots
        const daysText = days == null ? '' : `, ~${days.toFixed(1)}d`
        const target = settings?.asset_plan_slot_days_target
        const missed = target != null && days != null && days > target + 0.05
        const unlockText = row.unlock_time_seconds > 0 ? `, unlocks ~${duration(row.unlock_time_seconds)}` : ''
        return (
          <Text size="sm" c={missed ? 'warn' : undefined}>
            {n} slot{n === 1 ? '' : 's'} (~{qty(perSlot)} each{daysText}{unlockText})
          </Text>
        )
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
  ], [settings?.asset_plan_slot_days_target])

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
  if (jobs.length === 0) return <HintCard>Nothing to build - all stock targets are bought or covered.</HintCard>

  return (
    <Stack>
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
          <NumberInput
            label="Slot target (days to clear backlog)"
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
        is still blocked (hover the Blocked number to see which direct materials are short, and by how much). "Stock Coverage" is how much of <i>this item itself</i> is already on hand relative to
        what's currently wanted - for a stock target that's its backup/home/Jita goal, for a pure intermediate
        component (no goal of its own) it's this round's pooled demand instead. Distinct from "Blocked", which is
        about whether <i>this item's own materials</i> are available to build it, not about this item's own stock.
        Click the column header to sort by it if you want to see what's closest to running out first; it doesn't
        affect the list's own sort order or which jobs get queued. "Split Into" (Reactions/Advanced Components/
        Capital Components only) recommends how many of your currently-free character job slots to queue this
        job's ready runs across in parallel, instead of one long serial batch. Slots are claimed in priority
        order: first by how much currently-<i>blocked</i> job time elsewhere in the plan finishing this job would
        newly unblock ("unlocks ~Xh" in the cell, when positive) - e.g. a Reaction whose product is the only thing
        still missing for several Component jobs outranks one feeding nothing else, even if it's better stocked -
        then, for jobs tied on that, by Stock Coverage ascending (least of itself already on hand claims first).
        Whichever job wins claims its own full need before the next one gets anything - so a single high-priority
        job can take the entire free-slot pool for a category and leave everything else sharing it at 0 this
        round, on purpose: the goal is to fully finish what unblocks the most downstream work (or, failing that,
        what's most depleted) rather than spread every job forward a little. Only a job's <i>sole remaining</i>
        same-category blocker (another Reaction, Advanced, or Capital Component - never a raw-buy material) earns
        unlock credit; a job still short on something else, or sharing the blocker with another still-missing job
        of the same kind, earns none yet. Two modes control each job's own "need" (the ceiling it can claim): with
        Slot target empty (the default), a job's need is simply its own ready runs, uncapped. With a Slot target
        set, each job's need instead becomes however many slots it would take to finish its own ready runs within
        that many days (never more than it has ready runs) - priority order still decides who claims first, so if
        the pool can't cover everyone's need in that order, some jobs may get fewer slots than their target asks
        for, or none at all. The "~Nd" next to the split is how many days that recommendation would actually take
        - always shown, in both modes. Orange means it missed the configured Slot target because the pool ran
        short before reaching this job (the real number is still shown; nothing is hidden or auto-capped).
      </Text>
    </Stack>
  )
}
