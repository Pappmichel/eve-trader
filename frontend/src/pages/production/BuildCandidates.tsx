import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Group, MultiSelect, NumberInput, Stack, Text, TextInput, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { BuildCandidate } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'
import { ActionTierIcon, TIER_COPY } from '../../components/ActionTierIcon'
import { isk, pct, qty } from '../../format'

const META_UNKNOWN = 'unknown'
const DISCOVER_LABELS = { discover_build_candidates: 'Discover Build Candidates' }

export default function BuildCandidates() {
  const [topN, setTopN] = useState<number | ''>(200)

  // Background job (Phase 2, 2026-09-13) - the scan can walk up to ~19,400
  // SDE items on a cold cache (see production/engine.py's
  // _scan_build_candidates docstring), genuinely slow enough to warrant
  // progress reporting instead of a blocking spinner with zero feedback,
  // same pattern as Doctrine's Sync Contracts / Admin's Refresh SDE.
  const discoverJob = useBackgroundJob({
    queryKey: ['production', 'build-candidates', 'discover-status'],
    fetchStatus: productionApi.discoverBuildCandidatesStatus,
    resultKeys: [['production', 'build-candidates']],
    labels: DISCOVER_LABELS,
    defaultLabel: 'Discover Build Candidates',
    pollIntervalMs: 1500,
  })
  const discoverStart = useBackgroundJobStart(discoverJob, (n: number) => productionApi.discoverBuildCandidates(n))
  const discoverRunning = discoverJob.runningStatus || discoverStart.isPending
  // The job's own result never carries the rows themselves (engine._discover_
  // cache is a process-local dict, not a DB table) - fetch them separately,
  // re-sliced to the current "Results to fetch" value without re-scanning
  // (get_cached_discover_results re-slices the same full cached scan).
  const { data } = useQuery({
    queryKey: ['production', 'build-candidates', topN],
    queryFn: () => productionApi.buildCandidates(typeof topN === 'number' ? topN : 200),
  })
  const neverRunYet = discoverJob.status?.status === 'idle' || discoverJob.status === undefined

  const activities = useMemo(() => [...new Set((data ?? []).map((r) => r.activity))].sort(), [data])
  const metaLevels = useMemo(() => {
    const levels = [...new Set((data ?? []).map((r) => r.meta_level))]
    const nums = levels.filter((l): l is number => l !== null).sort((a, b) => a - b)
    const options = nums.map(String)
    if (levels.includes(null)) options.push(META_UNKNOWN)
    return options
  }, [data])

  const [selActivities, setSelActivities] = useState<string[]>([])
  const [selMeta, setSelMeta] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [minMarginPct, setMinMarginPct] = useState<number | ''>(0)
  const [minDailyProfit, setMinDailyProfit] = useState<number | ''>(0)

  const effectiveActivities = selActivities.length ? selActivities : activities
  const effectiveMeta = selMeta.length ? selMeta : metaLevels

  const filtered = useMemo(() => {
    return (data ?? []).filter((r) => {
      if (!effectiveActivities.includes(r.activity)) return false
      const metaKey = r.meta_level === null ? META_UNKNOWN : String(r.meta_level)
      if (!effectiveMeta.includes(metaKey)) return false
      if (search && !r.type_name.toLowerCase().includes(search.toLowerCase())) return false
      if (minMarginPct && r.margin < Number(minMarginPct) / 100) return false
      if (minDailyProfit && r.potential_daily_profit < Number(minDailyProfit)) return false
      return true
    })
  }, [data, effectiveActivities, effectiveMeta, search, minMarginPct, minDailyProfit])

  const columns = useMemo<ColumnDef<BuildCandidate, any>[]>(() => [
    {
      header: 'Item', accessorKey: 'type_name', size: 420,
      cell: (i) => {
        const row = i.row.original
        const note = row.activity === 'Reaction' ? row.alchemy_comparison : null
        const iskH = (value: number | null) => (value == null ? '–' : `${isk(value)}/h`)
        return (
          <Stack gap={2}>
            <Text>{row.type_name}</Text>
            {note && (
              <Text size="xs" c="dimmed" style={{ whiteSpace: 'normal' }}>
                ⚗ Alchemy alternative available: normal {iskH(note.normal_isk_per_hour)} vs. alchemy {iskH(note.alchemy_isk_per_hour)}
              </Text>
            )}
          </Stack>
        )
      },
    },
    { header: 'Activity', accessorKey: 'activity', size: 120 },
    { header: 'Meta Level', accessorKey: 'meta_level', size: 100, cell: (i) => i.getValue() ?? '–' },
    { header: 'Build Cost', accessorKey: 'build_cost', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Margin', accessorKey: 'margin', size: 100, cell: (i) => pct(i.getValue()) },
    { header: 'Daily Movement (units)', accessorKey: 'daily_movement', size: 170, cell: (i) => qty(i.getValue()) },
    {
      header: 'Potential Daily Profit (theoretical)', accessorKey: 'potential_daily_profit', size: 220,
      cell: (i) => <Text c="accent" fw={600}>{isk(i.getValue())}</Text>,
    },
  ], [])

  return (
    <Stack>
      <HintCard>
        Scans every buildable, market-listed item not already a stock target and flags where building clearly
        beats buying, priced at the C-J sell quote. Needs the SDE cache populated (Refresh SDE in the Admin tool)
        and can take a while.
        <br /><br />
        Sorted by <b>Potential Daily Profit</b> (theoretical - the value of the item's entire day of C-J turnover,
        not what you alone could capture), not Margin. Minimum-margin/profit gates live in Settings; the filters
        below only narrow what's already fetched.
      </HintCard>

      <Group align="flex-end">
        <NumberInput
          label="Results to fetch" value={topN} onChange={(v) => setTopN(v === '' ? '' : Number(v))}
          min={10} step={50} w={160}
        />
        <Tooltip
          label={`Scannt jedes SDE-Item mit Preisen von ESI/Jita-Cache neu (Ergebnis wird einige Minuten gecacht) - läuft als Background-Job mit Fortschrittsanzeige. ${TIER_COPY.live}`}
          multiline w={280}
        >
          <Button
            w={280} variant={discoverRunning ? 'light' : undefined}
            rightSection={<ActionTierIcon tier="live" />}
            onClick={() => topN !== '' && discoverStart.mutate(Number(topN))}
            disabled={topN === ''}
          >
            Discover Build Candidates
          </Button>
        </Tooltip>
      </Group>
      {discoverRunning && (
        <Text size="xs" c="dimmed">{discoverJob.formatProgress(discoverJob.status?.progress, discoverJob.jobName)}</Text>
      )}

      {neverRunYet && (!data || data.length === 0) && (
        <HintCard>No computation yet. Click <b>Discover Build Candidates</b> above.</HintCard>
      )}

      {!neverRunYet && data && data.length === 0 && (
        <HintCard>No untracked item currently clears the minimum build margin / minimum daily profit.</HintCard>
      )}

      {data && data.length > 0 && (
        <>
          <Group grow align="flex-end">
            <MultiSelect label="Activity" data={activities} value={selActivities} onChange={setSelActivities} placeholder="All" clearable />
            <MultiSelect label="Meta Level" data={metaLevels} value={selMeta} onChange={setSelMeta} placeholder="All" clearable />
            <TextInput label="Search (item)" value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
            <NumberInput label="Min. margin %" value={minMarginPct} onChange={(v) => setMinMarginPct(v === '' ? '' : Number(v))} min={0} step={1} />
            <NumberInput label="Min. daily profit (ISK)" value={minDailyProfit} onChange={(v) => setMinDailyProfit(v === '' ? '' : Number(v))} min={0} step={1000} />
          </Group>

          <Text size="sm" c="dimmed">{filtered.length} of {data.length} candidates not yet tracked as stock targets</Text>

          {filtered.length === 0 ? (
            <HintCard>No candidates match the current filters.</HintCard>
          ) : (
            <DataTable data={filtered} columns={columns} maxHeight={560} />
          )}
        </>
      )}
    </Stack>
  )
}
