import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Select, Skeleton, Stack } from '@mantine/core'
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid } from 'recharts'

import { tradingApi } from '../../api/client'
import { HintCard } from '../../components/HintCard'
import { COLORS } from '../../theme'

export default function PriceHistory() {
  const { data: typeIds, isLoading } = useQuery({ queryKey: ['trading', 'history', 'type-ids'], queryFn: tradingApi.historyTypeIds })
  const [chosen, setChosen] = useState<string | null>(null)

  const effectiveId = chosen ?? (typeIds && typeIds.length > 0 ? String(typeIds[0].type_id) : null)
  const { data: history } = useQuery({
    queryKey: ['trading', 'history', effectiveId],
    queryFn: () => tradingApi.history(Number(effectiveId)),
    enabled: !!effectiveId,
  })

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
        <LineChart data={history ?? []}>
          <CartesianGrid stroke={COLORS.border} />
          <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <YAxis stroke={COLORS.textDim} />
          <Tooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }} />
          <Line type="monotone" dataKey="avg_price" stroke={COLORS.accent} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </>
  )
}
