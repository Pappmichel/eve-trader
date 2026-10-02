import { useState } from 'react'
import {
  Badge, Button, Checkbox, Container, Group, Loader, Modal, Select, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import { notify } from '../../../notify'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'

import { ApiError, charNotificationsApi } from '../../../api/client'
import type { NotificationItem } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { dateTime } from '../../../format'
import { useCharacterSync } from '../../../hooks/useCharacterSync'
import { isStale, useSyncWhenStale } from '../../../hooks/useSyncWhenStale'

const PAGE = 50
const DETAIL_COLUMNS: ColumnDef<{ key: string; value: string }, any>[] = [
  { header: 'Field', accessorKey: 'key', size: 200 },
  { header: 'Value', accessorKey: 'value', size: 320 },
]
const KEY = ['char-notifications']

function NotificationDetailModal({ item, onClose }: { item: NotificationItem | null; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: [...KEY, 'detail', item?.character_id, item?.notification_id],
    queryFn: () => charNotificationsApi.detail(item!.character_id, item!.notification_id),
    enabled: item !== null, retry: false,
  })
  return (
    <Modal opened={item !== null} onClose={onClose} title={item?.summary ?? 'Notification'} size="lg">
      {isLoading && <Loader color="accent" />}
      {error && <Text c="red" size="sm">{error instanceof ApiError ? error.message : 'Could not load this notification.'}</Text>}
      {data && (
        <Stack gap="xs">
          <Text size="xs" c="dimmed">{data.type} · {dateTime(data.sent_at)}</Text>
          {data.details.length === 0 ? (
            <Text size="sm" c="dimmed">
              {data.parsed ? 'This notification carries no details.' : 'The details of this notification could not be read.'}
            </Text>
          ) : (
            <DataTable
              data={data.details} columns={DETAIL_COLUMNS} maxHeight={360}
              getRowId={(d) => d.key} exportFilename="notification-details"
            />
          )}
        </Stack>
      )}
    </Modal>
  )
}

