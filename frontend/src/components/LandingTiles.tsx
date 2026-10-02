import { Group, Skeleton, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { portfolioApi, productionApi, tradingApi } from '../api/client'
import { isk, qty } from '../format'

// Compact KPI line for a Landing card. Each tile loads on its own; while
// loading it shows a skeleton, and if the request fails it renders nothing
// (the card simply stays as it was - no error noise on the landing page).
function KpiLine({ isLoading, isError, items }: {
  isLoading: boolean
  isError: boolean
  items: { label: string; value: string }[] | null
}) {
  if (isLoading) return <Skeleton height={16} width="70%" data-testid="kpi-skeleton" />
  if (isError || !items) return null
  return (
    <Group gap="md" data-testid="kpi-line">
      {items.map((it) => (
        <Text key={it.label} size="xs" c="dimmed">
          <Text span fw={600} inherit style={{ color: 'var(--mantine-color-text)' }}>{it.value}</Text> {it.label}
        </Text>
      ))}
    </Group>
  )
}

export function TradingTile() {
  const q = useQuery({ queryKey: ['landing', 'trading-kpis'], queryFn: tradingApi.kpis, retry: false })
  const d = q.data
  return (
    <KpiLine isLoading={q.isLoading} isError={q.isError} items={d ? [
      { value: qty(d.shortlist_count), label: 'shortlisted' },
      { value: qty(d.import_candidates), label: 'to import' },
      { value: qty(d.own_sell_orders), label: 'own orders' },
    ] : null} />
  )
}

export function ProductionTile() {
  const q = useQuery({ queryKey: ['landing', 'production-kpis'], queryFn: productionApi.kpis, retry: false })
  const d = q.data
  return (
    <KpiLine isLoading={q.isLoading} isError={q.isError} items={d ? [
      { value: qty(d.stock_targets), label: 'stock targets' },
      { value: qty(d.active_jobs), label: 'active jobs' },
      { value: qty(d.open_special_orders), label: 'open orders' },
    ] : null} />
  )
}

// Reads the newest *stored* snapshot via /history - never /overview, which
// writes today's snapshot on the first call of a day.
export function PortfolioTile() {
  const q = useQuery({ queryKey: ['landing', 'portfolio-latest'], queryFn: () => portfolioApi.history(), retry: false })
  const rows = q.data
  const latest = rows && rows.length > 0
    ? rows.reduce((a, b) => (b.snapshot_date > a.snapshot_date ? b : a))
    : null
  const items = latest ? [
    { value: isk(latest.combined_value), label: 'combined value' },
    ...(latest.total_wealth != null ? [{ value: isk(latest.total_wealth), label: 'total wealth' }] : []),
  ] : null
  return <KpiLine isLoading={q.isLoading} isError={q.isError} items={items} />
}
