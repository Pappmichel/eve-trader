import { Alert, Button, Group, Stack, Switch, Text } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import { ApiError, charMailApi } from '../../../api/client'
import type { MailArchiveRow } from '../../../api/types'
import { dateTime } from '../../../format'
import { ARCHIVE_KEY, backfillLabel } from './mailUtils'

// Mail is read live and nothing is stored - unless a character's "Archive
// mail" switch is on here. Turning it off deletes that character's archive,
// which is the one and only way archived mail is ever removed.
function ArchiveRow({ row }: { row: MailArchiveRow }) {
  const queryClient = useQueryClient()
  const toggle = useMutation({
    mutationFn: (args: { enabled: boolean; confirm?: boolean }) =>
      charMailApi.setArchive({ character_id: row.character_id, enabled: args.enabled, confirm_delete: args.confirm }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['char-mail'] })
    },
    onError: (err: unknown) => {
      notifications.show({
        title: 'Mail archive', color: 'danger',
        message: err instanceof ApiError ? err.message : String(err),
      })
    },
  })

  const setEnabled = (enabled: boolean) => {
    if (enabled || row.counts.headers === 0) {
      toggle.mutate({ enabled })
      return
    }
    modals.openConfirmModal({
      title: `Delete ${row.character_name}'s archived mail?`,
      children: (
        <Text size="sm">
          Turning the archive off deletes {row.counts.headers} archived mail(s) of {row.character_name} from
          this app&apos;s database. The mail stays in the game; you can archive it again later, which
          re-downloads it.
        </Text>
      ),
      labels: { confirm: 'Delete archive', cancel: 'Keep' },
      confirmProps: { color: 'danger' },
      onConfirm: () => toggle.mutate({ enabled: false, confirm: true }),
    })
  }

  const blocked = !row.archive_enabled && (!row.shared || row.reauth_needed)
  return (
    <Stack gap={2} py="xs">
      <Group justify="space-between" wrap="nowrap">
        <div>
          <Text fw={600}>{row.character_name}</Text>
          <Text size="xs" c="dimmed">{backfillLabel(row.backfill_state, row.counts.headers, row.counts.bodies)}</Text>
        </div>
        <Switch
          aria-label={`Archive mail for ${row.character_name}`}
          label="Archive mail"
          checked={row.archive_enabled}
          disabled={blocked || toggle.isPending}
          onChange={(e) => setEnabled(e.currentTarget.checked)}
        />
      </Group>
      {blocked && (
        <Text size="xs" c="warn">
          {!row.shared
            ? 'Not shared with Mail - tick it on the Characters page first.'
            : 'Needs a re-authorize with the mail scope (Characters page).'}
        </Text>
      )}
      {row.archive_enabled && !row.shared && (
        <Text size="xs" c="warn">Not shared with Mail any more: the archive is kept but hidden until it is shared again.</Text>
      )}
      {row.error && <Text size="xs" c="danger">{row.error}</Text>}
      {row.archive_enabled && (
        <Text size="xs" c="dimmed">Last refreshed {row.last_refresh_at ? dateTime(row.last_refresh_at) : 'never'}</Text>
      )}
    </Stack>
  )
}

export function MailSettings() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ARCHIVE_KEY, queryFn: charMailApi.archive,
    refetchInterval: (query) =>
      query.state.data?.characters.some((c) => c.backfill_state === 'running') ? 2000 : false,
  })
  const refresh = useMutation({
    mutationFn: () => charMailApi.refreshArchive(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['char-mail'] }),
  })
  const rows = data?.characters ?? []
  const anyArchived = rows.some((r) => r.archive_enabled)
  return (
    <Stack gap="sm">
      <Alert color="info" variant="light" title="Live by default">
        Mail is read live from ESI and nothing about it is stored on this server. Switch on
        &quot;Archive mail&quot; for a character to keep a searchable copy in this app&apos;s database
        (headers and text, no size limit). Switching it off deletes that copy.
      </Alert>
      {isLoading && <Text size="sm" c="dimmed">Loading…</Text>}
      {rows.length === 0 && !isLoading && <Text size="sm" c="dimmed">No ESI characters registered yet.</Text>}
      {rows.map((r) => <ArchiveRow key={r.character_id} row={r} />)}
      {anyArchived && (
        <Group>
          <Button size="xs" variant="default" loading={refresh.isPending} onClick={() => refresh.mutate()}>
            Refresh archive
          </Button>
          <Text size="xs" c="dimmed">Also resumes an interrupted download.</Text>
        </Group>
      )}
    </Stack>
  )
}