export default function NotificationsPage() {
  const qc = useQueryClient()
  const [characterId, setCharacterId] = useState<string | null>(null)
  const [category, setCategory] = useState<string | null>(null)
  const [type, setType] = useState<string | null>(null)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const [opened, setOpened] = useState<NotificationItem | null>(null)

  const query = { characterId: characterId ? Number(characterId) : null, category, type, unreadOnly, limit: PAGE, offset }
  const { data, isLoading, error } = useQuery({
    queryKey: [...KEY, 'list', query], queryFn: () => charNotificationsApi.list(query), retry: false,
  })

  const refresh = useCharacterSync(
    'Notifications refresh', charNotificationsApi.sync, [KEY],
    'Fetches the current notification list from ESI for every character shared with Notifications.',
  )
  // Notifications are no longer refreshed in the background (esi_data
  // `on_demand`), so opening the page with an old list syncs it once.
  useSyncWhenStale({
    ready: !!data,
    stale: isStale((data?.characters ?? []).map((c) => c.synced_at)),
    sync: charNotificationsApi.sync,
    invalidateKeys: [KEY],
  })
  const setRead = useMutation({
    mutationFn: (v: { item: NotificationItem; read: boolean }) =>
      charNotificationsApi.setRead(v.item.character_id, [v.item.notification_id], v.read),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
    onError: (e) => notify({
      title: 'Could not change the flag', message: e instanceof ApiError ? e.message : 'Request failed.', color: 'danger',
    }),
  })

  // Any filter change goes back to the first page.
  const filter = (apply: () => void) => { apply(); setOffset(0) }
  const items = data?.items ?? []
  const notificationColumns: ColumnDef<NotificationItem, any>[] = [
    { header: 'When', accessorKey: 'sent_at', size: 170, cell: (i) => dateTime(i.getValue() as string) },
    { header: 'Character', accessorKey: 'character_name', size: 160, meta: { filterable: true } },
    {
      header: 'Notification', accessorKey: 'summary', size: 420,
      cell: (i) => {
        const n = i.row.original
        return (
          <Group gap="xs" wrap="nowrap">
            {!n.read && <Badge size="xs" color="accent">new</Badge>}
            <Button variant="subtle" size="compact-sm" onClick={() => setOpened(n)}
              styles={{ label: { fontWeight: n.read ? 400 : 700 } }}>
              {n.summary}
            </Button>
          </Group>
        )
      },
    },
    {
      header: '', id: 'actions', size: 130, enableSorting: false, enableResizing: false,
      cell: (i) => {
        const n = i.row.original
        return n.read_in_game ? (
          <Text size="xs" c="dimmed">read in game</Text>
        ) : (
          <Button size="compact-xs" variant="default" loading={setRead.isPending}
            onClick={() => setRead.mutate({ item: n, read: !n.read })}>
            {n.read ? 'Mark unread' : 'Mark read'}
          </Button>
        )
      },
    },
  ]
  const total = data?.total ?? 0
  const neverSynced = (data?.characters ?? []).filter((c) => c.synced_at === null)

  return (
    <Container size="xl" py="xl">
      <Group justify="space-between" align="flex-start" mb="md">
        <div>
          <Title order={1}>Notifications</Title>
          <Text size="sm" c="dimmed">
            In-game notifications of your characters. ESI has no way to mark them read, so “read” here is this
            app&apos;s own flag; a notification you already read in the game counts as read.
          </Text>
        </div>
        <Group gap="xs">
          <Tooltip label={refresh.tooltip} disabled={!refresh.tooltip} multiline w={280}>
            <Button size="xs" variant="default" leftSection={refresh.tierIcon}
              onClick={() => refresh.mutate()} loading={refresh.isPending}>
              Refresh
            </Button>
          </Tooltip>
          <Button component={Link} to="/character-management" variant="subtle" leftSection={<IconArrowLeft size={14} />}>
            Back
          </Button>
        </Group>
      </Group>

      <Text size="xs" c="dimmed" mb="md">
        Which characters appear is decided on the{' '}
        <Text component={Link} to="/character-management/characters" span c="accent" td="underline">Characters page</Text>.
      </Text>

      {isLoading ? <Loader color="accent" /> : error || !data ? (
        <Text c="red">{error instanceof ApiError ? error.message : 'Could not load notifications.'}</Text>
      ) : data.characters.length === 0 ? (
        <Text c="dimmed">No character is shared with Notifications. Tick it for a character on the Characters page.</Text>
      ) : (
        <Stack gap="sm">
          <Group align="flex-end">
            <Select label="Character" placeholder="All characters" clearable value={characterId}
              onChange={(v) => filter(() => setCharacterId(v))}
              data={data.characters.map((c) => ({ value: String(c.character_id), label: c.character_name }))} />
            <Select label="Category" placeholder="All" clearable value={category}
              onChange={(v) => filter(() => { setCategory(v); setType(null) })}
              data={data.categories.map((c) => ({ value: c.category, label: `${c.label} (${c.count})` }))} />
            <Select label="Type" placeholder="All" clearable searchable value={type}
              onChange={(v) => filter(() => setType(v))}
              data={data.types.map((t) => ({ value: t.type, label: `${t.label} (${t.count})` }))} />
            <Checkbox label={`Unread only (${data.unread_total})`} checked={unreadOnly}
              onChange={(e) => { const checked = e.currentTarget.checked; filter(() => setUnreadOnly(checked)) }} />
          </Group>

          {neverSynced.length > 0 && (
            <Text size="xs" c="dimmed">
              Not synced yet: {neverSynced.map((c) => c.character_name).join(', ')}. Press Refresh.
            </Text>
          )}

          {items.length === 0 ? <Text c="dimmed">No notifications match.</Text> : (
            <DataTable
              data={items} columns={notificationColumns} maxHeight={600}
              getRowId={(n) => `${n.character_id}-${n.notification_id}`} exportFilename="notifications"
            />
          )}

          <Group justify="space-between">
            <Text size="xs" c="dimmed">{total === 0 ? '0' : `${offset + 1}–${Math.min(offset + PAGE, total)}`} of {total}</Text>
            <Group gap="xs">
              <Button size="xs" variant="default" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button size="xs" variant="default" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </Group>
          </Group>
        </Stack>
      )}
      <NotificationDetailModal item={opened} onClose={() => setOpened(null)} />
    </Container>
  )
}
