import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Select, Skeleton, Stack } from '@mantine/core'
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid, Legend } from 'recharts'

import { tradingApi } from '../../api/client'
import type { PriceHistory as PriceHistoryData } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { isk } from '../../format'
import { COLORS } from '../../theme'
import { hubLabel } from '../../tradingHubs'

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

export default function PriceHistory() {
  const { data: typeIds, isLoading } = useQuery({ queryKey: ['trading', 'history', 'type-ids'], queryFn: tradingApi.historyTypeIds })
  const [chosen, setChosen] = useState<string | null>(null)
  // ?item=<type_id> (e.g. from the Shortlist detail drawer) preselects an item.
  const [searchParams] = useSearchParams()
  const fromUrl = searchParams.get('item')
  const urlChoice = fromUrl && typeIds?.some((t) => String(t.type_id) === fromUrl) ? fromUrl : null

  const effectiveId = chosen ?? urlChoice ?? (typeIds && typeIds.length > 0 ? String(typeIds[0].type_id) : null)
  const { data: history } = useQuery({
    queryKey: ['trading', 'history', effectiveId],
    queryFn: () => tradingApi.history(Number(effectiveId)),
    enabled: !!effectiveId,
  })
  const rows = useMemo(() => mergeHistory(history), [history])

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

  return (
    <>
      <Select
        label="Item"
        data={typeIds.map((t) => ({ value: String(t.type_id), label: t.type_name }))}
        value={effectiveId}
        onChange={setChosen}
        searchable
        mb="md"
        w={280}
      />
      <ResponsiveContainer width="100%" height={360}>
        <LineChart data={rows}>
          <CartesianGrid stroke={COLORS.border} />
          <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <YAxis stroke={COLORS.textDim} />
          <Tooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }}
            formatter={(v) => isk(v as number)} />
          <Legend />
          <Line type="monotone" dataKey="hub" name={`${hubLabel(history?.hub_region_id)} (buy hub)`}
            stroke={COLORS.accent} dot={false} connectNulls={false} />
          <Line type="monotone" dataKey="reference" name="Reference region (sell side)"
            stroke={COLORS.info} dot={false} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
    </>
  )
}
