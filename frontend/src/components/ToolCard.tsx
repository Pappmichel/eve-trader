import { Badge, Button, Card, Group, Stack, Text, Title } from '@mantine/core'
import { IconArrowRight } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

import { hasAnyToolGrant } from '../toolKeys'

// Each card is only rendered if `tools` contains its own tool_key - `tools` is
// undefined while /api/gate/status hasn't loaded yet (or the gate is disabled,
// in which case the backend already returns every tool_key - see gate.py's
// status handler), so `undefined` means "show everything", matching this
// app's pre-tool-grants behavior for local/dev installs rather than flashing
// an empty page during the initial load. `toolKey` may be a list (a card that
// fronts several grants, e.g. the Character Management hub): shown if the
// session holds any of them.
export function ToolCard({ tools, toolKey, to, title, description, badge }: {
  tools: string[] | undefined
  toolKey: string | readonly string[]
  to: string
  title: string
  description: string
  badge?: string
}) {
  const keys = typeof toolKey === 'string' ? [toolKey] : toolKey
  if (!hasAnyToolGrant(tools, keys)) return null
  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap">
          <Title order={3}>{title}</Title>
          {badge && <Badge color="warn" variant="filled">{badge}</Badge>}
        </Group>
        <Text c="dimmed" size="sm">{description}</Text>
        <Button component={Link} to={to} mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
      </Stack>
    </Card>
  )
}
