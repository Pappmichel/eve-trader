import { useQuery } from '@tanstack/react-query'
import { Container, Title, Text, SimpleGrid, Card, Group, Stack, Button, Loader, Center } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

import { portfolioApi } from '../api/client'
import { isk, qty, pct } from '../format'

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
        </Stack>
      )}
    </Container>
  )
}
