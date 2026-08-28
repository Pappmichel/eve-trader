import { AppShell, Burger, Stack, Title, Text, Button, Group, Container, Tabs, ScrollArea, Divider } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { modals } from '@mantine/modals'
import { IconArrowLeft, IconRefresh } from '@tabler/icons-react'

import { stationTradingApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { useRoleCharacters } from '../../hooks/useRoleCharacters'
import { dateTime } from '../../format'

const TABS = [
  { path: '/station-trading', label: 'Overview' },
  { path: '/station-trading/shortlist', label: 'Shortlist' },
  { path: '/station-trading/undercut', label: 'Undercut Check' },
  { path: '/station-trading/settings', label: 'Settings' },
]

export default function StationTradingLayout() {
  const [opened, { toggle }] = useDisclosure(true)
  const location = useLocation()
  const navigate = useNavigate()

  const { data: syncTime } = useQuery({
    queryKey: ['station-trading', 'esi-sync-time'], queryFn: stationTradingApi.esiSyncTime,
  })
  const { characters, addCharacter, startLogin, removeCharacter, isRemoving } = useRoleCharacters(
    ['station-trading', 'characters'], stationTradingApi.traderCharacters, stationTradingApi.removeCharacter, 'trader',
  )
  const refreshShortlist = useAction('Refresh Shortlist', stationTradingApi.refreshShortlist, [
    ['station-trading', 'shortlist'], ['station-trading', 'esi-sync-time'],
  ])

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 300, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Station Trading</Text>
          </Group>
          <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => navigate('/')}>Tools</Button>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p={0} style={{ display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 'var(--mantine-spacing-md)', borderBottom: '1px solid var(--mantine-color-dark-4)' }}>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">Station Trading</Title>
          <Button size="xs" fullWidth leftSection={<IconRefresh size={14} />}
            onClick={() => refreshShortlist.mutate()} loading={refreshShortlist.isPending}>
            Refresh Shortlist
          </Button>
        </div>

        <ScrollArea style={{ flex: 1 }} p="md">
          <Stack gap="lg">
            <div>
              <Group justify="space-between" mb="xs" wrap="nowrap">
                <Title order={6} c="dimmed" tt="uppercase">Trader Characters</Title>
                <Text size="xs" c="dimmed">{dateTime(syncTime?.synced_at)}</Text>
              </Group>
              <Text size="xs" c="dimmed" mb="xs">
                Buying and selling on Jita's own order book - a separate login from Trading's buyer/seller
                characters, since this needs your skill levels too.
              </Text>
              {characters.map((c) => (
                <Group key={c.role_key} justify="space-between" mb={4}>
                  <Text size="sm" fw={600}>{c.character_name}</Text>
                  <Button size="xs" variant="subtle" color="danger"
                    onClick={() => modals.openConfirmModal({
                      title: 'Remove character',
                      children: <Text size="sm">Remove {c.character_name} from Station Trading? You can log them back in any time.</Text>,
                      labels: { confirm: 'Remove', cancel: 'Cancel' },
                      confirmProps: { color: 'danger' },
                      onConfirm: () => removeCharacter(c.role_key),
                    })}
                    loading={isRemoving(c.role_key)}>
                    Remove
                  </Button>
                </Group>
              ))}
              <Button size="xs" variant="default" mt="xs" onClick={() => startLogin()} loading={addCharacter.isPending}>
                Add Character
              </Button>
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
