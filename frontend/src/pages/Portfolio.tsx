import { useQuery } from '@tanstack/react-query'
import { Container, Title, Text, SimpleGrid, Card, Group, Stack, Button, Loader, Center, Badge } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

import { portfolioApi } from '../api/client'
import { HintCard } from '../components/HintCard'
import { isk, qty, pct, dateTime } from '../format'
import type { SchedulerJobStatus } from '../api/types'

function schedulerCadence(job: SchedulerJobStatus): string {
  if (job.tier_interval_hours) {
    const t = job.tier_interval_hours
    return `frequent ${t.frequent}h / normal ${t.normal}h / rare ${t.rare}h`
  }
  if (job.interval_hours == null) return 'no single cadence'
  return `every ${job.interval_hours}h`
}

function SchedulerJobRow({ label, job }: { label: string; job: SchedulerJobStatus }) {
  // Empty last_run_at is "never synced", not a missing/error value.
  const last = job.last_run_at == null ? 'never' : dateTime(job.last_run_at)
  return (
    <Group justify="space-between">
      <Text size="sm">{label}</Text>
      <Group gap="xs">
        <Text size="xs" c="dimmed">{schedulerCadence(job)} - last: {last}</Text>
        {job.last_error && <Badge color="danger" variant="light">error</Badge>}
      </Group>
    </Group>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card withBorder padding="lg" radius="md">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{label}</Title>
      <Text size="xl" fw={700}>{value}</Text>
      {hint && <Text size="xs" c="dimmed" mt={4}>{hint}</Text>}
    </Card>
  )
}

export default function Portfolio() {
  const { data, isLoading } = useQuery({ queryKey: ['portfolio', 'overview'], queryFn: portfolioApi.overview })
  const { data: scheduler } = useQuery({ queryKey: ['portfolio', 'scheduler-status'], queryFn: portfolioApi.schedulerStatus })

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

          {scheduler && (
            <Card withBorder padding="lg" radius="md">
              <Group justify="space-between" mb="sm">
                <Title order={6} c="dimmed" tt="uppercase">Background Scheduler</Title>
                <Badge color={scheduler.enabled ? (scheduler.running ? 'accent' : 'warn') : 'gray'} variant="light">
                  {scheduler.enabled ? (scheduler.running ? 'running' : 'enabled, not running') : 'disabled'}
                </Badge>
              </Group>
              <Stack gap="xs">
                <SchedulerJobRow label="Trading pipeline" job={scheduler.jobs.trading_pipeline} />
                <SchedulerJobRow label="ESI data sync" job={scheduler.jobs.esi_data_sync} />
                <SchedulerJobRow label="Backup" job={scheduler.jobs.backup} />
                <SchedulerJobRow label="Jita price cache" job={scheduler.jobs.jita_price_cache} />
              </Stack>
              {!scheduler.enabled && (
                <Text size="xs" c="dimmed" mt="sm">
                  Off by default - enable it in Settings to run these automatically instead of clicking
                  "Run Complete Pipeline" / "Sync ESI" by hand, or "Backup Now" on the Admin page. ESI
                  data sync fetches only (owner, kind) pairs whose freshness interval has elapsed; a
                  manual tool Sync stamps freshness and pushes those pairs back.
                </Text>
              )}
            </Card>
          )}

          <HintCard>
            Everything above is read-only. Backups moved to the Admin tool - one pg_dump already covers
            every tenant's data in one shot, so creating one is a cross-tenant-impacting action, not a
            per-tenant Portfolio button.
          </HintCard>
        </Stack>
      )}
    </Container>
  )
}
