import { useMemo, useState } from 'react'
import { Button, Group, MultiSelect, NumberInput, Stack, Text, TextInput } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { BuildCandidate } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'

const META_UNKNOWN = 'unknown'

export default function BuildCandidates() {
  const discover = useAction('Discover Build Candidates', (topN: number) => productionApi.discoverBuildCandidates(topN))
  const data = discover.data
  const [topN, setTopN] = useState<number | ''>(200)

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
    { header: 'Item', accessorKey: 'type_name', size: 260 },
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
        Scans every published, market-listed item with a real Manufacturing/Reaction/Invention recipe that
        <b> isn't</b> already one of your configured stock targets, and flags the ones where building clearly beats
        buying right now (same buy-vs-build margin gate the Bauliste itself uses) - Production's equivalent of the
        Trading tool's candidate discovery, for things you haven't thought to track yet. Prices at the C-J (home)
        sell quote - this tool is for building for the local C-J market, not freighting finished goods to Jita to
        sell. Needs the SDE cache populated (Refresh SDE in the Admin tool) and can take a while - it walks every
        market-listed item, not just your existing stock targets.
        <br /><br />
        Sorted by <b>Potential Daily Profit (theoretical)</b>, not Margin - a huge margin on an item nobody actually
        buys locally is worthless. Daily Movement is the item's average number of <b>units</b> traded per day in the
        C-J region (ESI's own official daily market history); Potential Daily Profit multiplies that by the
        estimated profit per unit - same "(theoretical)" framing as the Trading tool's own Profit/Day column: it's
        the value of the item's <b>entire</b> day of C-J turnover, not a claim that you personally could capture all
        of it. A rare, expensive item (a capital hull, a faction module) can show a huge number from only a handful
        of units/day - that's mathematically correct for "what's the whole market worth", just not something to bet
        on selling single-handedly. An item with no recent C-J trading history shows 0 for both and sorts to the
        bottom even if its margin looks great - that's an accurate reflection of this tool's own local sales
        channel, even for an item that happens to trade heavily elsewhere. The hard minimum-margin and
        minimum-daily-profit gates live in Settings (shared with the Bauliste); the filters below only narrow what's
        already been fetched, no re-scan needed.
        <br /><br />
        Filters out the worst SDE/market data quirks found while building this (a handful of reward ships have a
        real but vestigial "1 material" blueprint entry; some obscure items have no real order book at all) - but
        with tens of thousands of items scanned, treat every result as a lead to verify in-game before committing
        real ISK, not a guarantee.
      </HintCard>

      <Group align="flex-end">
        <NumberInput
          label="Results to fetch" value={topN} onChange={(v) => setTopN(v === '' ? '' : Number(v))}
          min={10} step={50} w={160}
        />
        <Button
          w={280} onClick={() => topN !== '' && discover.mutate(topN)}
          loading={discover.isPending} disabled={topN === ''}
        >
          Discover Build Candidates
        </Button>
      </Group>

      {data && data.length === 0 && (
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
