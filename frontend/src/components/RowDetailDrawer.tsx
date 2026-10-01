import type { ReactNode } from 'react'
import { Drawer, Group, Stack, Text } from '@mantine/core'

// Side panel for the detail of one table row. Open state is owned by the page
// (usually mirrored in the URL, e.g. ?item=<id>, so back/forward and shared
// links work); this component only renders it.
export function RowDetailDrawer({ opened, onClose, title, children }: {
  opened: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
}) {
  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="md" title={title} padding="md" withCloseButton>
      <Stack gap="md">{children}</Stack>
    </Drawer>
  )
}

// One "label ... value" line; `muted` dims the value (e.g. "–" for no data).
export function DetailRow({ label, value, muted = false }: { label: string; value: ReactNode; muted?: boolean }) {
  return (
    <Group justify="space-between" wrap="nowrap" gap="md">
      <Text size="sm" c="dimmed">{label}</Text>
      <Text size="sm" ff="monospace" c={muted ? 'dimmed' : undefined} ta="right">{value}</Text>
    </Group>
  )
}
