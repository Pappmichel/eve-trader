import { Container, Title, Text, SimpleGrid, Card, Button, Stack, Group, Badge } from '@mantine/core'
import { IconArrowRight } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { authApi, gateApi } from '../api/client'
import { useAction } from '../hooks/useAction'

// Only rendered once gateStatus.enabled is true (see AccessConfig.
// access_gate_enabled - off by default) - a local/dev install with the gate
// disabled never shows this at all, matching how it behaved before the
// gate existed.
function AccessGateStatus() {
  const { data: gateStatus } = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const logout = useAction('Log Out', gateApi.logout, [['gate', 'status']])
  // /api/auth/gate/start returns {url}, same as every other LoginButton in
  // this app (see TradingLayout.tsx) - not itself a redirect, so it has to
  // be fetched and navigated to manually, not linked to directly.
  const login = useAction('Login', async () => {
    const { url } = await authApi.start('gate')
    window.location.href = url
  })

  if (!gateStatus?.enabled) return null

  return (
    <Group justify="flex-end" mb="md">
      {gateStatus.logged_in ? (
        <>
          <Badge color="accent" variant="light">{gateStatus.character_name}</Badge>
          <Button size="xs" variant="default" onClick={() => logout.mutate()} loading={logout.isPending}>
            Log Out
          </Button>
        </>
      ) : (
        <Button size="xs" onClick={() => login.mutate()} loading={login.isPending}>
          Login with EVE Online
        </Button>
      )}
    </Group>
  )
}

export default function Landing() {
  return (
    <Container size="md" py="xl">
      <AccessGateStatus />
      <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>
        C-J Import & Manufacturing
      </Text>
      <Title order={1} mb="lg">EVE Trader</Title>
      <Text c="dimmed" mb="xl">Margins, buy/build decisions and live market data in one place.</Text>

      <SimpleGrid cols={2} spacing="md">
        <Card withBorder padding="lg" radius="md">
          <Stack gap="xs">
            <Title order={3}>Trading</Title>
            <Text c="dimmed" size="sm">
              C-J import trading: candidate search, shortlist, margins, trade reconciliation.
            </Text>
            <Button component={Link} to="/trading" mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
          </Stack>
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Stack gap="xs">
            <Title order={3}>Production</Title>
            <Text c="dimmed" size="sm">
              Stock targets, buy-vs-build decisions, buy/build lists for T2 manufacturing.
            </Text>
            <Button component={Link} to="/production" mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
          </Stack>
        </Card>
      </SimpleGrid>

      <Card withBorder padding="lg" radius="md" mt="md">
        <Stack gap="xs">
          <Title order={3}>Portfolio Overview</Title>
          <Text c="dimmed" size="sm">
            Combined read-only snapshot of Trading realized profit and Production stock value.
          </Text>
          <Button component={Link} to="/portfolio" mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
        </Stack>
      </Card>

      <Text size="xs" c="dimmed" ta="center" mt="xl">
        EVE Trader is an unofficial third-party tool, not affiliated with or endorsed by CCP hf.
        EVE, EVE Online, CCP, and all related logos and trademarks are the property of CCP hf.
      </Text>
    </Container>
  )
}
