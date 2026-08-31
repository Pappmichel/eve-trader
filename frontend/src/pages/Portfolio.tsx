import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Container, Title, Text, SimpleGrid, Card, Group, Stack, Button, Loader, Center, Badge } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

import { portfolioApi } from '../api/client'
import { HintCard } from '../components/HintCard'
import { useAction } from '../hooks/useAction'
import { isk, qty, pct, dateTime } from '../format'
import type { SchedulerJobStatus } from '../api/types'

function SchedulerJobRow({ label, job }: { label: string; job: SchedulerJobStatus }) {
  return (
    <Group justify="space-between">
      <Text size="sm">{label}</Text>
      <Group gap="xs">
        <Text size="xs" c="dimmed">every {job.interval_hours}h - last: {dateTime(job.last_run_at)}</Text>
        {job.last_error && <Badge color="danger" variant="light">error</Badge>}
      </Group>
    </Group>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
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
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['portfolio', 'overview'], queryFn: portfolioApi.overview })
  const { data: scheduler } = useQuery({ queryKey: ['portfolio', 'scheduler-status'], queryFn: portfolioApi.schedulerStatus })
  const { data: backups } = useQuery({ queryKey: ['portfolio', 'backups'], queryFn: portfolioApi.backups })
  const createBackup = useAction('Backup Now', portfolioApi.createBackup)

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
                <SchedulerJobRow label="Production ESI sync" job={scheduler.jobs.production_sync} />
                <SchedulerJobRow label="Backup" job={scheduler.jobs.backup} />
                <SchedulerJobRow label="Jita price cache" job={scheduler.jobs.jita_price_cache} />
              </Stack>
              {!scheduler.enabled && (
                <Text size="xs" c="dimmed" mt="sm">
                  Off by default - enable it in Settings to run these automatically instead of clicking
                  "Run Complete Pipeline" / "Sync ESI" / "Backup Now" by hand.
                </Text>
              )}
            </Card>
          )}

          <Card withBorder padding="lg" radius="md">
            <Group justify="space-between" mb="sm">
              <Title order={6} c="dimmed" tt="uppercase">Backups</Title>
              <Button
                size="xs" loading={createBackup.isPending}
                onClick={() => createBackup.mutate(undefined, {
                  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio', 'backups'] }),
                })}
              >
                Backup Now
              </Button>
            </Group>
            <Text size="xs" c="dimmed" mb="sm">
              Backs up the full database plus config.yaml. Kept under data/backups/; the oldest are pruned
              automatically once there are more than 14.
            </Text>
            {backups && backups.length === 0 && (
              <Text size="sm" c="dimmed">No backups yet.</Text>
            )}
            {backups && backups.length > 0 && (
              <Stack gap={4}>
                {backups.slice(0, 5).map((b) => (
                  <Group key={b.name} justify="space-between">
                    <Text size="xs" ff="monospace">{b.name}</Text>
                    <Text size="xs" c="dimmed">{dateTime(b.created_at)} - {formatBytes(b.size_bytes)}</Text>
                  </Group>
                ))}
                {backups.length > 5 && (
                  <Text size="xs" c="dimmed">+ {backups.length - 5} more in data/backups/</Text>
                )}
              </Stack>
            )}
          </Card>

          <HintCard>Everything above is read-only except "Backup Now", which writes a new file under data/backups/.</HintCard>
        </Stack>
      )}
    </Container>
  )
}
