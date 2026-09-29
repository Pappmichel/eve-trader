import { AppShell, Stack, Title, Text, Button, Group, Tabs, Container, Divider, Tooltip } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { IconRefresh } from '@tabler/icons-react'

import { moduleReprocessingApi } from '../../api/client'
import { useAction, warnIfPricedViaFallback } from '../../hooks/useAction'
import { dateTime } from '../../format'
import { ToolHeader } from '../../components/ToolHeader'

const TABS = [
  { path: '/modules', label: 'Overview' },
  { path: '/modules/shortlist', label: 'Shortlist' },
  { path: '/modules/shopping-list', label: 'Shopping List' },
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
  // One button does everything: scans the full candidate universe against a
  // Goonmetrics current-price snapshot, auto-adds whatever clears the
  // configured margin/profit threshold in Settings, then live-prices the
  // whole (now possibly-grown) shortlist via ESI - no separate "Discover,
  // then manually pick candidates" step (confirmed with the user: reviewing
  // thousands of candidates by hand doesn't scale, so this now mirrors
  // Station Trading's own single-button Refresh Shortlist).
  const refresh = useAction('Refresh Shortlist', moduleReprocessingApi.refreshShortlist, [
    ['module_reprocessing', 'shortlist', 'snapshot'], ['module_reprocessing', 'shortlist', 'items'],
    ['module_reprocessing', 'esi-sync-time'],
  ], { tier: 'live', effect: 'Scans the full candidate universe against Goonmetrics, auto-adds anything profitable, then reprices the whole shortlist live via ESI (with a Goonmetrics fallback) - can take up to a minute.' })

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <ToolHeader title="Module Reprocessing" opened={opened} onToggle={toggle} />
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
              <Tooltip label={refresh.tooltip} disabled={!refresh.tooltip} multiline w={280}>
                <Button size="xs" leftSection={<IconRefresh size={14} />} rightSection={refresh.tierIcon}
                  onClick={() => refresh.mutate(undefined, { onSuccess: warnIfPricedViaFallback })} loading={refresh.isPending}>
                  Refresh Shortlist
                </Button>
              </Tooltip>
              <Text size="xs" c="dimmed">
                Scans every T1/Meta module and drone against Goonmetrics, auto-adds anything clearing the
                margin/profit threshold in Settings, then reprices the whole shortlist live via ESI.
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
