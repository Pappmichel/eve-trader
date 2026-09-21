import { AppShell, Burger, Stack, Title, Text, Button, Group, Tabs, Container, Divider, ActionIcon, Tooltip } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { modals } from '@mantine/modals'
import { spotlight } from '@mantine/spotlight'
import { IconArrowLeft, IconRefresh, IconSearch, IconTrash } from '@tabler/icons-react'

import { doctrineApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'
import { useRoleCharacters, type RoleCharacter } from '../../hooks/useRoleCharacters'
import { ActionTierIcon, TIER_COPY } from '../../components/ActionTierIcon'
import { dateTime } from '../../format'

const TABS = [
  { path: '/doctrine', label: 'Overview' },
  { path: '/doctrine/doctrines', label: 'Doctrines' },
  { path: '/doctrine/contracts', label: 'Contracts' },
  { path: '/doctrine/contracts/history', label: 'History' },
  { path: '/doctrine/stockpile', label: 'Stockpile' },
  { path: '/doctrine/shopping-list', label: 'Shopping List' },
  { path: '/doctrine/settings', label: 'Settings' },
]

const SYNC_RESULT_KEYS: string[][] = [
  ['doctrine', 'status'], ['doctrine', 'contracts'], ['doctrine', 'contract-history'], ['doctrine', 'sync-time'],
]
const SYNC_LABELS = { sync_contracts: 'Refresh what I need' }

// A fitting/detail sub-page (path has extra segments beyond a known tab)
// still highlights its parent tab - Tabs.value requires an exact match
// otherwise none of the TABS above would show as active while viewing a
// single doctrine or fitting. History is checked before the plain Contracts
// prefix it would otherwise also match. The bare "/doctrine" root is its own
// Overview tab now, so only an exact match highlights it - everything else
// (including a single doctrine/fitting sub-page) falls through to Doctrines.
function activeTab(pathname: string): string {
  if (pathname === '/doctrine') return '/doctrine'
  if (pathname.startsWith('/doctrine/contracts/history')) return '/doctrine/contracts/history'
  if (pathname.startsWith('/doctrine/contracts')) return '/doctrine/contracts'
  if (pathname.startsWith('/doctrine/stockpile')) return '/doctrine/stockpile'
  if (pathname.startsWith('/doctrine/shopping-list')) return '/doctrine/shopping-list'
  if (pathname.startsWith('/doctrine/settings')) return '/doctrine/settings'
  return '/doctrine/doctrines'
}

function CharacterGroup({ title, queryKey, listFn, removeFn, legacyPrefix }: {
  title: string
  queryKey: string[]
  listFn: () => Promise<RoleCharacter[]>
  removeFn: (roleKey: string) => Promise<unknown>
  legacyPrefix: string
}) {
  const { characters, removeCharacter, isRemoving } = useRoleCharacters(
    queryKey, listFn, removeFn,
  )

  return (
    <div>
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
      {characters.length === 0 && (
        <Text size="xs" c="dimmed">Share on the Characters page.</Text>
      )}
      <Stack gap="xs">
        {characters.map((c) => (
          <Group key={c.role_key} justify="space-between" wrap="nowrap">
            <Text size="sm">{c.character_name}</Text>
            {c.role_key.startsWith(`${legacyPrefix}:`) ? (
              <ActionIcon size="sm" variant="subtle" color="danger"
                onClick={() => modals.openConfirmModal({
                  title: 'Remove character',
                  children: <Text size="sm">Remove {c.character_name} from {title}? This drops this tool&apos;s token key. Sharing stays on the Characters page.</Text>,
                  labels: { confirm: 'Remove', cancel: 'Cancel' },
                  confirmProps: { color: 'danger' },
                  onConfirm: () => removeCharacter(c.role_key),
                })}
                loading={isRemoving(c.role_key)}>
                <IconTrash size={14} />
              </ActionIcon>
            ) : (
              // Listed here because it shares the relevant data kind with
              // Doctrine, not because it holds a dedicated legacy token -
              // its esi:<id> key may be shared with other tools too, so
              // this sidebar cannot safely delete it. Unshare on the
              // Characters page instead.
              <Tooltip label="Shared via the Characters page - unshare there, not here" multiline w={220}>
                <ActionIcon size="sm" variant="subtle" color="gray" disabled>
                  <IconTrash size={14} />
                </ActionIcon>
              </Tooltip>
            )}
          </Group>
        ))}
      </Stack>
    </div>
  )
}

export default function DoctrineLayout() {
  // Starts closed, not open - only `collapsed.mobile` (below) is driven by
  // this state (desktop's navbar visibility is unaffected either way), but
  // starting open meant the drawer covered the entire page on first mobile
  // load, including any table underneath, blocking touch/scroll input to it
  // until the user found and tapped the burger - confirmed real bug across
  // every tool layout (see the other five `useDisclosure` call sites).
  const [opened, { toggle }] = useDisclosure(false)
  const location = useLocation()
  const navigate = useNavigate()

  const { data: syncTime } = useQuery({ queryKey: ['doctrine', 'sync-time'], queryFn: doctrineApi.syncTime })
  const syncJob = useBackgroundJob({
    queryKey: ['doctrine', 'pipeline', 'sync'],
    fetchStatus: doctrineApi.syncContractsStatus,
    resultKeys: SYNC_RESULT_KEYS,
    labels: SYNC_LABELS,
    defaultLabel: 'Refresh what I need',
    // Same reasoning as Admin's SDE refresh - contract sync now reports
    // per-item batch progress, so a tighter poll shows real movement
    // instead of just "Running…" for its (typically short) duration.
    pollIntervalMs: 1000,
  })
  const syncStart = useBackgroundJobStart(syncJob, () => doctrineApi.syncContracts())
  const syncRunning = syncJob.runningStatus || syncStart.isPending

  const { data: assetSyncTime } = useQuery({ queryKey: ['doctrine', 'asset-sync-time'], queryFn: doctrineApi.assetSyncTime })
  const syncAssets = useAction('Sync Assets', doctrineApi.syncAssets, [
    ['doctrine', 'stockpile'], ['doctrine', 'asset-sync-time'],
  ], { tier: 'live', effect: 'Refreshes the ESI asset snapshots shared with Doctrine.' })

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 260, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Text fw={700} tt="uppercase" lts={1}>EVE Trader — Doctrine</Text>
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
          <CharacterGroup title="Contract Characters" queryKey={['doctrine', 'characters']}
            listFn={doctrineApi.characters} removeFn={doctrineApi.removeCharacter} legacyPrefix="doctrine" />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">Contract Sync</Title>
              <Text size="xs" c="dimmed">{dateTime(syncTime?.synced_at)}</Text>
            </Group>
            <Tooltip
              label={`Refreshes the contract snapshots this tool is shared (runs as a background job with progress). ${TIER_COPY.live}`}
              multiline w={280}
            >
              <Button size="xs" leftSection={<IconRefresh size={14} />} rightSection={<ActionTierIcon tier="live" />}
                variant={syncRunning ? 'light' : undefined}
                onClick={() => syncStart.mutate()}>
                Refresh what I need
              </Button>
            </Tooltip>
            {syncRunning && (
              <Text size="xs" c="dimmed" mt={4}>
                {syncJob.formatProgress(syncJob.status?.progress, syncJob.jobName)}
              </Text>
            )}
          </div>

          <Divider />

          <CharacterGroup title="Asset-Scanning Characters" queryKey={['doctrine', 'asset-characters']}
            listFn={doctrineApi.assetCharacters} removeFn={doctrineApi.removeAssetCharacter} legacyPrefix="doctrine-assets" />

          <div>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Title order={6} c="dimmed" tt="uppercase">Asset Sync</Title>
              <Text size="xs" c="dimmed">{dateTime(assetSyncTime?.synced_at)}</Text>
            </Group>
            <Tooltip label={syncAssets.tooltip} disabled={!syncAssets.tooltip} multiline w={280}>
              <Button size="xs" leftSection={<IconRefresh size={14} />} rightSection={syncAssets.tierIcon}
                onClick={() => syncAssets.mutate()} loading={syncAssets.isPending}>
                Refresh what I need
              </Button>
            </Tooltip>
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
