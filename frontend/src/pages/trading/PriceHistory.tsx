import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueries } from '@tanstack/react-query'
import { Group, MultiSelect, Select, SegmentedControl, Skeleton, Stack } from '@mantine/core'
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid, Legend, Brush } from 'recharts'

import { sdeApi, tradingApi } from '../../api/client'
import type { PriceHistory as PriceHistoryData } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { isk } from '../../format'
import { COLORS } from '../../theme'
import { hubLabel } from '../../tradingHubs'
import { normalizeCompare, type CompareRow } from './priceHistoryCompare'

interface ChartRow {
  date: string
  hub: number | null
  reference: number | null
}

// One row per date with both regions' average price side by side; a date
// only one region has stays a gap in the other line.
function mergeHistory(history: PriceHistoryData | undefined): ChartRow[] {
  if (!history) return []
  const byDate = new Map<string, ChartRow>()
  const row = (date: string) => {
    let r = byDate.get(date)
    if (!r) { r = { date, hub: null, reference: null }; byDate.set(date, r) }
    return r
  }
  for (const p of history.hub) row(p.date).hub = p.avg_price
  for (const p of history.reference) row(p.date).reference = p.avg_price
  return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))
}

const MAX_COMPARE = 5
const DAY_MS = 86_400_000
const RANGE_OPTIONS = [
  { label: '7d', value: '7' },
  { label: '30d', value: '30' },
  { label: '90d', value: '90' },
  { label: 'All', value: 'all' },
]
const LINE_COLORS = [COLORS.accent, COLORS.info, COLORS.warn, COLORS.danger, COLORS.text]

type SeriesKey = 'hub' | 'reference'

function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

function cutoffIso(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10)
}

// Days between the oldest stored date and today (0 without data).
function coveredDays(histories: (PriceHistoryData | undefined)[]): number {
  let oldest: string | null = null
  for (const h of histories) {
    for (const p of [...(h?.hub ?? []), ...(h?.reference ?? [])]) {
      if (oldest === null || p.date < oldest) oldest = p.date
    }
  }
  if (oldest === null) return 0
  return Math.max(0, Math.round((Date.parse(todayIso()) - Date.parse(oldest)) / DAY_MS))
}

