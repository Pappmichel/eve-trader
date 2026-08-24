import { AppShell, Badge, Burger, Stack, Title, Text, Button, Group, Container, Tabs, ScrollArea, Divider } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { modals } from '@mantine/modals'
import { spotlight } from '@mantine/spotlight'
import { IconArrowLeft, IconSearch } from '@tabler/icons-react'

import { productionApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { useRoleCharacters } from '../../hooks/useRoleCharacters'
import { dateTime } from '../../format'

const TABS = [
  { path: '/production', label: 'Overview' },
  { path: '/production/stock-targets', label: 'Stock Targets' },
  { path: '/production/market', label: 'Market Status' },
  { path: '/production/jobs', label: 'Industry Jobs' },
  { path: '/production/slots', label: 'Character Slots' },
  { path: '/production/buy', label: 'Buy List' },
  { path: '/production/build', label: 'Build List' },
  { path: '/production/asset-plan', label: 'Build List (Asset-Optimized)' },
  { path: '/production/logistics', label: 'Logistics' },
  { path: '/production/invention', label: 'Invention' },
  { path: '/production/blueprints', label: 'Blueprints' },
  { path: '/production/unlisted-stock', label: 'Unlisted Stock' },
  { path: '/production/build-candidates', label: 'Build Candidates' },
  { path: '/production/margin', label: 'Margin' },
  { path: '/production/material-tree', label: 'Material Tree' },
  { path: '/production/asset-search', label: 'Asset Search' },
  { path: '/production/settings', label: 'Settings' },
]

export default function ProductionLayout() {
  const [opened, { toggle }] = useDisclosure(true)
  const location = useLocation()
  const navigate = useNavigate()

  const { data: sdeCounts } = useQuery({ queryKey: ['production', 'sde', 'counts'], queryFn: productionApi.sdeCounts })
  const { data: sdeFreshness } = useQuery({
    queryKey: ['production', 'sde-freshness'], queryFn: productionApi.sdeFreshness,
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false,
  })
  const { data: syncTime } = useQuery({ queryKey: ['production', 'esi-sync-time'], queryFn: productionApi.esiSyncTime })

  // No "Refresh SDE" action/button here anymore - moved to the Admin tool
  // (GitHub issue #34: the SDE cache is global/shared across every tenant,
  // so triggering a refresh is a cross-tenant-impacting action). The
  // freshness/counts info below stays - still legitimately informs this
  // tenant's own Production sidebar, just no longer paired with a button
  // that would refresh it for every tenant at once.
  const { characters, addCharacter, startLogin, removeCharacter, isRemoving } = useRoleCharacters(
    ['production', 'characters'], productionApi.producerCharacters, productionApi.removeCharacter, 'producer',
  )
  const syncEsi = useAction('Sync ESI Data', productionApi.syncEsi, [
    ['production', 'jobs'], ['production', 'slots'], ['production', 'market-status'], ['production', 'esi-sync-time'],
    ['production', 'blueprints'], ['production', 'stock-value'],
  ])
  const refreshPlan = useAction('Refresh Production', productionApi.refreshPlan, [
    ['production', 'plan'], ['production', 'stock-targets'], ['production', 'logistics'],
  ])

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 300, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Production</Text>
          </Group>
          <Group gap="xs">
            <Button variant="subtle" size="xs" leftSection={<IconSearch size={14} />} onClick={() => spotlight.open()}>
              Jump to... (⌘K)
            </Button>
            <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => navigate('/')}>Tools</Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p={0} style={{ display: 'flex', flexDirection: 'column' }}>
        {/* Pinned above the scrollable character list below - stays reachable
            no matter how many producer characters are authorized (the bug
            this fixes: the compute button used to be the last item in one
            long scrolling stack, pushed off-screen by a long character list). */}
        <div style={{ padding: 'var(--mantine-spacing-md)', borderBottom: '1px solid var(--mantine-color-dark-4)' }}>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">Production</Title>
          <Button size="xs" fullWidth onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
            Compute Buy/Build List
          </Button>
        </div>

        <ScrollArea style={{ flex: 1 }} p="md">
          <Stack gap="lg">
            <div>
              <Group justify="space-between" mb="xs" wrap="nowrap">
                <Title order={6} c="dimmed" tt="uppercase">SDE Data</Title>
                {sdeFreshness && (
                  sdeFreshness.newer_sde_available ? (
                    <Badge size="xs" color="warn" variant="light">Update available</Badge>
                  ) : sdeFreshness.remote_check_succeeded ? (
                    <Badge size="xs" color="accent" variant="light">Up to date</Badge>
                  ) : (
                    <Badge size="xs" color="gray" variant="light">Check failed</Badge>
                  )
                )}
              </Group>
              <Text size="xs" c="dimmed" mb="xs">Blueprint materials/products/times from Fuzzwork.</Text>
              <Text size="xs" c="dimmed">Refreshed: {dateTime(sdeFreshness?.local_refreshed_at)}</Text>
              {sdeCounts && Object.entries(sdeCounts).map(([table, count]) => (
                <Text size="xs" c="dimmed" key={table}>{table}: {count.toLocaleString('en-US')}</Text>
              ))}
              <Text size="xs" c="dimmed" mt="xs">Refresh via Admin → SDE Data.</Text>
            </div>

            <Divider />

            <div>
              <Group justify="space-between" mb="xs" wrap="nowrap">
                <Title order={6} c="dimmed" tt="uppercase">ESI Sync</Title>
                <Text size="xs" c="dimmed">{dateTime(syncTime?.synced_at)}</Text>
              </Group>
              {characters.map((c) => (
                <Group key={c.role_key} justify="space-between" mb={4}>
                  <Text size="sm" fw={600}>{c.character_name}</Text>
                  <Button size="xs" variant="subtle" color="danger"
                    onClick={() => modals.openConfirmModal({
                      title: 'Remove character',
                      children: <Text size="sm">Remove {c.character_name} from Production? You can log them back in any time.</Text>,
                      labels: { confirm: 'Remove', cancel: 'Cancel' },
                      confirmProps: { color: 'danger' },
                      onConfirm: () => removeCharacter(c.role_key),
                    })}
                    loading={isRemoving(c.role_key)}>
                    Remove
                  </Button>
                </Group>
              ))}
              <Stack gap="xs" mt="xs">
                <Button size="xs" variant="default" onClick={() => startLogin()} loading={addCharacter.isPending}>
                  Add Character
                </Button>
                <Button size="xs" variant="default" disabled={characters.length === 0}
                  onClick={() => syncEsi.mutate()} loading={syncEsi.isPending}>
                  Sync ESI Data
                </Button>
              </Stack>
            </div>

            <Divider />

            <Text size="xs" c="dimmed">
              Unofficial third-party tool, not affiliated with or endorsed by CCP hf. EVE, EVE Online, CCP, and
              all related logos/trademarks are property of CCP hf.
            </Text>
          </Stack>
        </ScrollArea>
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
