import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Container, Title, Text, SimpleGrid, Card, Group, Stack, Button, Loader, Center, SegmentedControl } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid, Legend } from 'recharts'

import { portfolioApi } from '../api/client'
import { HintCard } from '../components/HintCard'
import { isk, qty, pct } from '../format'
import { COLORS } from '../theme'
import type { PortfolioSnapshotRow } from '../api/types'

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card withBorder padding="lg" radius="md">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{label}</Title>
      <Text size="xl" fw={700}>{value}</Text>
      {hint && <Text size="xs" c="dimmed" mt={4}>{hint}</Text>}
    </Card>
  )
}

const RANGE_OPTIONS = [
  { label: '7d', value: '7' },
  { label: '30d', value: '30' },
  { label: '90d', value: '90' },
  { label: 'All', value: 'all' },
]

function tooltipStyle() {
  return { background: COLORS.surface2, border: `1px solid ${COLORS.border}` }
}

function HistoryChart({ title, hint, rows, lines }: {
  title: string
  hint?: string
  rows: PortfolioSnapshotRow[]
  lines: { dataKey: keyof PortfolioSnapshotRow; name: string; color: string; formatter: (v: number) => string }[]
}) {
  // Fewer than 2 days of history has no meaningful trend to chart -
  // mirrors trading_daily_profit_volatility's own "None below 2 days of
  // data" precedent rather than rendering an empty/broken chart.
  if (rows.length < 2) {
    return (
      <Card withBorder padding="lg" radius="md">
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
        <Text size="sm" c="dimmed">History builds up from here - check back tomorrow.</Text>
      </Card>
    )
  }
  return (
    <Card withBorder padding="lg" radius="md">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
      {hint && <Text size="xs" c="dimmed" mb="sm">{hint}</Text>}
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={rows}>
          <CartesianGrid stroke={COLORS.border} />
          <XAxis dataKey="snapshot_date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <YAxis stroke={COLORS.textDim} tick={{ fontSize: 11 }} width={90}
            tickFormatter={(v: number) => lines[0].formatter(v)} />
          <Tooltip contentStyle={tooltipStyle()} formatter={(v, name) => {
            const line = lines.find((l) => l.name === name)
            return [line && typeof v === 'number' ? line.formatter(v) : v, name]
          }} />
          {lines.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {lines.map((line) => (
            <Line key={String(line.dataKey)} type="monotone" dataKey={line.dataKey} name={line.name}
              stroke={line.color} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Card>
  )
}

export default function Portfolio() {
  const { data, isLoading } = useQuery({ queryKey: ['portfolio', 'overview'], queryFn: portfolioApi.overview })
  const [range, setRange] = useState('30')
  const { data: history } = useQuery({
    queryKey: ['portfolio', 'history', range],
    queryFn: () => portfolioApi.history(range === 'all' ? undefined : Number(range)),
  })

  return (
    <Container size="md" py="xl">
      <Group justify="space-between" mb="lg">
        <div>
          <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>Trading + Production combined</Text>
          <Title order={1}>Portfolio Overview</Title>
        </div>
        <Button component={Link} to="/" variant="subtle" leftSection={<IconArrowLeft size={14} />}>Back</Button>
      </Group>

      {isLoading && <Center h={120}><Loader color="accent" /></Center>}

      {data && (
        <Stack gap="lg">
          <Stat label="Combined Value" value={isk(data.combined_value)}
            hint="Trading realized profit (latest reconciliation) + current Production stock value" />

          <SimpleGrid cols={2} spacing="md">
            <Stat label="Trading: Realized Profit" value={isk(data.trading_realized_profit)}
              hint={`${qty(data.trading_trade_count)} matched trades in latest reconciliation`} />
            <Stat label="Trading: Average Margin" value={pct(data.trading_average_margin)} />
            <Stat label="Trading: Daily Profit Volatility"
              value={data.trading_daily_profit_volatility === null ? '–' : isk(data.trading_daily_profit_volatility)}
              hint="Standard deviation of realized profit per day - a simple risk signal, not a formal VaR model" />
            <Stat label="Production: Stock Value" value={isk(data.production_stock_value)}
              hint={data.production_stock_targets_configured ? undefined : 'No stock targets configured yet'} />
          </SimpleGrid>

          <Group justify="space-between" align="center">
            <Title order={4}>History</Title>
            <SegmentedControl size="xs" value={range} onChange={setRange} data={RANGE_OPTIONS} />
          </Group>

          <HistoryChart title="Combined Value" rows={history ?? []}
            lines={[{ dataKey: 'combined_value', name: 'Combined Value', color: COLORS.accent, formatter: isk }]} />

          <HistoryChart title="Trading Realized Profit vs. Production Stock Value" rows={history ?? []}
            lines={[
              { dataKey: 'trading_realized_profit', name: 'Trading Realized Profit', color: COLORS.accent, formatter: isk },
              { dataKey: 'production_stock_value', name: 'Production Stock Value', color: COLORS.info, formatter: isk },
            ]} />

          <SimpleGrid cols={2} spacing="md">
            <HistoryChart title="Trading Average Margin" rows={history ?? []}
              lines={[{ dataKey: 'trading_average_margin', name: 'Average Margin', color: COLORS.info, formatter: pct }]} />
            <HistoryChart title="Trading Daily Profit Volatility" rows={history ?? []}
              lines={[{ dataKey: 'trading_daily_profit_volatility', name: 'Daily Profit Volatility', color: COLORS.warn, formatter: isk }]} />
          </SimpleGrid>

          <HintCard>Everything above is read-only.</HintCard>
        </Stack>
      )}
    </Container>
  )
}
