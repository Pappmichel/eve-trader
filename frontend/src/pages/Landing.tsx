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

// Each card below is only rendered if `tools` contains its own tool_key -
// `tools` is undefined while /api/gate/status hasn't loaded yet (or the gate
// is disabled, in which case the backend already returns every tool_key -
// see gate.py's status handler), so `undefined` means "show everything",
// matching this app's pre-tool-grants behavior for local/dev installs
// rather than flashing an empty page during the initial load.
function ToolCard({ tools, toolKey, to, title, description }: {
  tools: string[] | undefined
  toolKey: string
  to: string
  title: string
  description: string
}) {
  if (tools !== undefined && !tools.includes(toolKey)) return null
  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="xs">
        <Title order={3}>{title}</Title>
        <Text c="dimmed" size="sm">{description}</Text>
        <Button component={Link} to={to} mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
      </Stack>
    </Card>
  )
}

export default function Landing() {
  const { data: gateStatus } = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const tools = gateStatus?.tools

  return (
    <Container size="md" py="xl">
      <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>
        C-J Import & Manufacturing
      </Text>
      <Title order={1} mb="lg">EVE Trader</Title>
      <Text c="dimmed" mb="xl">Margins, buy/build decisions and live market data in one place.</Text>

      {/* GitHub issue #104: the login prompt used to render before any of the
          explanation above, reading as a login wall rather than a real
          landing page for a first-time visitor - now it comes after, once
          they know what they'd be logging into. */}
      <AccessGateStatus />

      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }} spacing="md">
        <ToolCard tools={tools} toolKey="trading" to="/trading" title="Trading"
          description="C-J import trading: candidate search, shortlist, margins, trade reconciliation." />
        <ToolCard tools={tools} toolKey="production" to="/production" title="Production"
          description="Stock targets, buy-vs-build decisions, buy/build lists for T2 manufacturing." />
        <ToolCard tools={tools} toolKey="doctrine" to="/doctrine" title="Doctrine"
          description="Fleet doctrine fittings, contract validation against C-J stock contracts, stockpile tracking." />
        <ToolCard tools={tools} toolKey="refining" to="/ore" title="Ore &amp; Minerals"
          description="Import compressed ore/ice, refine at C-J, sell minerals for profit." />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="md" mt="md">
        <ToolCard tools={tools} toolKey="portfolio" to="/portfolio" title="Portfolio Overview"
          description="Combined read-only snapshot of Trading realized profit and Production stock value." />
        <ToolCard tools={tools} toolKey="admin" to="/admin" title="Admin"
          description="Manage tenants, users, and which tools each character can see." />
      </SimpleGrid>

      <Text size="xs" c="dimmed" ta="center" mt="xl">
        EVE Trader is an unofficial third-party tool, not affiliated with or endorsed by CCP hf.
        EVE, EVE Online, CCP, and all related logos and trademarks are the property of CCP hf.
      </Text>
    </Container>
  )
}
