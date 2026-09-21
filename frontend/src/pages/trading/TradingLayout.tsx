import { AppShell, Burger, Stack, Title, Text, Button, Group, Badge, Tabs, Container, Divider, Tooltip } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { spotlight } from '@mantine/spotlight'
import {
  IconArrowLeft, IconBolt, IconCircleNumber1, IconCircleNumber2, IconPlayerPlay, IconSearch,
} from '@tabler/icons-react'

import { productionApi, tradingApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { useTradingPipelineJob } from '../../hooks/useRefreshAndPruneJob'
import { useRoleCharacters } from '../../hooks/useRoleCharacters'
import { ActionTierIcon, TIER_COPY } from '../../components/ActionTierIcon'
import { dateTime } from '../../format'

// The four Daily-Workflow/Candidate-Setup buttons below all run through
// useTradingPipelineJob's shared background-job lock, not useAction, so
// they build their own tooltip/icon directly (all 'live' - every one of
// them calls ESI and/or Goonmetrics) instead of getting it from the hook.
const liveJobTooltip = (effect: string) => `${effect} ${TIER_COPY.live}`

const TABS = [
  { path: '/trading', label: 'Overview' },
  { path: '/trading/shortlist', label: 'Shortlist' },
  { path: '/trading/candidates', label: 'Candidate Universe' },
  { path: '/trading/new-candidates', label: 'New Candidates' },
  { path: '/trading/history', label: 'Price History' },
  { path: '/trading/trades', label: 'Realized Trades' },
  { path: '/trading/transactions', label: 'Transactions' },
  { path: '/trading/unlisted-stock', label: 'Unlisted Stock' },
  { path: '/trading/undercut', label: 'Undercut Check' },
  { path: '/trading/settings', label: 'Settings' },
]

// GitHub issue #46: multiple characters supported (more registered
// characters = more available order slots). Buyer/Seller merged into one
// sharing-based list server-side (bug found 2026-09-21, same class as
// docs/ESI_ACCESS_PLAN.md's Known gap 4 - buyerCharacters/sellerCharacters
// now return the identical list, since the sharing model has no buyer-vs-
// seller distinction; see actions.list_shared_trading_characters's own
// docstring) - rendering them as two separate sections would just show
// every character twice. List only; ESI login and token removal are the
// Characters page.
function RoleCharacters() {
  const { characters } = useRoleCharacters(
    ['trading', 'characters', 'shared'], tradingApi.sellerCharacters,
  )

  return (
    <div>
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">Shared with Trading</Title>
      {characters.length === 0 && (
        <Badge color="danger" variant="light" mb="xs">none shared</Badge>
      )}
      {characters.length === 0 && (
        <Text size="xs" c="dimmed" mb="xs">Share Wallet, Market Orders, and/or Assets on the Characters page.</Text>
      )}
      <Stack gap={4} mb="xs">
        {characters.map((c) => (
          <Text key={c.role_key} size="sm" fw={600}>{c.character_name}</Text>
        ))}
      </Stack>
      <Text size="xs" c="dimmed">
        To drop a character&apos;s token, use the Characters page.
      </Text>
    </div>
  )
}

export default function TradingLayout() {
  // Starts closed, not open - only `collapsed.mobile` (below) is driven by
  // this state (desktop's navbar visibility is unaffected either way), but
  // starting open meant the drawer covered the entire page on first mobile
  // load, including the Shortlist table underneath, blocking touch/scroll
  // input to it until the user found and tapped the burger - confirmed real
  // bug (mobile "can't scroll the shortlist table right" report), and the
  // same pattern across every tool layout, not just this one (see the other
  // five `useDisclosure` call sites: Doctrine/Production/Ore/Sorting/Station
  // Trading Layout).
  const [opened, { toggle }] = useDisclosure(false)
  const location = useLocation()
  const navigate = useNavigate()

  const { data: syncTime } = useQuery({ queryKey: ['trading', 'esi-sync-time'], queryFn: tradingApi.esiSyncTime })
  // Same query key/shared cache as ProductionLayout's own SDE-freshness
  // check (App.tsx's SdeFreshnessChecker fires the actual fetch) - reused
  // here purely to surface trading_universe_stale next to "Load Market
  // Groups", the action that would resolve it.
  const { data: sdeFreshness } = useQuery({
    queryKey: ['production', 'sde-freshness'], queryFn: productionApi.sdeFreshness,
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false,
  })

  const buildUniverse = useAction('Load Market Groups', tradingApi.buildUniverse,
    [['trading', 'candidates', 'universe'], ['production', 'sde-freshness']],
    { tier: 'live', effect: 'Loads EVE\'s entire market group structure live from ESI.' })
  const buildFocused = useAction('Filter Candidates', tradingApi.buildFocused, [['trading', 'candidates', 'focused']],
    { tier: 'local', effect: 'Filters the already-loaded market groups locally for candidates - loads nothing new from ESI.' })
  // Refresh Shortlist, Search+Add+Clean Up, and Run Complete Pipeline share
  // one background-job lock (POST returns immediately; this hook polls GET
  // .../status until status != running). A second click while one is running
  // is a 409 rather than a second thread.
  const tradingJob = useTradingPipelineJob()
  const reconcile = useAction('Reconcile Trades', tradingApi.reconcileTrades, [['trading', 'trades', 'realized']],
    { tier: 'live', effect: 'Matches wallet transactions live from ESI against open positions.' })

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Trading</Text>
          </Group>
          <Group gap="xs">
            <Button variant="subtle" size="xs" leftSection={<IconSearch size={14} />} onClick={() => spotlight.open()}>
              Jump to... (⌘K)
            </Button>
            <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => navigate('/')}>Tools</Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap="md">
          <RoleCharacters />

          <Divider />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">ESI Sync</Title>
              <Text size="xs" c="dimmed">{dateTime(syncTime?.synced_at)}</Text>
            </Group>
            <Text size="xs" c="dimmed">
              Time of the last run that fetched live ESI data (Refresh Shortlist / Search+Add+Clean Up / Pipeline).
            </Text>
          </div>

          <Divider />

          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Daily Workflow</Title>
            <Stack gap="xs">
              <Tooltip label={liveJobTooltip('Loads current prices/stock live from ESI (with a Goonmetrics fallback) and updates the shortlist.')} multiline w={280}>
                <Button size="xs" variant={tradingJob.isJob('refresh_shortlist') ? 'light' : 'default'}
                  leftSection={<ActionTierIcon tier="live" />}
                  onClick={() => tradingJob.startRefreshShortlist()}>
                  Refresh Shortlist
                </Button>
              </Tooltip>
              <Tooltip label={liveJobTooltip('Searches live via Goonmetrics/ESI for new import candidates, adds good hits, and cleans up the shortlist.')} multiline w={280}>
                <Button size="xs" leftSection={<IconBolt size={14} />}
                  rightSection={<ActionTierIcon tier="live" />}
                  variant={tradingJob.isJob('refresh_and_prune') ? 'light' : undefined}
                  onClick={() => tradingJob.startRefreshAndPrune(true)}>
                  Search + Add + Clean Up
                </Button>
              </Tooltip>
              {tradingJob.progressLabel && (
                <Text size="xs" c="dimmed">{tradingJob.progressLabel}</Text>
              )}
              <Tooltip label={reconcile.tooltip} disabled={!reconcile.tooltip} multiline w={280}>
                <Button size="xs" variant="default" leftSection={reconcile.tierIcon}
                  onClick={() => reconcile.mutate()} loading={reconcile.isPending}>
                  Reconcile Trades
                </Button>
              </Tooltip>
              <Tooltip label={liveJobTooltip('Runs sync, search, add, and shortlist cleanup all in one go.')} multiline w={280}>
                <Button size="xs" leftSection={<IconPlayerPlay size={14} />}
                  rightSection={<ActionTierIcon tier="live" />}
                  variant={tradingJob.isJob('pipeline') ? 'light' : 'default'}
                  onClick={() => tradingJob.startPipeline()}>
                  Run Complete Pipeline
                </Button>
              </Tooltip>
            </Stack>
          </div>

          <Divider />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">Candidate Setup (rare)</Title>
              {sdeFreshness && (
                sdeFreshness.trading_universe_stale ? (
                  <Badge size="xs" color="warn" variant="light">SDE updated since</Badge>
                ) : (
                  <Badge size="xs" color="accent" variant="light">Up to date</Badge>
                )
              )}
            </Group>
            <Stack gap="xs">
              <Tooltip label={buildUniverse.tooltip} disabled={!buildUniverse.tooltip} multiline w={280}>
                <Button size="xs" variant="default" leftSection={<IconCircleNumber1 size={14} />}
                  rightSection={buildUniverse.tierIcon}
                  onClick={() => buildUniverse.mutate()} loading={buildUniverse.isPending}>
                  Load Market Groups
                </Button>
              </Tooltip>
              <Tooltip label={buildFocused.tooltip} disabled={!buildFocused.tooltip} multiline w={280}>
                <Button size="xs" variant="default" leftSection={<IconCircleNumber2 size={14} />}
                  onClick={() => buildFocused.mutate()} loading={buildFocused.isPending}>
                  Filter Candidates
                </Button>
              </Tooltip>
              <Tooltip label={liveJobTooltip('Same as Search+Add+Clean Up, but scans the entire candidate pool instead of a 500-item window.')} multiline w={280}>
                <Button size="xs" variant="light" color="warn" leftSection={<IconSearch size={14} />}
                  rightSection={<ActionTierIcon tier="live" />}
                  onClick={() => tradingJob.startRefreshAndPrune(false)}>
                  Full Search (ALL candidates)
                </Button>
              </Tooltip>
              {tradingJob.progressLabel && (
                <Text size="xs" c="dimmed">{tradingJob.progressLabel}</Text>
              )}
              <Text size="xs" c="dimmed">
                Backtests every remaining candidate instead of a 500 window - runs in the background,
                so leaving this page is safe. Results are saved as they come in, not just at the end.
              </Text>
            </Stack>
          </div>

          <Divider />

          <Text size="xs" c="dimmed">
            Unofficial third-party tool, not affiliated with or endorsed by CCP hf. EVE, EVE Online, CCP, and
            all related logos/trademarks are property of CCP hf.
          </Text>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        <Container size="xl" px={0}>
          <Tabs value={location.pathname} onChange={(v) => v && navigate(v)} mb="md">
            <Tabs.List>
              {TABS.map((t) => (
                <Tabs.Tab key={t.path} value={t.path}>
                  {t.label}
                </Tabs.Tab>
              ))}
            </Tabs.List>
          </Tabs>
          <Outlet />
        </Container>
      </AppShell.Main>
    </AppShell>
  )
}
