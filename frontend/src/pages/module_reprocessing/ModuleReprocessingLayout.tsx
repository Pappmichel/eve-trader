import { AppShell, Burger, Stack, Title, Text, Button, Group, Tabs, Container, Divider, Tooltip } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconArrowLeft, IconRefresh, IconSearch } from '@tabler/icons-react'

import { moduleReprocessingApi } from '../../api/client'
import { useAction, warnIfPricedViaFallback } from '../../hooks/useAction'
import { dateTime } from '../../format'

const TABS = [
  { path: '/modules', label: 'Overview' },
  { path: '/modules/discover', label: 'Discover' },
  { path: '/modules/shortlist', label: 'Shortlist' },
  { path: '/modules/settings', label: 'Settings' },
]

export default function ModuleReprocessingLayout() {
  // See OreLayout.tsx's/DoctrineLayout.tsx's own comment on this same line -
  // starting the mobile navbar drawer open covered the entire page
  // (including any table) on first mobile load, blocking touch/scroll until
  // the burger was tapped.
  const [opened, { toggle }] = useDisclosure(false)
  const location = useLocation()
  const navigate = useNavigate()

  const { data: syncTime } = useQuery({
    queryKey: ['module_reprocessing', 'esi-sync-time'], queryFn: moduleReprocessingApi.esiSyncTime,
  })
  const discover = useAction('Discover', moduleReprocessingApi.discover, [
    ['module_reprocessing', 'discover', 'results'],
  ], { tier: 'live', effect: 'Scans the full T1/Meta module+drone SDE universe against a Goonmetrics current-price snapshot - can take up to a minute.' })
  const refresh = useAction('Refresh Shortlist', moduleReprocessingApi.refreshShortlist, [
    ['module_reprocessing', 'shortlist', 'snapshot'], ['module_reprocessing', 'esi-sync-time'],
  ], { tier: 'live', effect: 'Reprices the entire shortlist live via ESI (with a Goonmetrics fallback).' })

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Module Reprocessing</Text>
          </Group>
          <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => navigate('/')}>Tools</Button>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap="md">
          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Characters</Title>
            <Text size="xs" c="dimmed">
              Buys T1/Meta modules and drones, sells refined minerals at C-J. Reuses Trading&apos;s shared
              seller tokens — share Market Orders and Wallet with Trading on the Characters page; there is
              no separate login here.
            </Text>
          </div>

          <Divider />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">ESI Sync</Title>
              <Text size="xs" c="dimmed">{dateTime(syncTime?.synced_at)}</Text>
            </Group>
            <Text size="xs" c="dimmed">Time of the last Refresh Shortlist run.</Text>
          </div>

          <Divider />

          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Workflow</Title>
            <Stack gap="xs">
              <Tooltip label={discover.tooltip} disabled={!discover.tooltip} multiline w={280}>
                <Button size="xs" variant="default" leftSection={<IconSearch size={14} />} rightSection={discover.tierIcon}
                  onClick={() => discover.mutate()} loading={discover.isPending}>
                  Discover
                </Button>
              </Tooltip>
              <Tooltip label={refresh.tooltip} disabled={!refresh.tooltip} multiline w={280}>
                <Button size="xs" leftSection={<IconRefresh size={14} />} rightSection={refresh.tierIcon}
                  onClick={() => refresh.mutate(undefined, { onSuccess: warnIfPricedViaFallback })} loading={refresh.isPending}>
                  Refresh Shortlist
                </Button>
              </Tooltip>
              <Text size="xs" c="dimmed">
                Discover scans the full candidate universe and estimates profitability from Goonmetrics -
                pick items on the Discover tab to add them. Refresh Shortlist reprices everything you've
                added and recomputes profit.
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
