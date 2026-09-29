import { AppShell, Stack, Title, Text, Tabs, Container, Divider } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ToolHeader } from '../../components/ToolHeader'

const TABS = [
  { path: '/sorting', label: 'Overview' },
  { path: '/sorting/settings', label: 'Settings' },
]

export default function SortingLayout() {
  // See DoctrineLayout.tsx's own comment on this same line - starting the
  // mobile navbar drawer open covered the entire page (including any table)
  // on first mobile load, blocking touch/scroll until the burger was tapped.
  const [opened, { toggle }] = useDisclosure(false)
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <ToolHeader title="Sorting" opened={opened} onToggle={toggle} />
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap="md">
          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Intake</Title>
            <Text size="xs" c="dimmed">
              Jita imports for every tool land in personal hangars and shared corp divisions first.
              EVE has no API to move an item between hangar divisions, so sorting is still manual.
              This tool only shows what is sitting in the configured intake and which demand pot still wants it.
            </Text>
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
