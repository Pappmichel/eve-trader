import { AppShell, Button, Container, Divider, Group, Stack, Tabs, Text, Title, Tooltip } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { IconRefresh } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

import { assetManagerApi } from '../../api/client'
import { JobProgress } from '../../components/JobProgress'
import { ToolHeader } from '../../components/ToolHeader'
import { dateTime } from '../../format'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'

const TABS = [
  { path: '/assets', label: 'Inventory' },
  { path: '/assets/locations', label: 'Locations' },
  { path: '/assets/history', label: 'History' },
  { path: '/assets/insights', label: 'Insights' },
]

const KIND_LABELS: Record<string, string> = {
  assets: 'Assets', blueprints: 'Blueprints', market_orders: 'Orders', contracts: 'Contracts', industry_jobs: 'Jobs',
}

const SYNC_RESULT_KEYS = [['asset-manager']]
const SYNC_LABELS = { sync: 'Sync assets' }

function Freshness() {
  const { data } = useQuery({ queryKey: ['asset-manager', 'freshness'], queryFn: assetManagerApi.freshness })
  if (!data) return null
  if (data.owners.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        No character shares assets yet. Share Assets on the Characters page with this tool or any tool you use.
      </Text>
    )
  }
  return (
    <Stack gap={6}>
      {data.owners.map((o) => (
        <div key={`${o.owner_type}:${o.owner_id}`}>
          <Text size="xs" fw={600}>{o.owner_name}</Text>
          {Object.entries(o.kinds).map(([kind, f]) => (
            <Group key={kind} justify="space-between" gap={4} wrap="nowrap">
              <Text size="xs" c="dimmed">{KIND_LABELS[kind] ?? kind}</Text>
              <Tooltip label={f.last_error ?? 'Last successful sync'} disabled={!f.last_error && !f.last_success_at} withArrow>
                <Text size="xs" c={f.last_error ? 'red' : 'dimmed'}>
                  {f.last_success_at ? dateTime(f.last_success_at) : 'never'}
                </Text>
              </Tooltip>
            </Group>
          ))}
        </div>
      ))}
    </Stack>
  )
}

export default function AssetManagerLayout() {
  const [opened, { toggle }] = useDisclosure(false)
  const location = useLocation()
  const navigate = useNavigate()
  const syncJob = useBackgroundJob({
    queryKey: ['asset-manager', 'sync'],
    fetchStatus: assetManagerApi.syncStatus,
    resultKeys: SYNC_RESULT_KEYS,
    labels: SYNC_LABELS,
    defaultLabel: 'Sync assets',
    pollIntervalMs: 1500,
  })
  const syncStart = useBackgroundJobStart(syncJob, () => assetManagerApi.sync())
  const syncRunning = syncJob.runningStatus || syncStart.isPending
  const tab = TABS.find((t) => t.path !== '/assets' && location.pathname.startsWith(t.path))?.path ?? '/assets'

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <ToolHeader title="Asset Manager" opened={opened} onToggle={toggle} showJump />
      </AppShell.Header>

      <AppShell.Navbar p="md" style={{ overflowY: 'auto' }}>
        <Stack gap="md">
          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Data</Title>
            <Text size="xs" c="dimmed" mb="xs">
              Every character and corporation that shares assets with a tool you hold. Reads the last
              sync - EVE has no API to move items.
            </Text>
            <Tooltip label="Refreshes assets, blueprints, orders, contracts and jobs of every owner shown here." multiline w={260}>
              <Button size="xs" leftSection={<IconRefresh size={14} />} loading={syncStart.isPending}
                variant={syncRunning ? 'light' : undefined} onClick={() => syncStart.mutate()}>
                Sync assets
              </Button>
            </Tooltip>
            {syncRunning && (
              <div style={{ marginTop: 4 }}>
                <JobProgress label={syncJob.formatProgress(syncJob.status?.progress, syncJob.jobName)} progress={syncJob.status?.progress} />
              </div>
            )}
          </div>
          <Divider />
          <Freshness />
          <Divider />
          <Text size="xs" c="dimmed">
            Unofficial third-party tool, not affiliated with or endorsed by CCP hf. EVE, EVE Online, CCP, and
            all related logos/trademarks are property of CCP hf.
          </Text>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        <Container size="xl" px={0}>
          <Tabs value={tab} onChange={(v) => v && navigate(v)} mb="md">
            <Tabs.List>
              {TABS.map((t) => <Tabs.Tab key={t.path} value={t.path}>{t.label}</Tabs.Tab>)}
            </Tabs.List>
          </Tabs>
          <Outlet />
        </Container>
      </AppShell.Main>
    </AppShell>
  )
}
