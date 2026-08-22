import { AppShell, Burger, Stack, Title, Text, Button, Group, Tabs, Container, Divider, ActionIcon } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconArrowLeft, IconRefresh, IconTrash } from '@tabler/icons-react'

import { authApi, doctrineApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { dateTime } from '../../format'

const TABS = [
  { path: '/doctrine', label: 'Doctrines' },
  { path: '/doctrine/contracts', label: 'Contracts' },
  { path: '/doctrine/contracts/history', label: 'History' },
  { path: '/doctrine/stockpile', label: 'Stockpile' },
  { path: '/doctrine/shopping-list', label: 'Shopping List' },
  { path: '/doctrine/settings', label: 'Settings' },
]

// A fitting/detail sub-page (path has extra segments beyond a known tab)
// still highlights its parent tab - Tabs.value requires an exact match
// otherwise none of the TABS above would show as active while viewing a
// single doctrine or fitting. History is checked before the plain Contracts
// prefix it would otherwise also match.
function activeTab(pathname: string): string {
  if (pathname.startsWith('/doctrine/contracts/history')) return '/doctrine/contracts/history'
  if (pathname.startsWith('/doctrine/contracts')) return '/doctrine/contracts'
  if (pathname.startsWith('/doctrine/stockpile')) return '/doctrine/stockpile'
  if (pathname.startsWith('/doctrine/shopping-list')) return '/doctrine/shopping-list'
  if (pathname.startsWith('/doctrine/settings')) return '/doctrine/settings'
  return '/doctrine'
}

// Shared by both character groups below - only the query key, ssoRolePrefix,
// and remove-fn differ between "characters authed to read contracts" and
// "characters authed to scan their own inventory" (see doctrine/esi_sync.py's
// own module docstring for why these stay two separate EVE SSO character
// groups rather than one, each with its own narrower scope grant).
function CharacterGroup({ title, queryKey, listFn, ssoRolePrefix, removeFn }: {
  title: string
  queryKey: string[]
  listFn: () => Promise<{ role_key: string; character_id: number; character_name: string }[]>
  ssoRolePrefix: string
  removeFn: (roleKey: string) => Promise<unknown>
}) {
  const { data: characters } = useQuery({ queryKey, queryFn: listFn })
  // Same redirect-based EVE SSO flow Trading's buyer/seller login and
  // Production's own Add Character button use (see TradingLayout.tsx's
  // LoginButton / ProductionLayout.tsx) - navigates the whole page to EVE
  // SSO and back via api/routers/auth.py's /callback route, rather than the
  // old server-side webbrowser.open() + blocking-local-HTTP-server flow
  // (doctrine/actions.py's do_auth_doctrine/auth.py's
  // get_token_interactive_multi). Confirmed real bug live: that old flow
  // tries to bind its own HTTP server on the exact host:port the running
  // backend already listens on - works by accident in local dev, always
  // fails on a real deployment ("Cannot assign requested address").
  const addCharacter = useAction('Login', async () => {
    const { url } = await authApi.start(ssoRolePrefix)
    window.location.href = url
  })
  const removeCharacter = useAction('Remove Character', removeFn, [queryKey])

  return (
    <div>
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
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
    ['doctrine', 'status'], ['doctrine', 'contracts'], ['doctrine', 'contract-history'], ['doctrine', 'sync-time'],
  ])

  const { data: assetSyncTime } = useQuery({ queryKey: ['doctrine', 'asset-sync-time'], queryFn: doctrineApi.assetSyncTime })
  const syncAssets = useAction('Sync Assets', doctrineApi.syncAssets, [
    ['doctrine', 'stockpile'], ['doctrine', 'asset-sync-time'],
  ])

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 260, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
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
          <CharacterGroup title="Contract Characters" queryKey={['doctrine', 'characters']}
            listFn={doctrineApi.characters} ssoRolePrefix="doctrine" removeFn={doctrineApi.removeCharacter} />

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

          <CharacterGroup title="Asset-Scanning Characters" queryKey={['doctrine', 'asset-characters']}
            listFn={doctrineApi.assetCharacters} ssoRolePrefix="doctrine-assets" removeFn={doctrineApi.removeAssetCharacter} />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">Asset Sync</Title>
              <Text size="xs" c="dimmed">{dateTime(assetSyncTime?.synced_at)}</Text>
            </Group>
            <Button size="xs" leftSection={<IconRefresh size={14} />} onClick={() => syncAssets.mutate()} loading={syncAssets.isPending}>
              Sync Assets
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
