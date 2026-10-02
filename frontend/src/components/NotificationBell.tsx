import { ActionIcon, Badge, Button, Group, Indicator, Popover, ScrollArea, Stack, Text } from '@mantine/core'
import { IconBell } from '@tabler/icons-react'

import { clearNotifications, markAllNotificationsRead, useNotificationHistory } from '../notify'
import { relativeTime } from '../format'

// Bell with the last notifications of this browser tab (toasts vanish after a
// few seconds and are easy to miss). Stored in sessionStorage by notify.ts.
export function NotificationBell() {
  const { entries, unread } = useNotificationHistory()
  return (
    <Popover width={340} position="bottom-end" shadow="md" withArrow onOpen={markAllNotificationsRead}>
      <Popover.Target>
        <Indicator label={unread > 9 ? '9+' : unread} size={16} disabled={unread === 0} color="danger" offset={4}>
          <ActionIcon variant="subtle" size="lg" aria-label={unread > 0 ? `Notifications (${unread} unread)` : 'Notifications'}>
            <IconBell size={18} />
          </ActionIcon>
        </Indicator>
      </Popover.Target>
      <Popover.Dropdown p="xs">
        <Group justify="space-between" mb="xs">
          <Text size="sm" fw={700}>Notifications</Text>
          {entries.length > 0 && (
            <Button size="compact-xs" variant="subtle" onClick={clearNotifications}>Clear</Button>
          )}
        </Group>
        {entries.length === 0 ? (
          <Text size="sm" c="dimmed">Nothing yet in this session.</Text>
        ) : (
          <ScrollArea.Autosize mah={360}>
            <Stack gap="xs">
              {entries.map((e) => (
                <div key={e.id}>
                  <Group gap={6} wrap="nowrap">
                    <Badge size="xs" color={e.color} variant="dot" />
                    <Text size="sm" fw={600} style={{ flex: 1 }} truncate>{e.title || 'Notice'}</Text>
                    <Text size="xs" c="dimmed">{relativeTime(e.at)}</Text>
                  </Group>
                  {e.message && <Text size="xs" c="dimmed" lineClamp={3} style={{ whiteSpace: 'pre-line' }}>{e.message}</Text>}
                </div>
              ))}
            </Stack>
          </ScrollArea.Autosize>
        )}
      </Popover.Dropdown>
    </Popover>
  )
}
