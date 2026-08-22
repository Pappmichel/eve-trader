import { AppShell, Burger, Stack, Title, Text, Button, Group, Badge, Tabs, Container, Divider } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  IconArrowLeft, IconBolt, IconCircleNumber1, IconCircleNumber2, IconPlayerPlay, IconSearch,
} from '@tabler/icons-react'

import { productionApi, tradingApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { useRoleCharacters } from '../../hooks/useRoleCharacters'
import { dateTime } from '../../format'

const TABS = [
  { path: '/trading', label: 'Shortlist' },
  { path: '/trading/candidates', label: 'Candidate Universe' },
  { path: '/trading/new-candidates', label: 'New Candidates' },
  { path: '/trading/history', label: 'Price History' },
  { path: '/trading/trades', label: 'Realized Trades' },
  { path: '/trading/unlisted-stock', label: 'Unlisted Stock' },
  { path: '/trading/undercut', label: 'Undercut Check' },
  { path: '/trading/settings', label: 'Settings' },
]

// GitHub issue #46: buyer/seller support multiple characters now (more
// registered characters = more available order slots), not one fixed
// role each - lists every registered character for `role` with a Remove
// button, plus an "Add Character" login button, same shape
// ProductionLayout.tsx already uses for producer characters.
function RoleCharacters({ role, label }: { role: 'buyer' | 'seller'; label: string }) {
  const fetchCharacters = role === 'buyer' ? tradingApi.buyerCharacters : tradingApi.sellerCharacters
  const { characters, addCharacter, removeCharacter, isRemoving } = useRoleCharacters(
    ['trading', 'characters', role], fetchCharacters, tradingApi.removeCharacter, role,
  )

  return (
    <div>
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{label}</Title>
      {characters.length === 0 && (
        <Badge color="danger" variant="light" mb="xs">not logged in</Badge>
      )}
      <Stack gap={4} mb="xs">
        {characters.map((c) => (
          <Group key={c.role_key} justify="space-between" wrap="nowrap">
            <Text size="sm" fw={600}>{c.character_name}</Text>
            <Button size="xs" variant="subtle" color="danger"
              onClick={() => removeCharacter(c.role_key)} loading={isRemoving(c.role_key)}>
              Remove
            </Button>
          </Group>
        ))}
      </Stack>
      <Button size="xs" variant="default" onClick={() => addCharacter.mutate()} loading={addCharacter.isPending}>
        Add {label}
      </Button>
    </div>
  )
}

export default function TradingLayout() {
  const [opened, { toggle }] = useDisclosure(true)
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
    [['trading', 'candidates', 'universe'], ['production', 'sde-freshness']])
  const buildFocused = useAction('Filter Candidates', tradingApi.buildFocused, [['trading', 'candidates', 'focused']])
  const refreshShortlist = useAction('Refresh Shortlist', tradingApi.refreshShortlist, [
    ['trading', 'shortlist', 'snapshot'], ['trading', 'kpis'], ['trading', 'esi-sync-time'],
  ])
  // Finds new candidates, adds the recommended ones, and prunes stale/over-cap
  // items - all three in one action since there's no manual review step
  // between "found" and "added" (add_flag is purely algorithmic, see
  // NewCandidates.tsx) that would justify separate buttons for each step.
  const refreshAndPrune = useAction('Search, Add & Clean Up Candidates',
    () => tradingApi.refreshAndPruneCandidates(true), [
      ['trading', 'shortlist', 'snapshot'], ['trading', 'shortlist', 'items'],
      ['trading', 'candidates', 'new'], ['trading', 'kpis'], ['trading', 'esi-sync-time'],
    ])
  // Full (safe=false) search: backtests every remaining candidate in one run
  // instead of a 500-sized window - can take several minutes (thousands of
  // Goonmetrics/ESI requests). Results are saved incrementally batch-by-batch
  // on the backend (history_backtest.find_new_import_candidates' results_sink),
  // so even if this gets interrupted partway through, whatever was already
  // scored is kept, not lost. Also runs add+prune afterward, same as ⚡ -
  // otherwise a later ⚡ click would start a newer run and its add step would
  // only see its own (windowed) results, silently ignoring this run's.
  const fullSearch = useAction('Full Search + Add & Clean Up',
    () => tradingApi.refreshAndPruneCandidates(false), [
      ['trading', 'shortlist', 'snapshot'], ['trading', 'shortlist', 'items'],
      ['trading', 'candidates', 'new'], ['trading', 'kpis'], ['trading', 'esi-sync-time'],
    ])
  const reconcile = useAction('Reconcile Trades', tradingApi.reconcileTrades, [['trading', 'trades', 'realized']])
  const runPipeline = useAction('Pipeline', () => tradingApi.runPipeline(true, false), [
    ['trading', 'shortlist', 'snapshot'], ['trading', 'shortlist', 'items'], ['trading', 'candidates', 'new'],
    ['trading', 'trades', 'realized'], ['trading', 'kpis'], ['trading', 'esi-sync-time'],
  ])

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Trading</Text>
          </Group>
          <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => navigate('/')}>Tools</Button>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap="md">
          <RoleCharacters role="buyer" label="Buyer" />
          <RoleCharacters role="seller" label="Seller" />

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
              <Button size="xs" variant="default" onClick={() => refreshShortlist.mutate()} loading={refreshShortlist.isPending}>
                Refresh Shortlist
              </Button>
              <Button size="xs" leftSection={<IconBolt size={14} />} onClick={() => refreshAndPrune.mutate()} loading={refreshAndPrune.isPending}>
                Search + Add + Clean Up
              </Button>
              <Button size="xs" variant="default" onClick={() => reconcile.mutate()} loading={reconcile.isPending}>
                Reconcile Trades
              </Button>
              <Button size="xs" leftSection={<IconPlayerPlay size={14} />} onClick={() => runPipeline.mutate()} loading={runPipeline.isPending}>
                Run Complete Pipeline
              </Button>
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
              <Button size="xs" variant="default" leftSection={<IconCircleNumber1 size={14} />}
                onClick={() => buildUniverse.mutate()} loading={buildUniverse.isPending}>
                Load Market Groups
              </Button>
              <Button size="xs" variant="default" leftSection={<IconCircleNumber2 size={14} />}
                onClick={() => buildFocused.mutate()} loading={buildFocused.isPending}>
                Filter Candidates
              </Button>
              <Button size="xs" variant="light" color="warn" leftSection={<IconSearch size={14} />}
                onClick={() => fullSearch.mutate()} loading={fullSearch.isPending}>
                Full Search (ALL candidates)
              </Button>
              <Text size="xs" c="dimmed">
                Backtests every remaining candidate instead of a 500 window - can take several minutes. Safe to
                interrupt: results are saved as they come in, not just at the end.
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