export default function PriceHistory() {
  const { data: typeIds, isLoading } = useQuery({ queryKey: ['trading', 'history', 'type-ids'], queryFn: tradingApi.historyTypeIds })
  const { data: regions } = useQuery({ queryKey: ['sde', 'regions'], queryFn: sdeApi.regions, retry: false })
  const [chosen, setChosen] = useState<string[] | null>(null)
  const [rangeChoice, setRangeChoice] = useState<string | null>(null)
  const [seriesKey, setSeriesKey] = useState<SeriesKey>('hub')
  // ?item=<type_id> (e.g. from the Shortlist detail drawer) preselects an item.
  const [searchParams] = useSearchParams()
  const fromUrl = searchParams.get('item')
  const urlChoice = fromUrl && typeIds?.some((t) => String(t.type_id) === fromUrl) ? fromUrl : null

  const defaultId = urlChoice ?? (typeIds && typeIds.length > 0 ? String(typeIds[0].type_id) : null)
  const ids = chosen && chosen.length > 0 ? chosen : defaultId ? [defaultId] : []
  const compare = ids.length > 1
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['trading', 'history', id],
      queryFn: () => tradingApi.history(Number(id)),
    })),
  })
  const histories = results.map((r) => r.data)
  const single = compare ? undefined : histories[0]
  const available = coveredDays(histories)

  const rangeEnabled = (v: string) => v === 'all' || Number(v) <= available + 7
  const range = rangeChoice && rangeEnabled(rangeChoice) ? rangeChoice : rangeEnabled('30') ? '30' : 'all'
  const cutoff = range === 'all' ? null : cutoffIso(Number(range))

  const nameOf = (id: string) => typeIds?.find((t) => String(t.type_id) === id)?.type_name ?? id
  const refRegionId = histories.find((h) => h)?.reference_region_id
  const refName = regions?.find((r) => r.region_id === refRegionId)?.region_name
  const refLabel = refName ? `${refName} (sell side)` : 'Reference region (sell side)'
  const hubRegionId = histories.find((h) => h)?.hub_region_id

  const updatedKey = results.map((r) => r.dataUpdatedAt).join(',')
  const idsKey = ids.join(',')
  const rows = useMemo(() => {
    const inRange = <T extends { date: string }>(pts: T[]) => (cutoff ? pts.filter((p) => p.date >= cutoff) : pts)
    if (compare) {
      return normalizeCompare(ids.map((id, i) => ({
        id,
        points: inRange(histories[i]?.[seriesKey] ?? []),
      })))
    }
    return inRange(mergeHistory(single))
    // histories/ids are derived from the keys below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compare, updatedKey, idsKey, seriesKey, cutoff])

  if (isLoading) {
    return (
      <Stack>
        <Skeleton height={36} width={200} />
        <Skeleton height={360} />
      </Stack>
    )
  }
  if (!typeIds || typeIds.length === 0) {
    return <HintCard>No price history cached yet. It's created automatically by a run of <b>Search + Add + Clean Up</b>.</HintCard>
  }

  const options = typeIds.map((t) => ({ value: String(t.type_id), label: t.type_name }))
  return (
    <>
      <Group align="flex-end" mb="md" gap="md">
        <Select
          label="Item"
          data={options}
          value={ids[0] ?? null}
          onChange={(v) => v && setChosen([v, ...ids.slice(1).filter((x) => x !== v)])}
          searchable
          w={280}
        />
        <MultiSelect
          label={`Compare with (max ${MAX_COMPARE} items in total)`}
          placeholder="Add items"
          data={options}
          value={ids.slice(1)}
          onChange={(v) => setChosen([ids[0], ...v.filter((x) => x !== ids[0])].slice(0, MAX_COMPARE))}
          maxValues={MAX_COMPARE - 1}
          searchable
          clearable
          w={340}
        />
        {compare && (
          <SegmentedControl
            size="xs"
            aria-label="Region series"
            value={seriesKey}
            onChange={(v) => setSeriesKey(v as SeriesKey)}
            data={[{ label: 'Buy hub', value: 'hub' }, { label: 'Reference region', value: 'reference' }]}
          />
        )}
        <SegmentedControl
          size="xs"
          aria-label="Date range"
          value={range}
          onChange={setRangeChoice}
          data={RANGE_OPTIONS.map((o) => ({ ...o, disabled: !rangeEnabled(o.value) }))}
        />
      </Group>
      <ResponsiveContainer width="100%" height={400}>
        <LineChart data={rows as CompareRow[]}>
          <CartesianGrid stroke={COLORS.border} />
          <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <YAxis stroke={COLORS.textDim} domain={compare ? ['auto', 'auto'] : undefined} />
          <Tooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }}
            formatter={(v, _name, entry) => {
              if (!compare) return isk(v as number)
              const real = (entry.payload as CompareRow | undefined)?.[`p${String(entry.dataKey).slice(1)}`]
              return `${(v as number).toFixed(1)} (${isk(real as number)})`
            }} />
          <Legend />
          {compare ? ids.map((id, i) => (
            <Line key={id} type="monotone" dataKey={`i${id}`} name={nameOf(id)}
              stroke={LINE_COLORS[i % LINE_COLORS.length]} dot={false} connectNulls />
          )) : (
            <>
              <Line type="monotone" dataKey="hub" name={`${hubLabel(hubRegionId)} (buy hub)`}
                stroke={COLORS.accent} dot={false} connectNulls={false} />
              <Line type="monotone" dataKey="reference" name={refLabel}
                stroke={COLORS.info} dot={false} connectNulls={false} />
            </>
          )}
          <Brush dataKey="date" height={24} stroke={COLORS.textDim} fill={COLORS.surface2} />
        </LineChart>
      </ResponsiveContainer>
    </>
  )
}
