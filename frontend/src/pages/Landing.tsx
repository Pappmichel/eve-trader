import { Container, Title, Text, SimpleGrid, Button, Group, Badge, Alert } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { authApi, gateApi } from '../api/client'
import { NotificationBell } from '../components/NotificationBell'
import { PortfolioTile, ProductionTile, TradingTile } from '../components/LandingTiles'
import { ToolCard } from '../components/ToolCard'
import { useAction } from '../hooks/useAction'
import { openGateAccessConfirmModal } from '../roleAccessDescriptions'
import { CHARACTER_MANAGEMENT_TOOL_KEYS } from '../toolKeys'

// Browser-local acknowledgement for the identity-only gate login. Before a
// first gate login there's no tenant yet to attach a server-side record
// to (that's what this login resolves). Repeat logins from the same
// browser skip the dialog; gate reads no game data.
const GATE_CONSENT_KEY = 'eve-trader:gate-consent-acknowledged'

// Only rendered once gateStatus.enabled is true (see AccessConfig.
// access_gate_enabled - on by default; a local/dev install that opts out
// in config.yaml never shows this at all, matching how it behaved before
// the gate existed).
function AccessGateStatus() {
  const { data: gateStatus } = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const logout = useAction('Log Out', gateApi.logout, [['gate', 'status']])
  // /api/auth/gate/start returns {url}; this page navigates to it, same as
  // the Characters page Re-authorize button.
  const login = useAction('Login', async () => {
    const { url } = await authApi.start()
    window.location.href = url
  })

  const startLogin = () => {
    let alreadyAcknowledged = false
    try {
      alreadyAcknowledged = localStorage.getItem(GATE_CONSENT_KEY) === '1'
    } catch {
      // Storage unavailable (private browsing, blocked cookies, ...) - fall
      // through to showing the confirmation again rather than crashing.
    }
    if (alreadyAcknowledged) {
      login.mutate()
      return
    }
    openGateAccessConfirmModal(() => {
      try {
        localStorage.setItem(GATE_CONSENT_KEY, '1')
      } catch {
        // Best-effort - a failed write just means this shows again next
        // time, not a reason to block the login itself.
      }
      login.mutate()
    })
  }

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
        <Button size="xs" onClick={startLogin} loading={login.isPending}>
          Login with EVE Online
        </Button>
      )}
    </Group>
  )
}

export default function Landing() {
  const { data: gateStatus } = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const tools = gateStatus?.tools

  return (
    <Container size="md" py="xl">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>
          C-J Import & Manufacturing
        </Text>
        <NotificationBell />
      </Group>
      <Title order={1} mb="lg">EVE Trader</Title>
      <Text c="dimmed" mb="xl">Margins, buy/build decisions and live market data in one place.</Text>

      {/* GitHub issue #104: the login prompt used to render before any of the
          explanation above, reading as a login wall rather than a real
          landing page for a first-time visitor - now it comes after, once
          they know what they'd be logging into. */}
      <AccessGateStatus />

      {gateStatus?.suspended && (
        <Alert color="danger" title="Access suspended" mb="lg">
          Your corporation or alliance is no longer allowlisted. Your data is unchanged.
          Log out and back in to re-check immediately, or wait for the next automatic check.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }} spacing="md">
        <ToolCard tools={tools} toolKey="trading" to="/trading" title="Trading" kpi={gateStatus && <TradingTile />}
          description="C-J import trading: candidate search, shortlist, margins, trade reconciliation." />
        <ToolCard tools={tools} toolKey="production" to="/production" title="Production" kpi={gateStatus && <ProductionTile />}
          description="Stock targets, buy-vs-build decisions, buy/build lists for T2 manufacturing." />
        <ToolCard tools={tools} toolKey="doctrine" to="/doctrine" title="Doctrine"
          description="Fleet doctrine fittings, contract validation against C-J stock contracts, stockpile tracking." />
        <ToolCard tools={tools} toolKey="refining" to="/ore" title="Ore &amp; Minerals"
          description="Import compressed ore/ice, refine at C-J, sell minerals for profit." />
        <ToolCard tools={tools} toolKey="station_trading" to="/station-trading" title="Station Trading"
          description="Buy and sell on Jita's own order book, profiting from the bid-ask spread." />
        <ToolCard tools={tools} toolKey="sorting" to="/sorting" title="Sorting"
          description="See what's sitting in the intake hangars and which tool still wants it." />
        <ToolCard tools={tools} toolKey="module_reprocessing" to="/modules" title="Module Reprocessing"
          description="Import T1/Meta modules and drones, reprocess at C-J, sell minerals for profit." />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }} spacing="md" mt="md">
        <ToolCard tools={tools} toolKey="portfolio" to="/portfolio" title="Portfolio Overview" kpi={gateStatus && <PortfolioTile />}
          description="Combined read-only snapshot of Trading realized profit and Production stock value." />
        <ToolCard tools={tools} toolKey={CHARACTER_MANAGEMENT_TOOL_KEYS} to="/character-management" title="Character Management"
          description="Your EVE characters: ESI access and sharing, with more character tools to come." />
        <ToolCard tools={tools} toolKey="admin" to="/admin" title="Admin"
          description="Manage tenants, users, and which tools each character can see."
          badge={
            gateStatus?.pending_access_requests
              ? `${gateStatus.pending_access_requests} pending`
              : undefined
          } />
      </SimpleGrid>

      <Text size="xs" c="dimmed" ta="center" mt="xl">
        EVE Trader is an unofficial third-party tool, not affiliated with or endorsed by CCP hf.
        EVE, EVE Online, CCP, and all related logos and trademarks are the property of CCP hf.
      </Text>
    </Container>
  )
}
