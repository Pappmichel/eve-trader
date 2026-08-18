import { AppShell, Burger, Stack, Title, Text, Button, Group, Tabs, Container, Divider, ActionIcon } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconArrowLeft, IconRefresh, IconTrash } from '@tabler/icons-react'

import { doctrineApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { dateTime } from '../../format'

const TABS = [
  { path: '/doctrine', label: 'Doctrines' },
  { path: '/doctrine/contracts', label: 'Contracts' },
  { path: '/doctrine/stockpile', label: 'Stockpile' },
  { path: '/doctrine/settings', label: 'Settings' },
]

// A fitting/detail sub-page (path has extra segments beyond a known tab)
// still highlights its parent tab - Tabs.value requires an exact match
// otherwise none of the TABS above would show as active while viewing a
// single doctrine or fitting.
function activeTab(pathname: string): string {
  if (pathname.startsWith('/doctrine/contracts')) return '/doctrine/contracts'
  if (pathname.startsWith('/doctrine/stockpile')) return '/doctrine/stockpile'
  if (pathname.startsWith('/doctrine/settings')) return '/doctrine/settings'
  return '/doctrine'
}

function CharacterList() {
  const { data: characters } = useQuery({ queryKey: ['doctrine', 'characters'], queryFn: doctrineApi.characters })
  const addCharacter = useAction('Add Character', doctrineApi.addCharacter, [['doctrine', 'characters']])
  const removeCharacter = useAction('Remove Character', doctrineApi.removeCharacter, [['doctrine', 'characters']])

  return (
    <div>
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">Characters</Title>
      <Stack gap="xs">
        {(characters ?? []).map((c) => (
          <Group key={c.role_key} justify="space-between" wrap="nowrap">
            <Text size="sm">{c.character_name}</Text>
            <ActionIcon size="sm" variant="subtle" color="danger"
              onClick={() => removeCharacter.mutate(c.role_key)} loading={removeCharacter.isPending}>
              <IconTrash size={14} />
            </ActionIcon>
          </Group>
        ))}
        <Button size="xs" variant="default" onClick={() => addCharacter.mutate()} loading={addCharacter.isPending}>
          Add Character
        </Button>
      </Stack>
    </div>
  )
}

export default function DoctrineLayout() {
  const [opened, { toggle }] = useDisclosure(true)
  const location = useLocation()
  const navigate = useNavigate()

  const { data: syncTime } = useQuery({ queryKey: ['doctrine', 'sync-time'], queryFn: doctrineApi.syncTime })
  const sync = useAction('Sync Contracts', doctrineApi.syncContracts, [
    ['doctrine', 'status'], ['doctrine', 'contracts'], ['doctrine', 'sync-time'],
  ])

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 260, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Doctrine</Text>
          </Group>
          <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => navigate('/')}>Tools</Button>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap="md">
          <CharacterList />

          <Divider />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">Contract Sync</Title>
              <Text size="xs" c="dimmed">{dateTime(syncTime?.synced_at)}</Text>
            </Group>
            <Button size="xs" leftSection={<IconRefresh size={14} />} onClick={() => sync.mutate()} loading={sync.isPending}>
              Sync Contracts
            </Button>
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
          <Tabs value={activeTab(location.pathname)} onChange={(v) => v && navigate(v)} mb="md">
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
