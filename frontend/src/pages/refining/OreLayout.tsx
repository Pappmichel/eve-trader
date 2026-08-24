import { AppShell, Burger, Stack, Title, Text, Button, Group, Tabs, Container, Divider } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconArrowLeft, IconDownload, IconRefresh } from '@tabler/icons-react'

import { refiningApi } from '../../api/client'
import { useAction, warnIfPricedViaFallback } from '../../hooks/useAction'
import { dateTime } from '../../format'

const TABS = [
  { path: '/ore', label: 'Ore Shortlist' },
  { path: '/ore/reprocessing', label: 'Reprocessing' },
  { path: '/ore/shopping-list', label: 'Mineral Shopping List' },
  { path: '/ore/settings', label: 'Settings' },
]

export default function OreLayout() {
  const [opened, { toggle }] = useDisclosure(true)
  const location = useLocation()
  const navigate = useNavigate()

  const { data: syncTime } = useQuery({ queryKey: ['refining', 'esi-sync-time'], queryFn: refiningApi.esiSyncTime })
  const addCandidates = useAction('Add Candidates', refiningApi.addCandidates, [
    ['refining', 'shortlist', 'items'],
  ])
  const refresh = useAction('Refresh Ore Shortlist', refiningApi.refreshShortlist, [
    ['refining', 'shortlist', 'snapshot'], ['refining', 'esi-sync-time'],
  ])

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Ore &amp; Minerals</Text>
          </Group>
          <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => navigate('/')}>Tools</Button>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap="md">
          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Login</Title>
            <Text size="xs" c="dimmed">
              Buys compressed ore/ice at Jita, sells refined minerals at C-J - reuses Trading's own Seller
              character login (Trading → Login → Seller), no separate login needed here.
            </Text>
          </div>

          <Divider />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">ESI Sync</Title>
              <Text size="xs" c="dimmed">{dateTime(syncTime?.synced_at)}</Text>
            </Group>
            <Text size="xs" c="dimmed">Time of the last Refresh Ore Shortlist run.</Text>
          </div>

          <Divider />

          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Workflow</Title>
            <Stack gap="xs">
              <Button size="xs" variant="default" leftSection={<IconDownload size={14} />}
                onClick={() => addCandidates.mutate()} loading={addCandidates.isPending}>
                Add Candidates
              </Button>
              <Button size="xs" leftSection={<IconRefresh size={14} />}
                onClick={() => refresh.mutate(undefined, { onSuccess: warnIfPricedViaFallback })} loading={refresh.isPending}>
                Refresh Ore Shortlist
              </Button>
              <Text size="xs" c="dimmed">
                Add Candidates pulls in any new compressed ore/ice type from the SDE (run once, or again after a
                Refresh SDE). Refresh Ore Shortlist re-prices everything and recomputes profit.
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
