import { AppShell, Stack, Title, Text, Tabs, Container, Divider } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ToolHeader } from '../../components/ToolHeader'

const TABS = [
  { path: '/pi', label: 'Profitability' },
  { path: '/pi/planner', label: 'Planner' },
  { path: '/pi/system', label: 'System' },
  { path: '/pi/chains', label: 'Chains' },
  { path: '/pi/templates', label: 'Templates' },
  { path: '/pi/plans', label: 'Plans' },
  { path: '/pi/demand', label: 'Production demand' },
  { path: '/pi/settings', label: 'Settings' },
]

export default function PiLayout() {
  // Mobile navbar starts closed - see DoctrineLayout.tsx.
  const [opened, { toggle }] = useDisclosure(false)
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'xs', sm: 'md' }}>
      <AppShell.Header>
        <ToolHeader title="Planetary Industry" opened={opened} onToggle={toggle} />
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Stack gap="md">
          <div>
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">About</Title>
            <Text size="xs" c="dimmed">
              Which planetary industry is worth building: profitability per product, a colony planner, system
              analysis, chains and importable templates. Planet slots and Command Center level come from your
              characters when you share Skills with Planetary Industry on the Characters page, otherwise from Settings.
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
