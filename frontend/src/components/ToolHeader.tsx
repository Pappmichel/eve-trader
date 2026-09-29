import { ActionIcon, Burger, Button, Group, Text } from '@mantine/core'
import { spotlight } from '@mantine/spotlight'
import { IconArrowLeft, IconSearch } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'

// The header row every tool layout (Trading, Production, Doctrine, ...) puts in
// its AppShell.Header. The shell's header height is fixed (56px), so this row
// must never wrap: with the text buttons a phone-width row broke into two
// lines and the second one spilled out of the header, half hidden behind the
// open navbar. Below `sm` the row is one line: burger, the short tool name
// (the "EVE Trader" prefix is desktop-only) and icon-only buttons with a
// proper tap target.
export function ToolHeader({ title, opened, onToggle, showJump = false }: {
  title: string
  opened: boolean
  onToggle: () => void
  showJump?: boolean
}) {
  const navigate = useNavigate()
  return (
    <Group h="100%" px={{ base: 'xs', sm: 'md' }} justify="space-between" wrap="nowrap">
      <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
        <Burger opened={opened} onClick={onToggle} hiddenFrom="sm" size="md" aria-label="Toggle navigation" />
        <Text fw={700} tt="uppercase" lts={1} truncate>
          <Text span inherit visibleFrom="sm">EVE Trader — </Text>{title}
        </Text>
      </Group>
      <Group gap={4} wrap="nowrap">
        {showJump && (
          <>
            <Button visibleFrom="sm" variant="subtle" size="xs" leftSection={<IconSearch size={14} />}
              onClick={() => spotlight.open()}>
              Jump to... (⌘K)
            </Button>
            <ActionIcon hiddenFrom="sm" variant="subtle" size="lg" aria-label="Jump to a page"
              onClick={() => spotlight.open()}>
              <IconSearch size={18} />
            </ActionIcon>
          </>
        )}
        <Button visibleFrom="sm" variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />}
          onClick={() => navigate('/')}>
          Tools
        </Button>
        <ActionIcon hiddenFrom="sm" variant="subtle" size="lg" aria-label="Back to tools" onClick={() => navigate('/')}>
          <IconArrowLeft size={18} />
        </ActionIcon>
      </Group>
    </Group>
  )
}
